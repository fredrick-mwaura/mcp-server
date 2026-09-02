"""security - the ONLY layer that decides whether a path may be touched.

Everything else in this package trusts security's verdict. If a path is not
approved here, it never reaches vfs.py, and therefore never reaches the real
filesystem. This single-gate design is what makes the attack surface auditable:

    tool handler  ->  security.canonical_path.resolve_allowed_path()  ->  vfs
"""

from mcp_fs.security.canonical_path import canonical_root, resolve_allowed_path

__all__ = ["canonical_root", "resolve_allowed_path"]
