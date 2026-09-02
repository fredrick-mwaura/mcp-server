"""Unit tests for syntax_guard - the pre-commit AST validation gate.

These tests validate that the syntax guard correctly blocks invalid Python
before it reaches disk, and correctly passes valid Python and non-Python files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_fs.errors import SyntaxValidationError
from mcp_fs.security.syntax_guard import validate_python_syntax


def test_valid_python_passes() -> None:
    """Valid Python code should pass without error."""
    validate_python_syntax("x = 1\nprint(x)\n", "test.py")


def test_invalid_python_raises_syntax_error() -> None:
    """Invalid Python should raise SyntaxValidationError with line info."""
    with pytest.raises(SyntaxValidationError) as exc_info:
        validate_python_syntax("def broken(\n", "test.py")
    assert exc_info.value.code == "syntax_error"
    assert exc_info.value.error_line is not None


def test_indentation_error_raises() -> None:
    """Indentation errors should be caught by the syntax guard."""
    code = "def foo():\nreturn 1\n"  # missing indent
    with pytest.raises(SyntaxValidationError):
        validate_python_syntax(code, "test.py")


def test_non_python_file_skipped() -> None:
    """Non-Python files should pass through without validation."""
    # Invalid Python syntax, but in a .txt file — should not raise.
    validate_python_syntax("def broken(\n", "readme.txt")
    validate_python_syntax("{invalid json}", "data.json")
    validate_python_syntax("fn main() {", "main.rs")


def test_pyw_file_is_validated() -> None:
    """Python Windows scripts (.pyw) should be validated too."""
    with pytest.raises(SyntaxValidationError):
        validate_python_syntax("def broken(\n", "app.pyw")


def test_empty_python_file_passes() -> None:
    """An empty Python file is valid syntax."""
    validate_python_syntax("", "empty.py")


def test_error_includes_path_info() -> None:
    """The error should include the path for context."""
    with pytest.raises(SyntaxValidationError) as exc_info:
        validate_python_syntax("x =\n", "/project/src/broken.py")
    assert "/project/src/broken.py" in str(exc_info.value)
