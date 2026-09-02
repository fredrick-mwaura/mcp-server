"""Read-only navigation tools (Phase 1: list_directory).

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
