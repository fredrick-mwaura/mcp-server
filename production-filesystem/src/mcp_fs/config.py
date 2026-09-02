"""Configuration for the filesystem server (Phase 1).

LESSON: "configuration is security." A file server is only as safe as the
boundaries you configure - which roots it may touch, which mode it runs in.
We read everything from the environment so that:
  - opencode can pass per-server settings (its `environment` map in
    opencode.json),
  - Docker can inject settings (no secrets in the image),
  - tests can override anything without touching code.

Phase 1 is deliberately small: two settings (roots + mode) plus a couple of
operational knobs. Later phases add timeouts, limits and log-level tuning
here, in one obvious place.

We use pydantic (already a dependency of the MCP SDK) purely for *validation*:
`Literal["read-only", "read-write"]` gives us a friendly error the moment
someone typos an env value, instead of a silent misconfiguration.
"""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, Field

# Every env var is namespaced MCP_FS_* so it can never collide with the Lesson
# 1 terminal server or a future sibling project (see PRODUCTION_PLAN §10).
_TRANSPORT = os.environ.get("MCP_FS_TRANSPORT", "stdio")


class Settings(BaseModel):
    """Validated, immutable server settings. Created once at startup."""

    # The ONLY directories the server may ever touch. A path that resolves
    # outside every root is rejected BEFORE any filesystem call is made.
    roots: list[str] = Field(
        default_factory=lambda: [os.getcwd()],
        description="Allowed workspace roots. Unset -> the launch directory.",
    )

    # Least privilege: read-only by default. Phase 3 turns on write tools and
    # this field becomes the master switch for them.
    mode: Literal["read-only", "read-write"] = "read-only"

    # Phase 1 ships only stdio. streamable-http arrives in Phase 4 but the
    # field already exists so nothing downstream needs to change shape.
    transport: Literal["stdio", "streamable-http"] = "stdio"

    # Operational knobs (not security - just ergonomics).
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # Phase 2: Token-budget limits. These protect the model's context window
    # and your API bill. read_file defaults to returning at most this many
    # lines per call; the caller can override with offset/limit but never
    # exceed max_read_bytes.
    max_read_lines: int = Field(
        default=200,
        description="Default max lines returned by read_file. Caller can "
        "request fewer via limit, but never more than this.",
    )
    max_read_bytes: int = Field(
        default=1_000_000,   # 1 MB
        description="Hard cap on file size for full reads. Files larger than "
        "this require offset/limit. Prevents 40 GB log dumps.",
    )

    # Phase 6: Remote HTTP transport knobs. Frictionless & zero-auth:
    # binds to host/port (defaults to localhost:8000).
    host: str = Field(
        default="127.0.0.1",
        description="Host interface to bind HTTP transport to (default: 127.0.0.1).",
    )
    port: int = Field(
        default=8000,
        description="Port for HTTP transport (default: 8000).",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a Settings object from the process environment.

        Raises pydantic.ValidationError (a ValueError) if an env value is
        invalid, so startup can print a human-readable error and exit.
        """
        roots_raw = os.environ.get("MCP_FS_ROOTS")
        if roots_raw is None or not roots_raw.strip():
            roots = [os.getcwd()]  # friendly default: the launch directory
        else:
            roots = _split_roots(roots_raw)

        return cls(
            roots=roots,
            mode=os.environ.get("MCP_FS_MODE", "read-only"),
            transport=os.environ.get("MCP_FS_TRANSPORT", "stdio"),
            log_level=os.environ.get("MCP_FS_LOG_LEVEL", "INFO"),
            log_format=os.environ.get("MCP_FS_LOG_FORMAT", "json"),
            max_read_lines=int(os.environ.get("MCP_FS_MAX_READ_LINES", "200")),
            max_read_bytes=int(os.environ.get("MCP_FS_MAX_READ_BYTES", "1000000")),
            host=os.environ.get("MCP_FS_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_FS_PORT", "8000")),
        )


def _split_roots(raw: str) -> list[str]:
    """Parse MCP_FS_ROOTS, accepting either a JSON array or comma-separated.

    Comma-separated is friendlier for a shell export:
        export MCP_FS_ROOTS=/data/a,/data/b
    JSON is friendlier for opencode.json's "environment" map:
        "env": { "MCP_FS_ROOTS": "[\"/data/a\", \"/data/b\"]" }
    """
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"MCP_FS_ROOTS looks like JSON but does not parse: {exc}"
            ) from exc
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) for item in parsed
        ):
            raise ValueError("MCP_FS_ROOTS JSON must be an array of strings.")
        return parsed

    return [item.strip() for item in raw.split(",") if item.strip()]
