"""vfs.py - the ONLY module allowed to touch the real filesystem.

LESSON: keep OS calls in one place so the rest of the codebase can be reasoned
about (and mocked) without thinking about disks. Every function here:

  1. receives an ALREADY-APPROVED canonical path (security did its job),
  2. performs the raw os/pathlib work,
  3. raises typed errors from errors.py on expected failures.

Phase 1 ships a single read operation: list the entries of a directory.
No writes exist yet - least privilege by construction, not by discipline.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp_fs.errors import PathNotADirectoryError, PathNotFoundError

# Kind rank used for display sorting: directories first, then files/symlinks.
_KIND_RANK = {"directory": 0, "file": 1, "symlink": 2}


def list_entries(canonical_dir: Path) -> list[dict[str, object]]:
    """Return the entries of `canonical_dir` as small, stable dictionaries.

    Args:
        canonical_dir: An approved, canonical path (from security).

    Returns:
        A list of one dict per entry:
            {"name", "type": directory|file|symlink, "size_bytes"}
        Directories carry size_bytes=None (their "size" is filesystem
        dependent and cheap to omit).

    Raises:
        PathNotFoundError:       the directory does not exist.
        PathNotADirectoryError:  the path exists but is a file, not a folder.
    """
    if not os.path.lexists(canonical_dir):
        raise PathNotFoundError(str(canonical_dir))
    if not os.path.isdir(canonical_dir):
        raise PathNotADirectoryError(str(canonical_dir))

    entries: list[dict[str, object]] = []

    # os.scandir() yields entries lazily and provides per-entry stats without
    # extra syscalls - the efficient production choice over os.listdir().
    with os.scandir(canonical_dir) as iterator:
        for entry in iterator:
            # is_symlink() must be checked BEFORE is_dir()/is_file(): with
            # follow_symlinks=False (our default) a link to a directory would
            # otherwise be reported as a plain directory, hiding an escape
            # vector from the model's view.
            is_link = entry.is_symlink()
            if is_link:
                kind = "symlink"
            elif entry.is_dir(follow_symlinks=False):
                kind = "directory"
            else:
                kind = "file"

            # stat(follow_symlinks=False) = lstat: size of the link itself,
            # never the file it points at. A symlink's target may live outside
            # the workspace - we report that honestly and let the security
            # gate refuse any later dereference of it.
            size_bytes: int | None = None
            try:
                stat = entry.stat(follow_symlinks=False)
                if not entry.is_dir(follow_symlinks=False):
                    size_bytes = stat.st_size
            except OSError:
                # One unreadable entry (permissions) must not fail the whole
                # listing. Report the entry; leave size unknown.
                pass

            entries.append(
                {
                    "name": entry.name,
                    "type": kind,
                    "size_bytes": size_bytes,
                }
            )

    # Deterministic ordering: directories first, then alphabetical. Stable,
    # predictable output is friendlier for the model than raw on-disk order.
    entries.sort(key=lambda e: (_KIND_RANK.get(str(e["type"]), 9), str(e["name"]).lower()))
    return entries


# --------------------------------------------------------------------------
# Phase 2: line-sliced file reading (the #1 token-saving operation)
# --------------------------------------------------------------------------

# A file is considered binary if ANY of its first chunk contains NUL bytes.
_BINARY_PROBE_SIZE = 8192


def _is_binary(path: Path) -> bool:
    """Check if a file looks like binary by probing its first bytes."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_BINARY_PROBE_SIZE)
            return b"\x00" in chunk
    except OSError:
        return False


