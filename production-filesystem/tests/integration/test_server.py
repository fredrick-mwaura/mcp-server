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


def test_server_exposes_only_phase1_tools(tmp_path: Path) -> None:
    async def _list():
        tools = await _server_for(tmp_path).list_tools()
        return [t.name for t in tools]

    assert asyncio.run(_list()) == ["list_directory"]


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
