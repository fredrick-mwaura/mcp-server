"""Read-only navigation tools (Phase 1: list_directory, Phase 2: read_file, find_files).

The handler receives an ALREADY-APPROVED canonical path - the security
decision happened in the layer above (server.py). Its only jobs are:
  1. call vfs for the raw data,
  2. shape the response the way an AI model best consumes it.
"""

from __future__ import annotations

from pathlib import Path

from mcp_fs import vfs


def list_directory(canonical_dir: Path) -> dict[str, object]:
    """List entries inside an approved directory.

    Args:
        canonical_dir: A security-approved, canonical, existing path.

    Returns:
        A response dict with the resolved path and its entries. PathNotFound
        / PathNotADirectory are raised as typed errors for the caller to map.
    """
    entries = vfs.list_entries(canonical_dir)
    return {
        "path": str(canonical_dir),
        "entry_count": len(entries),
        "entries": entries,
    }


def read_file(
    canonical_path: Path,
    *,
    offset: int = 1,
    limit: int = 200,
    max_read_bytes: int = 1_000_000,
) -> dict[str, object]:
    """Read a slice of an approved file with line-number prefixes.

    This handler is deliberately thin: all the slicing, binary detection,
    and size-cap logic lives in vfs.read_file_slice(). We just relay.

    Args:
        canonical_path: A security-approved, canonical, existing path.
        offset:         1-indexed start line.
        limit:          Maximum lines to return.
        max_read_bytes: Hard byte cap from config.

    Returns:
        A response dict with line-numbered content, range info, and metadata.
    """
    return vfs.read_file_slice(
        canonical_path,
        offset=offset,
        limit=limit,
        max_read_bytes=max_read_bytes,
    )


def find_files(
    canonical_root: Path,
    pattern: str = "*",
    *,
    max_results: int = 100,
    max_depth: int = 8,
) -> dict[str, object]:
    """Find files matching a glob pattern under an approved root.

    Args:
        canonical_root: A security-approved, canonical directory path.
        pattern:        A glob pattern (e.g. '*.py').
        max_results:    Stop after this many matches.
        max_depth:      Maximum directory depth to search.

    Returns:
        A response dict with the matches and counts.
    """
    matches = vfs.find_files(
        canonical_root,
        pattern,
        max_results=max_results,
        max_depth=max_depth,
    )
    return {
        "root": str(canonical_root),
        "pattern": pattern,
        "match_count": len(matches),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }
