"""grep_search - high-speed code search with bounded context (Phase 2).

This handler provides fast, token-efficient code search across allowed
workspace roots. It tries to use `ripgrep` (rg) for maximum speed but
falls back to a pure-Python implementation if rg is not installed.

Why this exists instead of having the model scan files manually:
  - A manual scan wastes 10+ tool calls and thousands of tokens.
  - grep_search returns compact match snippets with file, line number,
    and 1-2 lines of bounding context — typically 90% fewer tokens than
    dumping full files.
"""

from __future__ import annotations

import os
import re
import shutil
from collections import deque
from pathlib import Path

from mcp_fs.errors import InvalidArgumentError
from mcp_fs.process import run_bounded


def grep_search(
    canonical_root: Path,
    query: str,
    *,
    regex: bool = False,
    include_globs: list[str] | None = None,
    max_results: int = 30,
    context_lines: int = 1,
    max_output_bytes: int = 1_000_000,
) -> dict[str, object]:
    """Search for a pattern across files under an approved root.

    Args:
        canonical_root: A security-approved, canonical directory path.
        query:          The search string or regex pattern.
        regex:          If True, treat query as a regex pattern.
        include_globs:  Optional file-type filters (e.g. ['*.py', '*.js']).
        max_results:    Maximum number of match groups to return.
        context_lines:  Lines of surrounding context per match.

    Returns:
        A response dict with grouped matches by file.
    """
    if max_results < 1 or context_lines < 0:
        raise InvalidArgumentError("search limits", "max_results must be positive and context_lines non-negative")
    if regex:
        try:
            re.compile(query)
        except re.error as exc:
            raise InvalidArgumentError("query", f"invalid regular expression: {exc}") from exc
    if not query.strip():
        return {
            "root": str(canonical_root),
            "query": query,
            "match_count": 0,
            "matches": [],
        }

    # Try ripgrep first (10-100x faster than pure Python on large repos).
    rg_path = shutil.which("rg")
    if rg_path:
        return _search_with_ripgrep(
            canonical_root, query,
            regex=regex,
            include_globs=include_globs,
            max_results=max_results,
            context_lines=context_lines,
            max_output_bytes=max_output_bytes,
        )

    # Fallback: pure Python search (no external dependency required).
    return _search_python_fallback(
        canonical_root, query,
        regex=regex,
        include_globs=include_globs,
        max_results=max_results,
        context_lines=context_lines,
        max_output_bytes=max_output_bytes,
    )


def _search_with_ripgrep(
    root: Path,
    query: str,
    *,
    regex: bool,
    include_globs: list[str] | None,
    max_results: int,
    context_lines: int,
    max_output_bytes: int,
) -> dict[str, object]:
    """Execute ripgrep and parse its JSON output."""
    cmd = [
        "rg",
        "--json",                          # structured output
        "--max-count", str(max_results),   # per-file limit
        "--context", str(context_lines),   # surrounding lines
        "--max-depth", "8",                # depth cap
        "--hidden", "--glob", "!.git",     # include dotfiles except .git
    ]

    if not regex:
        cmd.append("--fixed-strings")

    if include_globs:
        for glob in include_globs:
            cmd.extend(["--glob", glob])

    cmd.extend(["--", query, str(root)])

    try:
        result = run_bounded(
            cmd, cwd=root, timeout=15,
            max_output_bytes=max_output_bytes,
        )
    except FileNotFoundError:
        # Fall back to Python if rg fails.
        return _search_python_fallback(
            root, query,
            regex=regex,
            include_globs=include_globs,
            max_results=max_results,
            context_lines=context_lines,
            max_output_bytes=max_output_bytes,
        )

    if result.timed_out:
        return _search_python_fallback(
            root, query, regex=regex, include_globs=include_globs,
            max_results=max_results, context_lines=context_lines,
            max_output_bytes=max_output_bytes,
        )
    return _parse_rg_json(root, query, result.stdout, max_results, max_output_bytes)


