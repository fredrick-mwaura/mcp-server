"""Unit tests for git_ops - Git and SWE-bench lifecycle operations.

Validates git_status, git_diff, export_swebench_patch, and revert_file
in temporary Git repositories without running the full MCP server.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mcp_fs.errors import NotAGitRepositoryError
from mcp_fs.tools.git_ops import (
    export_swebench_patch,
    git_diff,
    git_status,
    revert_file,
)


def _init_git_repo(repo_path: Path) -> None:
    """Initialize a git repo with user configuration and an initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_path), check=True, capture_output=True)

    # Initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    (repo_path / "main.py").write_text("def hello():\n    return 'world'\n")
    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_path), check=True, capture_output=True)


def test_not_a_git_repo_raises(tmp_path: Path) -> None:
    """Invoking git operations on a non-git directory raises NotAGitRepositoryError."""
    with pytest.raises(NotAGitRepositoryError):
        git_status(tmp_path)


def test_git_status_clean(tmp_path: Path) -> None:
    """Clean repository reports is_clean=True and 0 change counts."""
    _init_git_repo(tmp_path)
    status = git_status(tmp_path)

    assert status["is_clean"] is True
    assert status["branch"] == "main"
    assert status["counts"]["staged"] == 0
    assert status["counts"]["unstaged"] == 0
    assert status["counts"]["untracked"] == 0


def test_git_status_tracks_changes(tmp_path: Path) -> None:
    """git_status accurately categorizes staged, unstaged, and untracked files."""
    _init_git_repo(tmp_path)

    # Staged change
    staged_file = tmp_path / "staged.txt"
    staged_file.write_text("staged content\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=str(tmp_path), check=True, capture_output=True)

    # Unstaged modification
    (tmp_path / "main.py").write_text("def hello():\n    return 'changed'\n")

    # Untracked file
    (tmp_path / "new_untracked.py").write_text("x = 1\n")

    status = git_status(tmp_path)

    assert status["is_clean"] is False
    assert status["counts"]["staged"] == 1
    assert status["counts"]["unstaged"] == 1
    assert status["counts"]["untracked"] == 1
    assert any(s["path"] == "staged.txt" for s in status["staged"])
    assert any(s["path"] == "main.py" for s in status["unstaged"])
    assert "new_untracked.py" in status["untracked"]


def test_git_diff_unstaged_and_staged(tmp_path: Path) -> None:
    """git_diff produces diff against worktree and against index when cached=True."""
    _init_git_repo(tmp_path)

    # Make unstaged change
    (tmp_path / "main.py").write_text("def hello():\n    return 'modified'\n")

    res_unstaged = git_diff(tmp_path)
    assert res_unstaged["files_changed"] == 1
    assert "return 'modified'" in res_unstaged["diff"]
    assert "-    return 'world'" in res_unstaged["diff"]
    assert res_unstaged["truncated"] is False

    # Stage it
    subprocess.run(["git", "add", "main.py"], cwd=str(tmp_path), check=True, capture_output=True)

    # Unstaged should now be empty
    res_empty = git_diff(tmp_path)
    assert res_empty["files_changed"] == 0

    # Cached should capture it
    res_cached = git_diff(tmp_path, cached=True)
    assert res_cached["files_changed"] == 1
    assert "return 'modified'" in res_cached["diff"]


def test_git_diff_max_lines_capping(tmp_path: Path) -> None:
    """git_diff enforces max_lines cap to prevent token explosion."""
    _init_git_repo(tmp_path)

    large_content = "\n".join(f"line_{i} = {i}" for i in range(100)) + "\n"
    (tmp_path / "main.py").write_text(large_content)

    res = git_diff(tmp_path, max_lines=15)
    assert res["truncated"] is True
    assert "[... Diff truncated:" in res["diff"]


def test_export_swebench_patch(tmp_path: Path) -> None:
    """export_swebench_patch exports full diff against HEAD and detects untracked files."""
    _init_git_repo(tmp_path)

    # Modify file
    (tmp_path / "main.py").write_text("def hello():\n    return 'swebench_fix'\n")

    # Add untracked file
    (tmp_path / "scratch.py").write_text("temp = True\n")

    res = export_swebench_patch(tmp_path)
    assert res["is_empty"] is False
    assert res["files_changed"] == 1
    assert "return 'swebench_fix'" in res["patch"]
    assert "scratch.py" in res["untracked_files"]
    assert res["untracked_warning"] is not None


def test_revert_file_tracked(tmp_path: Path) -> None:
    """revert_file restores a modified tracked file to HEAD."""
    _init_git_repo(tmp_path)

    main_py = tmp_path / "main.py"
    main_py.write_text("broken code\n")

    res = revert_file(main_py)
    assert res["action"] == "reverted_to_head"
    assert main_py.read_text() == "def hello():\n    return 'world'\n"


def test_revert_file_untracked_moves_to_trash(tmp_path: Path) -> None:
    """revert_file on an untracked file safely moves it to .trash/."""
    _init_git_repo(tmp_path)

    untracked = tmp_path / "untracked.py"
    untracked.write_text("new stuff\n")

    res = revert_file(untracked)
    assert res["action"] == "untracked_moved_to_trash"
    assert not untracked.exists()
    assert Path(res["trash_path"]).exists()
