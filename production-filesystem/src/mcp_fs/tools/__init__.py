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

Phase 3 tools:
    - editing.edit_block           (write, surgical string replacement)
    - editing.write_file           (write, atomic file creation/overwrite)
    - editing.apply_patch          (write, unified diff patch application)
    - editing.delete_entry         (write, safe deletion to .trash/)

Phase 4 tools:
    - git_ops.git_status           (read-only, structured branch & file status)
    - git_ops.git_diff             (read-only, unified diff with token limits)
    - git_ops.export_swebench_patch(read-only, SWE-bench submission patch)
    - git_ops.revert_file          (write, instant rollback to HEAD)

Phase 5 tools:
    - execution.run_test           (write/exec, bounded runner with failure distillation)
"""

from mcp_fs.tools.navigation import list_directory, read_file, find_files
from mcp_fs.tools.search import grep_search
from mcp_fs.tools.outline import symbol_outline
from mcp_fs.tools.editing import edit_block, write_file, delete_entry, apply_patch
from mcp_fs.tools.git_ops import git_status, git_diff, export_swebench_patch, revert_file
from mcp_fs.tools.execution import run_test, distill_test_output

__all__ = [
    "list_directory",
    "read_file",
    "find_files",
    "grep_search",
    "symbol_outline",
    "edit_block",
    "write_file",
    "delete_entry",
    "apply_patch",
    "git_status",
    "git_diff",
    "export_swebench_patch",
    "revert_file",
    "run_test",
    "distill_test_output",
]
