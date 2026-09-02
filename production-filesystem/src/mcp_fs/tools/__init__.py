"""tools package - thin MCP tool handlers.

Each tool handler in here is *dumb on purpose*:
    it formats input for the layers below and formats their output for MCP.
It never resolves paths (security), never touches the filesystem (vfs), and
contains no business policy. That separation is the difference between a
one-file lesson server and a maintainable production server.

Phase 1 registerable tools:
    - navigation.list_directory   (read-only)
"""

from mcp_fs.tools.navigation import list_directory

__all__ = ["list_directory"]
