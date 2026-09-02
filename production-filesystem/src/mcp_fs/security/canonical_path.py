"""Path canonicalization & workspace scoping - THE single security gate.

THIS MODULE IS THE PRODUCT. Every path that any tool ever wants to touch must
first survive `resolve_allowed_path()`. It defends against three classic
filesystem-server attacks, each of which is a one-line "aha" for students:

Attack 1 - directory traversal (../../etc/passwd)
    Fix: normalize with os.path.normpath, then verify the result is *still*
    inside an allowed root. Traversal collapses during normalization, so a
    resolved path is never '..' anymore.

Attack 2 - sibling prefix confusion (root '/data', path '/dataevil/...')
    Fix: do NOT use a string .startswith() check - '/dataevil'.startswith
    ('/data') is True but the folder is a *different* directory. We compare
    real path *components* instead (pathlib relative_to), which respects the
    separator boundary between components.

Attack 3 - symlink escape (root contains link -> /etc)
    Fix: resolve symlinks FIRST with os.path.realpath(), THEN zone-check the
    resolved path. A listing inside the root may show a symlink, but any real
    read/write resolves the link and is rejected if it lands outside.

The ordering below is deliberate and must never be re-arranged casually:
    expand ~  ->  make absolute  ->  normalize  ->  resolve symlinks
    ->  zone-check  ->  (optionally) existence check
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from mcp_fs.errors import PathNotAllowedError, PathNotFoundError


def canonical_root(root: str | Path) -> Path:
    """Normalize one configured root into its canonical real path.

    The same expand/normalize/realpath pipeline is applied to the *allowed
    roots themselves*, so both sides of the comparison live in the same
    coordinate space (no symlinks, no '..', no '~').
    """
    raw = os.fspath(root)
    expanded = os.path.expanduser(raw)
    absolute = expanded if os.path.isabs(expanded) else os.path.abspath(expanded)
    return Path(os.path.realpath(absolute))


def resolve_allowed_path(
    requested: str,
    allowed_roots: Sequence[str | Path],
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve `requested` and return its canonical path IF it is allowed.

    Args:
        requested:    Path as supplied by the AI model / caller. May be
                      relative (resolved against the server's cwd), may
                      contain '~', '..', or symlinks.
        allowed_roots: The configured workspace roots.
        must_exist:    When True, raise PathNotFoundError if the resolved path
                       does not exist. (Read tools set this True; future
                       create/write tools will set it False.)

    Returns:
        The canonical, symlink-free, absolute path.

    Raises:
        PathNotAllowedError: resolved path is outside every allowed root.
        PathNotFoundError:   must_exist=True and the path is absent.
    """
    # --- 1. reject impossible input ----------------------------------------
    if "\x00" in requested:
        # NUL bytes cannot appear in real paths; they are a classic smuggling
        # trick and would break C-level string handling.
        raise PathNotAllowedError(requested, "(contains NUL byte)", [os.fspath(r) for r in allowed_roots])
    if not requested.strip():
        # An empty path means "current directory" to os functions - but only
        # if it survives the zone check below. Be explicit instead of clever.
        requested = os.getcwd()

    # --- 2. canonicalize the requested path --------------------------------
    expanded = os.path.expanduser(requested)
    absolute = expanded if os.path.isabs(expanded) else os.path.abspath(expanded)
    normalized = os.path.normpath(absolute)   # attack 1: collapse ../..
    real = Path(os.path.realpath(normalized)) # attack 3: resolve symlinks
    # (normcase lives inside realpath's handling on Windows, where paths are
    #  case-insensitive; pathlib comparisons below honor that per-platform.)

    # --- 3. zone-check against every allowed root --------------------------
    for root in allowed_roots:
        root_real = canonical_root(root)
        if _is_within(real, root_real):
            if must_exist and not os.path.lexists(real):
                raise PathNotFoundError(str(real))
            return real

    # --- 4. denied: report clearly so the model can recover -----------------
    raise PathNotAllowedError(requested, str(real), [str(canonical_root(r)) for r in allowed_roots])


def _is_within(candidate: Path, root: Path) -> bool:
    """Component-safe containment check (attack 2 defense).

    `candidate.relative_to(root)` only succeeds when every path component
    matches, which is exactly the separator boundary we need: '/dataevil'
    does NOT live inside '/data'. Equality (candidate == root) is allowed so a
    tool can operate on a root itself.
    """
    if candidate == root:
        return True
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
