"""tools package - thin MCP tool handlers.

Each tool handler in here is *dumb on purpose*:
    it formats input for the layers below and formats their output for MCP.
It never resolves paths (security), never touches the filesystem (vfs), and
contains no business policy. That separation is the difference between a
one-file lesson server and a maintainable production server.

Phase 1 tools:
    - navigation.list_directory   (read-only)

Phase 2 tools:
    - navigation.read_file        (read-only, line-sliced, token-preserving)
    - navigation.find_files       (read-only, glob-based file discovery)
    - search.grep_search          (read-only, ripgrep-accelerated code search)
    - outline.symbol_outline      (read-only, AST structural map)
"""

from mcp_fs.tools.navigation import list_directory, read_file, find_files
from mcp_fs.tools.search import grep_search
from mcp_fs.tools.outline import symbol_outline

__all__ = [
    "list_directory",
    "read_file",
    "find_files",
    "grep_search",
    "symbol_outline",
]
