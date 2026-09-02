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
