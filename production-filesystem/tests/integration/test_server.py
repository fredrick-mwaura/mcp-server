"""Integration tests: drive the REAL MCPServer object through its MCP API.

This proves the whole chain works end to end without needing a live MCP
client: we build a server (like main() does), then call its tools exactly the
way opencode will - via the SDK's own `tools/list` and `tools/call`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def _call(server, tool: str, arguments: dict) -> dict:
    """Run one tools/call against the server and return its text payload."""

    async def _run():
        result = await server.call_tool(tool, arguments)
        # TextContent is the SDK's wrapper; .text holds our JSON/dict output.
        import json

        if not result.content:
            return {"error_code": "no_content", "error": str(result)}
        text = result.content[0].text
        if text is None:
            return {}
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"text": text}

    return asyncio.run(_run())


def _server_for(root: Path):
    settings = Settings(roots=[str(root)], mode="read-only", transport="stdio")
    return build_server(settings)


# =================================================================
# Phase 1 tests (list_directory) — preserved from original
# =================================================================

def test_server_exposes_phase1_and_phase2_tools(tmp_path: Path) -> None:
    async def _list():
        tools = await _server_for(tmp_path).list_tools()
        return sorted(t.name for t in tools)

    tool_names = asyncio.run(_list())
    # Phase 1 + Phase 2 tools
    assert "list_directory" in tool_names
    assert "read_file" in tool_names
    assert "find_files" in tool_names
    assert "grep_search" in tool_names
    assert "symbol_outline" in tool_names


def test_list_directory_returns_entries(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "beta.py").touch()

    result = _call(_server_for(tmp_path), "list_directory", {"path": str(tmp_path)})

    assert result["entry_count"] == 2
    assert result["path"] == str(tmp_path.resolve())
    names = [(e["name"], e["type"]) for e in result["entries"]]
    # directories sort before files, both alphabetical
    assert names == [("sub", "directory"), ("alpha.txt", "file")]


def test_list_directory_blocks_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "out.txt"
    outside.touch()

    result = _call(
        _server_for(tmp_path),
        "list_directory",
        {"path": str(outside)},
    )

    assert result["error_code"] == "path_not_allowed"


def test_list_directory_missing_path_is_typed_error(tmp_path: Path) -> None:
    result = _call(
        _server_for(tmp_path),
        "list_directory",
        {"path": str(tmp_path / "ghost")},
    )
    assert result["error_code"] == "path_not_found"


def test_list_directory_on_file_is_typed_error(tmp_path: Path) -> None:
    a_file = tmp_path / "just_a_file.txt"
    a_file.touch()
    result = _call(_server_for(tmp_path), "list_directory", {"path": str(a_file)})
    assert result["error_code"] == "path_not_a_directory"


def test_list_directory_relative_path_resolves_to_cwd(tmp_path: Path, monkeypatch) -> None:
    """Relative '.' should resolve against cwd and still obey the root gate."""
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.chdir(inside)
    # cwd is inside the allowed root -> allowed and succeeds
    result = _call(_server_for(tmp_path), "list_directory", {"path": "."})
    assert "error_code" not in result
    assert result["path"] == str(inside.resolve())
    assert result["entry_count"] == 0


# =================================================================
# Phase 2 tests: read_file
# =================================================================

def test_read_file_returns_line_numbered_content(tmp_path: Path) -> None:
    """read_file should return lines with L1:, L2:, ... prefixes."""
    f = tmp_path / "hello.py"
    f.write_text("import os\nprint('hello')\n# done\n")

    result = _call(_server_for(tmp_path), "read_file", {"path": str(f)})

    assert result["is_binary"] is False
    assert result["total_lines"] == 3
    assert result["range"]["start"] == 1
    assert result["range"]["end"] == 3
    assert result["range"]["count"] == 3
    assert "L1: import os" in result["content"]
    assert "L2: print('hello')" in result["content"]
    assert "L3: # done" in result["content"]


def test_read_file_with_offset_and_limit(tmp_path: Path) -> None:
    """read_file should respect offset and limit for surgical reading."""
    f = tmp_path / "lines.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")

    result = _call(_server_for(tmp_path), "read_file", {
        "path": str(f), "offset": 5, "limit": 3,
    })

    assert result["range"]["start"] == 5
    assert result["range"]["count"] == 3
    assert "L5: line 5" in result["content"]
    assert "L7: line 7" in result["content"]
    assert "L4:" not in result["content"]
    assert "L8:" not in result["content"]


def test_read_file_detects_binary(tmp_path: Path) -> None:
    """read_file should refuse to dump binary into the context."""
    f = tmp_path / "image.bin"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100)

    result = _call(_server_for(tmp_path), "read_file", {"path": str(f)})

    assert result["is_binary"] is True
    assert result["content"] is None


def test_read_file_on_directory_is_typed_error(tmp_path: Path) -> None:
    """read_file on a directory should return path_not_a_file error."""
    sub = tmp_path / "subdir"
    sub.mkdir()

    result = _call(_server_for(tmp_path), "read_file", {"path": str(sub)})

    assert result["error_code"] == "path_not_a_file"


def test_read_file_blocks_traversal(tmp_path: Path) -> None:
    """read_file must honor the security gate."""
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret data")

    result = _call(_server_for(tmp_path), "read_file", {"path": str(outside)})

    assert result["error_code"] == "path_not_allowed"


def test_read_file_too_large_is_typed_error(tmp_path: Path) -> None:
    """Full reads on files above the byte cap should fail with file_too_large."""
    f = tmp_path / "big.log"
    # Create a file slightly over 1 MB
    f.write_text("x" * 1_100_000)

    result = _call(_server_for(tmp_path), "read_file", {"path": str(f)})

    assert result["error_code"] == "file_too_large"


def test_read_file_large_with_offset_is_allowed(tmp_path: Path) -> None:
    """Large files can be read if the caller specifies a targeted offset+limit."""
    f = tmp_path / "big.log"
    lines = [f"log line {i}" for i in range(1, 50001)]
    f.write_text("\n".join(lines) + "\n")

    result = _call(_server_for(tmp_path), "read_file", {
        "path": str(f), "offset": 100, "limit": 10,
    })

    assert "error_code" not in result
    assert result["range"]["start"] == 100
    assert result["range"]["count"] == 10


# =================================================================
# Phase 2 tests: find_files
# =================================================================

def test_find_files_matches_glob(tmp_path: Path) -> None:
    """find_files should return matching files by glob pattern."""
    (tmp_path / "main.py").write_text("pass")
    (tmp_path / "utils.py").write_text("pass")
    (tmp_path / "readme.md").write_text("hello")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "helper.py").write_text("pass")

    result = _call(_server_for(tmp_path), "find_files", {
        "pattern": "*.py", "path": str(tmp_path),
    })

    assert result["match_count"] == 3
    names = sorted(m["name"] for m in result["matches"])
    assert names == ["helper.py", "main.py", "utils.py"]


def test_find_files_skips_hidden_dirs(tmp_path: Path) -> None:
    """find_files should skip .git, .venv, and other hidden directories."""
    (tmp_path / "visible.py").write_text("pass")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.py").write_text("pass")

    result = _call(_server_for(tmp_path), "find_files", {
        "pattern": "*.py", "path": str(tmp_path),
    })

    assert result["match_count"] == 1
    assert result["matches"][0]["name"] == "visible.py"


def test_find_files_blocks_traversal(tmp_path: Path) -> None:
    """find_files must honor the security gate."""
    result = _call(_server_for(tmp_path), "find_files", {
        "pattern": "*.py", "path": str(tmp_path.parent),
    })
    assert result["error_code"] == "path_not_allowed"


# =================================================================
# Phase 2 tests: grep_search
# =================================================================

def test_grep_search_finds_matches(tmp_path: Path) -> None:
    """grep_search should return matching lines with file paths."""
    (tmp_path / "a.py").write_text("import os\nprint('hello')\n")
    (tmp_path / "b.py").write_text("import sys\nprint('world')\n")

    result = _call(_server_for(tmp_path), "grep_search", {
        "query": "print", "path": str(tmp_path),
    })

    assert result["match_count"] >= 2
    all_texts = []
    for m in result["matches"]:
        for h in m["hits"]:
            all_texts.append(h["text"])
    assert any("hello" in t for t in all_texts)
    assert any("world" in t for t in all_texts)


def test_grep_search_respects_include_globs(tmp_path: Path) -> None:
    """grep_search with include_globs should filter by file type."""
    (tmp_path / "code.py").write_text("target_string = 42\n")
    (tmp_path / "data.txt").write_text("target_string = 99\n")

    result = _call(_server_for(tmp_path), "grep_search", {
        "query": "target_string",
        "path": str(tmp_path),
        "include_globs": ["*.py"],
    })

    assert result["match_count"] >= 1
    files = [m["file"] for m in result["matches"]]
    assert any("code.py" in f for f in files)
    assert not any("data.txt" in f for f in files)


def test_grep_search_empty_query_returns_zero_matches(tmp_path: Path) -> None:
    """Empty query should return zero matches, not an error."""
    (tmp_path / "a.py").write_text("content\n")

    result = _call(_server_for(tmp_path), "grep_search", {
        "query": "", "path": str(tmp_path),
    })

    assert result["match_count"] == 0


def test_grep_search_blocks_traversal(tmp_path: Path) -> None:
    """grep_search must honor the security gate."""
    result = _call(_server_for(tmp_path), "grep_search", {
        "query": "secret", "path": str(tmp_path.parent),
    })
    assert result["error_code"] == "path_not_allowed"


# =================================================================
# Phase 2 tests: symbol_outline
# =================================================================

def test_symbol_outline_extracts_classes_and_functions(tmp_path: Path) -> None:
    """symbol_outline should extract top-level classes and functions."""
    f = tmp_path / "module.py"
    f.write_text('''\
"""Module docstring."""

class Settings:
    """Config holder."""

    def __init__(self, x: int) -> None:
        self.x = x

    def validate(self) -> bool:
        """Check validity."""
        return self.x > 0


def helper(a, b):
    """A helper function."""
    return a + b


async def async_helper():
    pass
''')

    result = _call(_server_for(tmp_path), "symbol_outline", {"path": str(f)})

    assert result["supported"] is True
    assert result["symbol_count"] == 3  # Settings, helper, async_helper

    # Check class extraction.
    settings = next(s for s in result["symbols"] if s["name"] == "Settings")
    assert settings["kind"] == "class"
    assert len(settings["methods"]) == 2  # __init__, validate
    method_names = [m["name"] for m in settings["methods"]]
    assert "__init__" in method_names
    assert "validate" in method_names

    # Check function extraction.
    helper_sym = next(s for s in result["symbols"] if s["name"] == "helper")
    assert helper_sym["kind"] == "function"
    assert helper_sym["args"] == ["a", "b"]

    # Check async function detection.
    async_sym = next(s for s in result["symbols"] if s["name"] == "async_helper")
    assert async_sym["kind"] == "async_function"


def test_symbol_outline_non_python_returns_unsupported(tmp_path: Path) -> None:
    """symbol_outline should gracefully report non-Python files."""
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')

    result = _call(_server_for(tmp_path), "symbol_outline", {"path": str(f)})

    assert result["supported"] is False
    assert result["language"] == "json"


def test_symbol_outline_syntax_error_returns_parse_error(tmp_path: Path) -> None:
    """symbol_outline should report syntax errors instead of crashing."""
    f = tmp_path / "broken.py"
    f.write_text("def broken(\n")

    result = _call(_server_for(tmp_path), "symbol_outline", {"path": str(f)})

    assert result["supported"] is True
    assert "parse_error" in result
    assert result["symbols"] == []


def test_symbol_outline_blocks_traversal(tmp_path: Path) -> None:
    """symbol_outline must honor the security gate."""
    outside = tmp_path.parent / "secret.py"
    outside.write_text("x = 1")

    result = _call(_server_for(tmp_path), "symbol_outline", {"path": str(outside)})

    assert result["error_code"] == "path_not_allowed"
