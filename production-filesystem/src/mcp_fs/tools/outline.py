"""symbol_outline - AST-based structural map of a Python file.

This is the second-highest token-saving tool after read_file slicing.
Instead of reading an entire 2,000-line module to find where a class or
function is defined, the agent gets a compact 20-30 line structural map:

    class Settings (L33-L100)
      def from_env (L55-L74)
      def _split_roots (L77-L99)

This lets the model navigate codebases with ~98% fewer tokens than reading
full files. The agent sees the "table of contents" and then uses read_file
with targeted offset/limit to read only the section it cares about.

For now this only supports Python files (via the stdlib ast module). Future
phases may add Tree-sitter for multi-language support.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path


def symbol_outline(canonical_path: Path) -> dict[str, object]:
    """Extract top-level classes, functions, and their methods from a Python file.

    Args:
        canonical_path: A security-approved, canonical, existing .py file path.

    Returns:
        A response dict with a list of symbols, each containing name, kind,
        line_start, line_end, args (for functions), and children (for classes).
    """
    from mcp_fs.errors import PathNotAFileError, PathNotFoundError

    if not os.path.lexists(canonical_path):
        raise PathNotFoundError(str(canonical_path))
    if os.path.isdir(canonical_path):
        raise PathNotAFileError(str(canonical_path))

    suffix = canonical_path.suffix.lower()
    if suffix != ".py":
        return {
            "path": str(canonical_path),
            "language": suffix.lstrip(".") if suffix else "unknown",
            "supported": False,
            "note": "symbol_outline currently supports Python (.py) files only.",
            "symbols": [],
        }

    try:
        source = canonical_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PathNotFoundError(str(canonical_path)) from exc

    try:
        tree = ast.parse(source, filename=str(canonical_path))
    except SyntaxError as exc:
        return {
            "path": str(canonical_path),
            "language": "python",
            "supported": True,
            "parse_error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
            "symbols": [],
        }

    symbols = _extract_symbols(tree)

    return {
        "path": str(canonical_path),
        "language": "python",
        "supported": True,
        "symbol_count": len(symbols),
        "symbols": symbols,
    }


def _extract_symbols(tree: ast.Module) -> list[dict[str, object]]:
    """Walk the AST top-level and extract classes and functions."""
    symbols: list[dict[str, object]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(_class_symbol(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_function_symbol(node))

    return symbols


def _class_symbol(node: ast.ClassDef) -> dict[str, object]:
    """Extract a class definition with its methods."""
    methods: list[dict[str, object]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_function_symbol(child))

    return {
        "kind": "class",
        "name": node.name,
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
        "bases": [_name_of(base) for base in node.bases],
        "docstring": ast.get_docstring(node) or None,
        "methods": methods,
    }


def _function_symbol(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, object]:
    """Extract a function/method definition."""
    is_async = isinstance(node, ast.AsyncFunctionDef)
    args = _extract_args(node.args)

    # Detect decorators (e.g. @property, @staticmethod, @mcp.tool()).
    decorators = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorators.append(f"{_name_of(dec.value)}.{dec.attr}")
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                decorators.append(f"{dec.func.id}()")
            elif isinstance(dec.func, ast.Attribute):
                decorators.append(f"{_name_of(dec.func.value)}.{dec.func.attr}()")

    symbol: dict[str, object] = {
        "kind": "async_function" if is_async else "function",
        "name": node.name,
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
        "args": args,
        "docstring": ast.get_docstring(node) or None,
    }
    if decorators:
        symbol["decorators"] = decorators

    return symbol


def _extract_args(args: ast.arguments) -> list[str]:
    """Extract argument names from a function signature."""
    names: list[str] = []
    for arg in args.args:
        if arg.arg != "self" and arg.arg != "cls":
            names.append(arg.arg)
    return names


def _name_of(node: ast.expr) -> str:
    """Best-effort name extraction from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return "?"
