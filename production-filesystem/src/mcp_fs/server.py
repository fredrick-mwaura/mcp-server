"""server.py - assembles and runs the MCP filesystem server.

This is the wiring layer. It:
  1. loads Settings from the environment,
  2. configures JSON logging,
  3. builds the allowed-root set (validating each root exists),
  4. creates an MCPServer and registers the Phase 1 tools as *closures* that
     capture that root set,
  5. runs on the configured transport (stdio for Phase 1).

LESSON - closures as dependency injection:
  The tool function must know the allowed roots. We could read a global, but
  globals are why config leaks between tests and deploys. Instead `build_server`
  creates the tool *inside* the function scope, so each server instance has its
  own captured security context. Same pattern scales to HTTP mode in Phase 4:
  one server object, two transports.
"""

from __future__ import annotations

import logging
import sys
import time

from pydantic import ValidationError

from mcp_fs.config import Settings
from mcp_fs.errors import FileSystemError
from mcp_fs.security import canonical_root, resolve_allowed_path
from mcp_fs.telemetry import configure_logging
from mcp_fs.tools import navigation
from mcp_fs import __version__

# --- SDK v2 --------------------------------------------------------------
# The official high-level MCP class. Lesson 1 already used it; here it serves
# the SAME API but we register tools built at runtime, not module level.
from mcp.server.mcpserver import MCPServer

logger = logging.getLogger("mcp_fs")


def _validate_roots(settings: Settings) -> list:
    """Canonicalize every configured root and fail fast if one is unusable.

    Fail-fast beats runtime surprises: a typo'd root in the config should stop
    the server at startup with a clear message, not silently produce
    path_not_allowed errors for everything the agent tries.
    """
    roots = [canonical_root(r) for r in settings.roots]
    missing = [str(r) for r in roots if not r.exists() or not r.is_dir()]
    if missing:
        raise ValueError(
            "Configured MCP_FS_ROOTS is not an existing directory: "
            + ", ".join(missing)
            + " (set MCP_FS_ROOTS to a comma-separated list of directories.)"
        )
    return roots


def build_server(settings: Settings) -> MCPServer:
    """Create a fully-wired MCPServer for the given settings.

    Pure function of its inputs (no globals) - this is what makes integration
    tests trivial: build a server around a tmp_path and call its tools.
    """
    roots = _validate_roots(settings)
    root_strings = [str(r) for r in roots]

    # The instructions reach the model on connection. Telling the agent WHICH
    # directories are legal turns a wall of path errors into good first tries.
    instructions = (
        "This server lists files and folders on the host computer. "
        "It is READ-ONLY in Phase 1. Only the following root(s) may be "
        "accessed; any other path is rejected:\n"
        + "\n".join(f"- {r}" for r in root_strings)
        + "\nPrefer absolute paths inside an allowed root."
    )

    mcp = MCPServer(
        "File System Server",
        version=__version__,
        instructions=instructions,
    )

    # ---------------------------------------------------------------------
    # TOOL: list_directory (the only Phase 1 tool)
    # ---------------------------------------------------------------------
    @mcp.tool()
    def list_directory(path: str = ".") -> dict[str, object]:
        """List the files and folders inside a directory on the host.

        The directory must live inside one of the server's allowed roots;
        otherwise an error with error_code "path_not_allowed" is returned.
        Relative paths resolve against the server's current directory, so
        prefer absolute paths inside an allowed root.

        Args:
            path: The directory to inspect, e.g. "/Users/me/project/src".

        Returns:
            A dictionary with the resolved path, the entry count, and the
            entries (name, type, size_bytes). Directories are listed first.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "list_directory", "path": path})
        try:
            # --- security gate: no vfs call happens until this passes ------
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            # --- delegate: the handler knows nothing about security ---------
            result = navigation.list_directory(canonical)
            logger.info(
                "tool_result",
                extra={"tool": "list_directory", "ok": True,
                       "entries": result["entry_count"],
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            # Expected, typed failures -> clean structured error for the model.
            logger.info(
                "tool_result",
                extra={"tool": "list_directory", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001 - never let one bad call kill us
            logger.exception("unexpected_tool_error", extra={"tool": "list_directory"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    return mcp


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def main() -> int:
    """Console-script entry point. Returns a process exit code."""
    try:
        settings = Settings.from_env()
    except (ValueError, ValidationError) as exc:
        # pydantic's ValidationError is verbose; surface the first problem.
        msg = getattr(exc, "errors", None)
        if msg:
            detail = msg()[0]
            print(f"Configuration error in {detail.get('loc', '?')}: "
                  f"{detail.get('msg', str(exc))}", file=sys.stderr)
        else:
            print(f"Configuration error: {exc}", file=sys.stderr)
        return 2  # conventional "usage/config error" exit code

    configure_logging(settings.log_level, settings.log_format)

    try:
        server = build_server(settings)
    except ValueError as exc:
        logger.error("startup_config_error", extra={"reason": str(exc)})
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logger.info(
        "server_starting",
        extra={
            "roots": settings.roots,
            "mode": settings.mode,
            "transport": settings.transport,
        },
    )
    logger.info("Listening on stdio. Connect me to an MCP client such as opencode.")

    # Block forever, serving JSON-RPC over stdin/stdout until the client exits.
    # Phase 4 swaps this call for the streamable-http runner behind auth.
    server.run(transport=settings.transport)
    return 0
