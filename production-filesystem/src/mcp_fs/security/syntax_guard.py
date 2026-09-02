"""syntax_guard.py - AST pre-commit validation for Python files (Phase 3).

This is the module that prevents the single most expensive failure mode in
LLM-driven code editing: the agent introduces a syntax error (bad indent,
missing colon, unclosed paren), the file is saved, and the agent then spends
3-5 additional turns trying to figure out what broke — each turn costing
more API tokens.

The fix is simple: before ANY write or edit to a .py file reaches disk, we
parse the proposed content with ast.parse(). If it fails, we reject the
write immediately and return the exact line and error to the agent. The
disk stays untouched. One turn, zero wasted tokens.

This module is part of the security/ package because it is a *gate* — the
same pattern as canonical_path.py. Nothing writes to disk without passing
through here first (for Python files).
"""

from __future__ import annotations

import ast
from pathlib import Path

from mcp_fs.errors import SyntaxValidationError


def validate_python_syntax(content: str, path: str | Path) -> None:
    """Parse `content` as Python and raise if it has syntax errors.

    This function is a no-op for non-Python paths. It only fires when the
    file extension is .py (or .pyw).

    Args:
        content: The proposed file content (the FULL file after the edit).
        path:    The destination path (used for error messages and to check
                 if the file is Python).

    Raises:
        SyntaxValidationError: the content would not parse as valid Python.
    """
    path_str = str(path)
    suffix = Path(path_str).suffix.lower()
    if suffix not in (".py", ".pyw"):
        return  # Not a Python file — no syntax validation needed.

    try:
        ast.parse(content, filename=path_str)
    except SyntaxError as exc:
        raise SyntaxValidationError(
            path=path_str,
            line=exc.lineno,
            detail=exc.msg,
        ) from exc
