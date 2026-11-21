"""Validated, fail-closed configuration for the filesystem server."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError(f"{name} JSON must be an array of strings")
        return parsed
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class Settings(BaseModel):
    """Immutable server settings, validated as one security policy."""

    model_config = ConfigDict(frozen=True)
    roots: list[str] = Field(default_factory=lambda: [os.getcwd()], min_length=1)
    mode: Literal["read-only", "read-write"] = "read-only"
    transport: Literal["stdio", "streamable-http"] = "stdio"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    max_read_lines: int = Field(default=200, ge=1, le=10_000)
    max_read_bytes: int = Field(default=1_000_000, ge=1)
    max_output_bytes: int = Field(default=1_000_000, ge=1)
    max_write_bytes: int = Field(default=5_000_000, ge=1)
    max_http_body_bytes: int = Field(default=4_000_000, ge=1)
    max_test_timeout: float = Field(default=300.0, ge=1, le=3_600)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    auth_mode: Literal["auto", "bearer", "none"] = "auto"
    auth_token: SecretStr | None = Field(default=None, repr=False)
    allow_unsafe_remote: bool = False
    allow_exec: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)

    @property
    def effective_auth_mode(self) -> Literal["bearer", "none"]:
        if self.transport == "stdio":
            return "none"
        if self.auth_mode == "auto":
            return "none" if _is_loopback(self.host) else "bearer"
        return self.auth_mode

    @model_validator(mode="after")
    def validate_security_policy(self) -> "Settings":
        if self.transport == "streamable-http" and not _is_loopback(self.host):
            if self.effective_auth_mode == "none" and not self.allow_unsafe_remote:
                raise ValueError(
                    "non-loopback HTTP without authentication requires "
                    "MCP_FS_ALLOW_UNSAFE_REMOTE=true"
                )
            if self.allow_exec and self.effective_auth_mode != "bearer":
                raise ValueError("remote execution requires bearer authentication")
        if self.effective_auth_mode == "bearer":
            token = self.auth_token.get_secret_value() if self.auth_token else ""
            if len(token) < 16:
                raise ValueError("bearer authentication requires a token of at least 16 characters")
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        roots_raw = os.environ.get("MCP_FS_ROOTS")
        roots = [os.getcwd()] if roots_raw is None or not roots_raw.strip() else _split_roots(roots_raw)
        token_raw = os.environ.get("MCP_FS_AUTH_TOKEN")
        token_file = os.environ.get("MCP_FS_AUTH_TOKEN_FILE")
        if token_raw and token_file:
            raise ValueError("set only one of MCP_FS_AUTH_TOKEN and MCP_FS_AUTH_TOKEN_FILE")
        if token_file:
            token_path = Path(token_file)
            if token_path.stat().st_size > 65_536:
                raise ValueError("MCP_FS_AUTH_TOKEN_FILE is unexpectedly large")
            token_raw = token_path.read_text(encoding="utf-8").strip()
        return cls(
            roots=roots,
            mode=os.environ.get("MCP_FS_MODE", "read-only"),
            transport=os.environ.get("MCP_FS_TRANSPORT", "stdio"),
            log_level=os.environ.get("MCP_FS_LOG_LEVEL", "INFO"),
            log_format=os.environ.get("MCP_FS_LOG_FORMAT", "json"),
            max_read_lines=int(os.environ.get("MCP_FS_MAX_READ_LINES", "200")),
            max_read_bytes=int(os.environ.get("MCP_FS_MAX_READ_BYTES", "1000000")),
            max_output_bytes=int(os.environ.get("MCP_FS_MAX_OUTPUT_BYTES", "1000000")),
            max_write_bytes=int(os.environ.get("MCP_FS_MAX_WRITE_BYTES", "5000000")),
            max_http_body_bytes=int(os.environ.get("MCP_FS_MAX_HTTP_BODY_BYTES", "4000000")),
            max_test_timeout=float(os.environ.get("MCP_FS_MAX_TEST_TIMEOUT", "300")),
            host=os.environ.get("MCP_FS_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_FS_PORT", "8000")),
            auth_mode=os.environ.get("MCP_FS_AUTH_MODE", "auto"),
            auth_token=SecretStr(token_raw) if token_raw else None,
            allow_unsafe_remote=_boolean("MCP_FS_ALLOW_UNSAFE_REMOTE"),
            allow_exec=_boolean("MCP_FS_ALLOW_EXEC"),
            allowed_hosts=_list("MCP_FS_ALLOWED_HOSTS"),
            allowed_origins=_list("MCP_FS_ALLOWED_ORIGINS"),
        )


def _split_roots(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) for item in parsed):
            raise ValueError("MCP_FS_ROOTS JSON must be a non-empty array of strings")
        return parsed
    roots = [item.strip() for item in raw.split(",") if item.strip()]
    if not roots:
        raise ValueError("MCP_FS_ROOTS must contain at least one root")
    return roots
