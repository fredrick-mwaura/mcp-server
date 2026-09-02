"""server.py - assembles and runs the MCP filesystem server.

This is the wiring layer. It:
  1. loads Settings from the environment,
  2. configures JSON logging,
  3. builds the allowed-root set (validating each root exists),
  4. creates an MCPServer and registers tools as *closures* that capture
     the root set and config limits,
  5. runs on the configured transport (stdio for Phase 1-2).

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
from mcp_fs.errors import FileSystemError, ReadOnlyModeError
from mcp_fs.security import canonical_root, resolve_allowed_path, validate_python_syntax
from mcp_fs.telemetry import configure_logging
from mcp_fs.tools import navigation, search, outline, editing
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

    # Capture config limits into the closure scope.
    max_read_lines = settings.max_read_lines
    max_read_bytes = settings.max_read_bytes
    server_mode = settings.mode

    def _require_write_mode(tool_name: str) -> None:
        """Raise ReadOnlyModeError if the server is not in read-write mode."""
        if server_mode != "read-write":
            raise ReadOnlyModeError(tool_name)

    def _find_root_for(canonical: 'Path') -> 'Path':
        """Find which allowed root a canonical path belongs to."""
        from pathlib import Path as P
        for r in roots:
            try:
                canonical.relative_to(r)
                return r
            except ValueError:
                continue
        return roots[0]  # fallback (should not happen after security gate)

    # The instructions reach the model on connection. Telling the agent WHICH
    # directories are legal turns a wall of path errors into good first tries.
    mode_desc = (
        "Read and write tools are available."
        if server_mode == "read-write"
        else "It is READ-ONLY. Write tools will return an error."
    )
    instructions = (
        "This server provides secure, token-efficient file system access. "
        "Read tools: list_directory, read_file (line-sliced), find_files, "
        "grep_search (ripgrep-accelerated), symbol_outline (AST map). "
        "Write tools: edit_block (surgical), write_file (atomic), "
        "apply_patch (unified diff), delete_entry (safe trash). "
        f"{mode_desc} Only the following root(s) may be accessed; "
        "any other path is rejected:\n"
        + "\n".join(f"- {r}" for r in root_strings)
        + "\nPrefer absolute paths inside an allowed root."
    )

    mcp = MCPServer(
        "File System Server",
        version=__version__,
        instructions=instructions,
    )

    # =================================================================
    # TOOL: list_directory (Phase 1)
    # =================================================================
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

    # =================================================================
    # TOOL: read_file (Phase 2 — the #1 token-saving operation)
    # =================================================================
    @mcp.tool()
    def read_file(
        path: str,
        offset: int = 1,
        limit: int = 200,
    ) -> dict[str, object]:
        """Read a slice of a file with line-number prefixes (e.g. "L42: code").

        This tool is designed for minimal token usage: it returns a bounded
        window of lines instead of the entire file. Use offset and limit to
        navigate large files. Binary files are detected and refused.

        Args:
            path:   The file to read, e.g. "/Users/me/project/src/main.py".
            offset: 1-indexed start line (default: 1).
            limit:  Maximum lines to return (default: 200, capped by config).

        Returns:
            A dictionary with line-numbered content, range info, total_lines,
            and size_bytes. Binary files return is_binary=True with no content.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "read_file", "path": path, "offset": offset, "limit": limit})
        try:
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            clamped_limit = min(limit, max_read_lines)
            result = navigation.read_file(
                canonical,
                offset=offset,
                limit=clamped_limit,
                max_read_bytes=max_read_bytes,
            )
            logger.info(
                "tool_result",
                extra={"tool": "read_file", "ok": True,
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "read_file", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "read_file"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: find_files (Phase 2)
    # =================================================================
    @mcp.tool()
    def find_files(
        pattern: str = "*",
        path: str = ".",
        max_results: int = 100,
    ) -> dict[str, object]:
        """Find files matching a glob pattern under an allowed directory.

        Searches recursively up to depth 8, skipping hidden dirs and
        __pycache__. Returns relative paths, names, and sizes.

        Args:
            pattern:     A glob pattern, e.g. "*.py", "test_*.py", "*.ts".
            path:        The directory to search under (default: ".").
            max_results: Maximum number of results (default: 100).

        Returns:
            A dictionary with matches (relative_path, name, size_bytes),
            match_count, and whether results were truncated.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "find_files", "pattern": pattern, "path": path})
        try:
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            result = navigation.find_files(
                canonical,
                pattern,
                max_results=min(max_results, 200),
                max_depth=8,
            )
            logger.info(
                "tool_result",
                extra={"tool": "find_files", "ok": True,
                       "matches": result["match_count"],
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "find_files", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "find_files"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: grep_search (Phase 2 — ripgrep-accelerated code search)
    # =================================================================
    @mcp.tool()
    def grep_search(
        query: str,
        path: str = ".",
        regex: bool = False,
        include_globs: list[str] | None = None,
        max_results: int = 30,
        context_lines: int = 1,
    ) -> dict[str, object]:
        """Search for a text pattern or regex across files in a directory.

        Uses ripgrep (rg) when available for 10-100x faster searches, with
        a pure-Python fallback. Returns compact match snippets with file
        paths and line numbers — 90% fewer tokens than reading full files.

        Args:
            query:         The search string or regex pattern.
            path:          The directory to search under (default: ".").
            regex:         If True, treat query as a regex pattern.
            include_globs: Optional file-type filters (e.g. ["*.py"]).
            max_results:   Maximum number of match lines (default: 30).
            context_lines: Surrounding context per match (default: 1).

        Returns:
            A dictionary with matches grouped by file, each containing
            line numbers and matched text.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "grep_search", "query": query, "path": path})
        try:
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            result = search.grep_search(
                canonical,
                query,
                regex=regex,
                include_globs=include_globs,
                max_results=min(max_results, 100),
                context_lines=min(context_lines, 5),
            )
            logger.info(
                "tool_result",
                extra={"tool": "grep_search", "ok": True,
                       "matches": result["match_count"],
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "grep_search", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "grep_search"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: symbol_outline (Phase 2 — AST structural map)
    # =================================================================
    @mcp.tool()
    def symbol_outline(path: str) -> dict[str, object]:
        """Extract a structural outline of a Python file's classes and functions.

        Returns a compact map of top-level symbols with line ranges, args,
        docstrings, and decorators — typically 98% fewer tokens than reading
        the full file. Use this first to understand code layout, then
        read_file with targeted offset/limit for the specific section.

        Args:
            path: The Python file to outline, e.g. "/project/src/config.py".

        Returns:
            A dictionary with a list of symbols. Each symbol has kind,
            name, line_start, line_end, and (for classes) a methods list.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "symbol_outline", "path": path})
        try:
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            result = outline.symbol_outline(canonical)
            logger.info(
                "tool_result",
                extra={"tool": "symbol_outline", "ok": True,
                       "symbols": result.get("symbol_count", 0),
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "symbol_outline", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "symbol_outline"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: edit_block (Phase 3 — surgical string replacement)
    # =================================================================
    @mcp.tool()
    def edit_block(
        path: str,
        target_content: str,
        replacement_content: str,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Replace a unique text chunk in a file with new content.

        The most token-efficient way to edit code: send only the exact text
        to find and its replacement. The target must appear exactly once in
        the file. Use dry_run=True to preview without modifying.

        Python files are validated with ast.parse() before saving — if the
        edit would introduce a syntax error, the file is NOT modified and
        the exact error is returned.

        Args:
            path:                The file to edit.
            target_content:      The exact text to find (must appear once).
            replacement_content: The replacement text.
            dry_run:             If True, report what would happen without editing.

        Returns:
            On success: path, action, and edit details.
            On dry_run: confirmation that the target was found.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "edit_block", "path": path, "dry_run": dry_run})
        try:
            _require_write_mode("edit_block")
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            result = editing.edit_block(
                canonical,
                target_content,
                replacement_content,
                dry_run=dry_run,
            )
            if not dry_run and result.get("action") == "edit_ready":
                new_content = result["new_content"]
                # AST syntax gate: validate Python before touching disk.
                validate_python_syntax(new_content, canonical)
                # Commit via atomic write.
                from mcp_fs import vfs
                write_result = vfs.write_file_atomic(canonical, new_content)
                result = {
                    "path": str(canonical),
                    "action": "edited",
                    "size_bytes": write_result["size_bytes"],
                }
            logger.info(
                "tool_result",
                extra={"tool": "edit_block", "ok": True,
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "edit_block", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "edit_block"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: write_file (Phase 3 — atomic create/overwrite)
    # =================================================================
    @mcp.tool()
    def write_file(
        path: str,
        content: str,
    ) -> dict[str, object]:
        """Create or overwrite a file atomically.

        Uses a crash-safe write pattern: content goes to a temp file first,
        is fsynced to disk, then atomically replaces the target. If the
        process dies mid-write, the original file is untouched.

        Python files are validated with ast.parse() before saving.

        Args:
            path:    The file to create or overwrite.
            content: The full file content to write.

        Returns:
            A dictionary with path, size_bytes, and action (created/overwritten).
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "write_file", "path": path})
        try:
            _require_write_mode("write_file")
            canonical = resolve_allowed_path(path, root_strings, must_exist=False)
            # AST syntax gate for Python files.
            validate_python_syntax(content, canonical)
            result = editing.write_file(canonical, content)
            logger.info(
                "tool_result",
                extra={"tool": "write_file", "ok": True,
                       "action": result["action"],
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "write_file", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "write_file"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: apply_patch (Phase 3 — unified diff application)
    # =================================================================
    @mcp.tool()
    def apply_patch(
        patch: str,
        path: str = ".",
    ) -> dict[str, object]:
        """Apply a unified diff patch to files under an allowed directory.

        Accepts standard unified diff format (as from `git diff`). Parses
        the patch, validates syntax for Python files, and applies changes
        atomically.

        Args:
            patch: The unified diff string.
            path:  The root directory the patch paths are relative to.

        Returns:
            A dictionary with files_modified count, file list, and any errors.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "apply_patch", "path": path})
        try:
            _require_write_mode("apply_patch")
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            parse_result = editing.apply_patch(canonical, patch)

            # Apply each parsed file result with syntax validation.
            applied_files: list[str] = []
            errors: list[str] = list(parse_result.get("errors", []))

            for file_result in parse_result.get("results", []):
                rel_path = file_result["file"]
                new_content = file_result["new_content"]
                target_path = canonical / rel_path

                try:
                    validate_python_syntax(new_content, target_path)
                    from mcp_fs import vfs
                    if file_result.get("is_new"):
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                    vfs.write_file_atomic(target_path, new_content)
                    applied_files.append(rel_path)
                except FileSystemError as file_exc:
                    errors.append(f"{rel_path}: {file_exc.message}")

            result = {
                "root": str(canonical),
                "action": "patch_applied",
                "files_modified": len(applied_files),
                "files": applied_files,
                "errors": errors,
            }
            logger.info(
                "tool_result",
                extra={"tool": "apply_patch", "ok": True,
                       "files": len(applied_files),
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "apply_patch", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "apply_patch"})
            return {"error_code": "internal_error", "error": f"Unexpected error: {exc}"}

    # =================================================================
    # TOOL: delete_entry (Phase 3 — safe deletion to .trash/)
    # =================================================================
    @mcp.tool()
    def delete_entry(
        path: str,
    ) -> dict[str, object]:
        """Delete a file or directory by moving it to .trash/ (recoverable).

        Instead of permanent deletion, files are moved to a .trash/
        directory inside the allowed root. This provides immediate recovery
        if the wrong file is deleted.

        Args:
            path: The file or directory to delete.

        Returns:
            A dictionary with the original path and the trash destination.
        """
        started = time.perf_counter()
        logger.info("tool_call", extra={"tool": "delete_entry", "path": path})
        try:
            _require_write_mode("delete_entry")
            canonical = resolve_allowed_path(path, root_strings, must_exist=True)
            root = _find_root_for(canonical)
            result = editing.delete_entry(canonical, root)
            logger.info(
                "tool_result",
                extra={"tool": "delete_entry", "ok": True,
                       "duration_ms": _ms_since(started)},
            )
            return result
        except FileSystemError as exc:
            logger.info(
                "tool_result",
                extra={"tool": "delete_entry", "ok": False,
                       "error_code": exc.code, "duration_ms": _ms_since(started)},
            )
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected_tool_error", extra={"tool": "delete_entry"})
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