def _parse_rg_json(
    root: Path, query: str, json_output: str, max_results: int, max_output_bytes: int = 1_000_000,
) -> dict[str, object]:
    """Parse ripgrep's JSON-lines output into our response format."""
    import json

    matches: list[dict[str, object]] = []
    current_file: str | None = None
    file_matches: list[dict[str, object]] = []
    total_count = 0
    output_bytes = 0
    output_truncated = False

    for line in json_output.splitlines():
        if total_count >= max_results:
            break
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type")
        data = entry.get("data", {})

        if msg_type == "begin":
            # New file section.
            path_info = data.get("path", {})
            current_file = path_info.get("text", "")
            file_matches = []
        elif msg_type == "match" and current_file:
            line_number = data.get("line_number", 0)
            line_text = data.get("lines", {}).get("text", "").rstrip()
            hit = {
                "line": line_number,
                "text": line_text,
                "is_match": True,
            }
            size = len(str(hit).encode("utf-8"))
            if output_bytes + size > max_output_bytes:
                output_truncated = True
                break
            file_matches.append(hit)
            output_bytes += size
            total_count += 1
        elif msg_type == "context" and current_file and total_count < max_results:
            line_number = data.get("line_number", 0)
            line_text = data.get("lines", {}).get("text", "").rstrip()
            hit = {"line": line_number, "text": line_text, "is_match": False}
            size = len(str(hit).encode("utf-8"))
            if output_bytes + size <= max_output_bytes:
                file_matches.append(hit)
                output_bytes += size
            else:
                output_truncated = True
        elif msg_type == "end" and current_file and file_matches:
            # Emit a relative path for token savings.
            try:
                rel = str(Path(current_file).relative_to(root))
            except ValueError:
                rel = current_file
            matches.append({
                "file": rel,
                "hits": file_matches,
            })
            current_file = None
            file_matches = []

    if current_file and file_matches:
        try:
            rel = str(Path(current_file).relative_to(root))
        except ValueError:
            rel = current_file
        matches.append({"file": rel, "hits": file_matches})

    return {
        "root": str(root),
        "query": query,
        "match_count": total_count,
        "truncated": total_count >= max_results or output_truncated,
        "matches": matches,
    }


def _search_python_fallback(
    root: Path,
    query: str,
    *,
    regex: bool,
    include_globs: list[str] | None,
    max_results: int,
    context_lines: int,
    max_output_bytes: int,
) -> dict[str, object]:
    """Pure-Python search fallback when ripgrep is not available."""
    import fnmatch

    pattern = re.compile(query if regex else re.escape(query))
    matches: list[dict[str, object]] = []
    total_count = 0
    root_depth = str(root).count(os.sep)
    output_bytes = 0
    output_truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        if total_count >= max_results:
            break

        # Depth limit.
        current_depth = dirpath.count(os.sep) - root_depth
        if current_depth >= 8:
            dirnames.clear()
            continue

        # Skip hidden dirs and __pycache__.
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
            and not (Path(dirpath) / d).is_symlink()
        )

        for filename in sorted(filenames):
            if total_count >= max_results:
                break

            # Apply include globs if specified.
            if include_globs:
                if not any(fnmatch.fnmatch(filename, g) for g in include_globs):
                    continue

            filepath = Path(dirpath) / filename
            if filepath.is_symlink():
                continue

            # Stream text and retain only the bounded context window.
            try:
                with open(filepath, "r", encoding="utf-8", errors="strict") as fh:
                    before: deque[tuple[int, str]] = deque(maxlen=context_lines)
                    file_hits: list[dict[str, object]] = []
                    after_remaining = 0
                    emitted_lines: set[int] = set()
                    for i, line in enumerate(fh, start=1):
                        is_match = bool(pattern.search(line))
                        if is_match:
                            for number, text in before:
                                if number not in emitted_lines:
                                    file_hits.append({"line": number, "text": text, "is_match": False})
                                    emitted_lines.add(number)
                            file_hits.append({"line": i, "text": line.rstrip(), "is_match": True})
                            emitted_lines.add(i)
                            total_count += 1
                            after_remaining = context_lines
                        elif after_remaining > 0 and i not in emitted_lines:
                            file_hits.append({"line": i, "text": line.rstrip(), "is_match": False})
                            emitted_lines.add(i)
                            after_remaining -= 1
                        before.append((i, line.rstrip()))
                        if total_count >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue

            if file_hits:
                try:
                    rel = str(filepath.relative_to(root))
                except ValueError:
                    rel = str(filepath)
                group = {
                    "file": rel,
                    "hits": file_hits,
                }
                size = len(str(group).encode("utf-8"))
                if output_bytes + size > max_output_bytes:
                    output_truncated = True
                    break
                matches.append(group)
                output_bytes += size

    return {
        "root": str(root),
        "query": query,
        "match_count": total_count,
        "truncated": total_count >= max_results or output_truncated,
        "matches": matches,
    }
