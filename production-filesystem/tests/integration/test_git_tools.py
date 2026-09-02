"""Integration tests for Git & SWE-bench tools via the real MCPServer API.

Validates:
  - git_status, git_diff, export_swebench_patch, and revert_file work via call_tool.
  - Mode gate: revert_file is blocked in read-only mode, allowed in read-write mode.
  - Security gate: path traversal outside allowed root is blocked for all git tools.
  - Non-git directory returns structured not_a_git_repo error.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def _call(server, tool: str, arguments: dict) -> dict:
    """Run one tools/call against the server and return its parsed dictionary."""
    async def _run():
        result = await server.call_tool(tool, arguments)
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


def _rw_server(root: Path):
    settings = Settings(roots=[str(root)], mode="read-write", transport="stdio")
    return build_server(settings)


def _ro_server(root: Path):
    settings = Settings(roots=[str(root)], mode="read-only", transport="stdio")
    return build_server(settings)


def _init_git_repo(repo_path: Path) -> None:
    """Initialize a git repository with an initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "runner@example.com"], cwd=str(repo_path), check=True, capture_output=True)

    (repo_path / "app.py").write_text("def solve():\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_path), check=True, capture_output=True)


# =================================================================
# git_status integration tests
# =================================================================

def test_git_status_via_mcp(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("def solve():\n    return 42\n")

    server = _ro_server(tmp_path)
    res = _call(server, "git_status", {"path": str(tmp_path)})

    assert res["is_clean"] is False
    assert res["counts"]["unstaged"] == 1
    assert any(u["path"] == "app.py" for u in res["unstaged"])


def test_git_status_not_a_repo_error(tmp_path: Path) -> None:
    server = _ro_server(tmp_path)
    res = _call(server, "git_status", {"path": str(tmp_path)})

    assert res["error_code"] == "not_a_git_repo"


def test_git_status_blocks_traversal(tmp_path: Path) -> None:
    server = _ro_server(tmp_path)
    res = _call(server, "git_status", {"path": str(tmp_path.parent)})

    assert res["error_code"] == "path_not_allowed"


# =================================================================
# git_diff integration tests
# =================================================================

def test_git_diff_via_mcp(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("def solve():\n    return 42\n")

    server = _ro_server(tmp_path)
    res = _call(server, "git_diff", {"path": str(tmp_path)})

    assert res["files_changed"] == 1
    assert "+    return 42" in res["diff"]


def test_git_diff_blocks_traversal(tmp_path: Path) -> None:
    server = _ro_server(tmp_path)
    res = _call(server, "git_diff", {"path": str(tmp_path.parent)})

    assert res["error_code"] == "path_not_allowed"


# =================================================================
# export_swebench_patch integration tests
# =================================================================

def test_export_swebench_patch_via_mcp(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("def solve():\n    return 100\n")
    (tmp_path / "extra.py").write_text("extra = True\n")

    server = _ro_server(tmp_path)
    res = _call(server, "export_swebench_patch", {"path": str(tmp_path)})

    assert res["is_empty"] is False
    assert res["files_changed"] == 1
    assert "return 100" in res["patch"]
    assert "extra.py" in res["untracked_files"]
    assert res["untracked_warning"] is not None


def test_export_swebench_patch_blocks_traversal(tmp_path: Path) -> None:
    server = _ro_server(tmp_path)
    res = _call(server, "export_swebench_patch", {"path": str(tmp_path.parent)})

    assert res["error_code"] == "path_not_allowed"


# =================================================================
# revert_file integration & mode gate tests
# =================================================================

def test_revert_file_blocked_in_readonly(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    app_py = tmp_path / "app.py"
    app_py.write_text("modified\n")

    server = _ro_server(tmp_path)
    res = _call(server, "revert_file", {"path": str(app_py)})

    assert res["error_code"] == "read_only_mode"
    # Content must remain untouched
    assert app_py.read_text() == "modified\n"


def test_revert_file_succeeds_in_readwrite(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    app_py = tmp_path / "app.py"
    app_py.write_text("modified\n")

    server = _rw_server(tmp_path)
    res = _call(server, "revert_file", {"path": str(app_py)})

    assert res["action"] == "reverted_to_head"
    assert app_py.read_text() == "def solve():\n    return 0\n"


def test_revert_file_blocks_traversal(tmp_path: Path) -> None:
    server = _rw_server(tmp_path)
    res = _call(server, "revert_file", {"path": str(tmp_path.parent / "secret.py")})

    assert res["error_code"] == "path_not_allowed"