def read_file_slice(
    canonical_path: Path,
    *,
    offset: int = 1,
    limit: int = 200,
    max_read_bytes: int = 1_000_000,
) -> dict[str, object]:
    """Read a window of lines from an approved file, with line-number prefixes.

    This is the single most important token-saving operation: instead of dumping
    an entire 3,000-line module into the prompt (~12,000 tokens), the caller gets
    a 200-line slice with `L42: def process():` prefixes (~800 tokens).

    Args:
        canonical_path: An approved, canonical, existing path (from security).
        offset:         1-indexed start line (default: first line).
        limit:          Maximum lines to return (capped by config).
        max_read_bytes: Hard byte cap from config. Files larger than this
                        require explicit offset/limit (prevents a 40 GB dump).

    Returns:
        A response dict containing the file content, line range, and metadata.

    Raises:
        PathNotFoundError:      the file does not exist.
        PathNotAFileError:      the path is a directory.
        FileTooLargeError:      file exceeds byte cap and no offset was given.
    """
    from mcp_fs.errors import FileTooLargeError, PathNotAFileError, PathNotFoundError

    if not os.path.lexists(canonical_path):
        raise PathNotFoundError(str(canonical_path))
    if os.path.isdir(canonical_path):
        raise PathNotAFileError(str(canonical_path))

    # Binary detection: refuse to dump binary into a text context window.
    if _is_binary(canonical_path):
        stat = os.stat(canonical_path)
        return {
            "path": str(canonical_path),
            "is_binary": True,
            "size_bytes": stat.st_size,
            "content": None,
            "note": "Binary file detected. Use get_metadata for file info.",
        }

    # Size gate: only enforce on "full reads" (offset=1 with no explicit limit).
    # When the caller specifies offset/limit they are already being surgical.
    stat = os.stat(canonical_path)
    requesting_full_read = (offset == 1 and limit >= 200)
    if requesting_full_read and stat.st_size > max_read_bytes:
        raise FileTooLargeError(str(canonical_path), stat.st_size, max_read_bytes)

    # Read lines (universal newlines handles \r\n transparently).
    try:
        with open(canonical_path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except OSError as exc:
        raise PathNotFoundError(str(canonical_path)) from exc

    total_lines = len(all_lines)

    # Clamp offset/limit to valid bounds.
    start_idx = max(0, offset - 1)  # 1-indexed -> 0-indexed
    end_idx = min(start_idx + limit, total_lines)
    selected = all_lines[start_idx:end_idx]

    # Format with line numbers: "L42: content\n" — lets the model reference
    # exact lines in its edit_block calls without hallucinating line numbers.
    numbered_lines = []
    for i, line in enumerate(selected, start=start_idx + 1):
        # Strip trailing newline; we rebuild structure in the output.
        numbered_lines.append(f"L{i}: {line.rstrip()}")

    return {
        "path": str(canonical_path),
        "is_binary": False,
        "total_lines": total_lines,
        "range": {"start": start_idx + 1, "end": end_idx, "count": len(selected)},
        "size_bytes": stat.st_size,
        "content": "\n".join(numbered_lines),
    }


# --------------------------------------------------------------------------
# Phase 2: file discovery (glob-based)
# --------------------------------------------------------------------------

def find_files(
    canonical_root: Path,
    pattern: str = "*",
    *,
    max_results: int = 100,
    max_depth: int = 8,
) -> list[dict[str, object]]:
    """Find files matching a glob pattern under an approved root.

    Uses os.walk with depth limiting — no unbounded recursive scans that
    could freeze on massive monorepos.

    Args:
        canonical_root: An approved, canonical directory path.
        pattern:        A glob pattern (e.g. '*.py', 'test_*.py').
        max_results:    Stop after this many matches (token budget).
        max_depth:      Maximum directory depth to search.

    Returns:
        A list of match dicts with relative_path, type, and size_bytes.
    """
    import fnmatch

    if not os.path.isdir(canonical_root):
        from mcp_fs.errors import PathNotADirectoryError
        raise PathNotADirectoryError(str(canonical_root))

    root_depth = str(canonical_root).count(os.sep)
    results: list[dict[str, object]] = []

    for dirpath, dirnames, filenames in os.walk(canonical_root):
        # Depth limiting: stop descending into deeper directories.
        current_depth = dirpath.count(os.sep) - root_depth
        if current_depth >= max_depth:
            dirnames.clear()  # prevent further descent
            continue

        # Skip hidden directories (e.g. .git, .venv, __pycache__).
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
        )

        for filename in sorted(filenames):
            if fnmatch.fnmatch(filename, pattern):
                full_path = Path(dirpath) / filename
                try:
                    rel = full_path.relative_to(canonical_root)
                    size = full_path.stat().st_size
                except (ValueError, OSError):
                    continue
                results.append({
                    "relative_path": str(rel),
                    "name": filename,
                    "size_bytes": size,
                })
                if len(results) >= max_results:
                    return results

    return results
