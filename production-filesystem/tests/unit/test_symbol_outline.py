"""Unit tests for symbol_outline AST extraction.

Tests the structural-map logic in isolation: class extraction, method
enumeration, function arguments, async detection, decorator handling,
and graceful error reporting.
"""

from __future__ import annotations

from pathlib import Path

from mcp_fs.tools.outline import symbol_outline


def test_extracts_top_level_function(tmp_path: Path) -> None:
    """A top-level function should appear in the symbols list."""
    f = tmp_path / "mod.py"
    f.write_text("def greet(name: str) -> str:\n    return f'Hello {name}'\n")

    result = symbol_outline(f)

    assert result["supported"] is True
    assert result["symbol_count"] == 1
    sym = result["symbols"][0]
    assert sym["kind"] == "function"
    assert sym["name"] == "greet"
    assert sym["args"] == ["name"]
    assert sym["line_start"] == 1


def test_extracts_class_with_methods(tmp_path: Path) -> None:
    """A class should include its methods as children."""
    f = tmp_path / "models.py"
    f.write_text('''\
class User:
    """A user model."""

    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hi, {self.name}"
''')

    result = symbol_outline(f)

    assert result["symbol_count"] == 1
    cls = result["symbols"][0]
    assert cls["kind"] == "class"
    assert cls["name"] == "User"
    assert cls["docstring"] == "A user model."
    assert len(cls["methods"]) == 2
    method_names = [m["name"] for m in cls["methods"]]
    assert "__init__" in method_names
    assert "greet" in method_names


def test_extracts_async_function(tmp_path: Path) -> None:
    """Async functions should have kind='async_function'."""
    f = tmp_path / "async_mod.py"
    f.write_text("async def fetch(url):\n    pass\n")

    result = symbol_outline(f)

    sym = result["symbols"][0]
    assert sym["kind"] == "async_function"
    assert sym["name"] == "fetch"
    assert sym["args"] == ["url"]


def test_extracts_decorators(tmp_path: Path) -> None:
    """Decorated functions should list their decorators."""
    f = tmp_path / "decorated.py"
    f.write_text("import functools\n\n@functools.cache\ndef expensive():\n    pass\n")

    result = symbol_outline(f)

    sym = result["symbols"][0]
    assert sym["name"] == "expensive"
    assert "decorators" in sym
    assert "functools.cache" in sym["decorators"]


def test_extracts_class_bases(tmp_path: Path) -> None:
    """Class bases should be reported."""
    f = tmp_path / "child.py"
    f.write_text("class Child(Parent, Mixin):\n    pass\n")

    result = symbol_outline(f)

    cls = result["symbols"][0]
    assert cls["bases"] == ["Parent", "Mixin"]


def test_non_python_file_unsupported(tmp_path: Path) -> None:
    """Non-Python files should return supported=False."""
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')

    result = symbol_outline(f)

    assert result["supported"] is False
    assert result["language"] == "json"


def test_syntax_error_returns_parse_error(tmp_path: Path) -> None:
    """Syntax errors should be reported gracefully, not crash."""
    f = tmp_path / "broken.py"
    f.write_text("def broken(\n")

    result = symbol_outline(f)

    assert result["supported"] is True
    assert "parse_error" in result
    assert result["symbols"] == []


def test_empty_file(tmp_path: Path) -> None:
    """An empty Python file should return zero symbols."""
    f = tmp_path / "empty.py"
    f.write_text("")

    result = symbol_outline(f)

    assert result["supported"] is True
    assert result["symbol_count"] == 0
    assert result["symbols"] == []


def test_self_and_cls_filtered_from_args(tmp_path: Path) -> None:
    """'self' and 'cls' should be excluded from reported args."""
    f = tmp_path / "methods.py"
    f.write_text('''\
class Foo:
    def instance_method(self, x, y):
        pass

    @classmethod
    def class_method(cls, z):
        pass
''')

    result = symbol_outline(f)

    cls = result["symbols"][0]
    inst_method = next(m for m in cls["methods"] if m["name"] == "instance_method")
    cls_method = next(m for m in cls["methods"] if m["name"] == "class_method")

    assert inst_method["args"] == ["x", "y"]
    assert cls_method["args"] == ["z"]
