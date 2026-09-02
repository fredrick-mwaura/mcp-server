"""Integration tests for Phase 3 write tools via the real MCPServer API.

These tests verify that:
  - Write tools work correctly in read-write mode.
  - Write tools are blocked in read-only mode.
  - The AST syntax guard prevents invalid Python from being written.
  - Security gates are enforced for all write operations.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def _call(server, tool: str, arguments: dict) -> dict:
    """Run one tools/call against the server and return its text payload."""
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
    """Build a read-write mode server for testing write tools."""
    settings = Settings(roots=[str(root)], mode="read-write", transport="stdio")
    return build_server(settings)


def _ro_server(root: Path):
    """Build a read-only mode server for testing mode gate."""
    settings = Settings(roots=[str(root)], mode="read-only", transport="stdio")
    return build_server(settings)


# =================================================================
# Mode Gate Tests: read-only blocks all write tools
# =================================================================

def test_edit_block_blocked_in_readonly(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    result = _call(_ro_server(tmp_path), "edit_block", {
        "path": str(f), "target_content": "x = 1", "replacement_content": "x = 2",
    })
    assert result["error_code"] == "read_only_mode"


def test_write_file_blocked_in_readonly(tmp_path: Path) -> None:
    result = _call(_ro_server(tmp_path), "write_file", {
        "path": str(tmp_path / "new.py"), "content": "x = 1\n",
    })
    assert result["error_code"] == "read_only_mode"


def test_delete_entry_blocked_in_readonly(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("content\n")
    result = _call(_ro_server(tmp_path), "delete_entry", {"path": str(f)})
    assert result["error_code"] == "read_only_mode"


def test_apply_patch_blocked_in_readonly(tmp_path: Path) -> None:
    result = _call(_ro_server(tmp_path), "apply_patch", {
        "patch": "some patch", "path": str(tmp_path),
    })
    assert result["error_code"] == "read_only_mode"


# =================================================================
# edit_block tests (read-write mode)
# =================================================================

def test_edit_block_replaces_unique_target(tmp_path: Path) -> None:
    """edit_block should replace the target string exactly once."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\ny = 2\nz = 3\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "y = 2",
        "replacement_content": "y = 42",
    })

    assert result["action"] == "edited"
    assert f.read_text() == "x = 1\ny = 42\nz = 3\n"


def test_edit_block_dry_run_does_not_modify(tmp_path: Path) -> None:
    """edit_block with dry_run=True should not touch the file."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "x = 1",
        "replacement_content": "x = 99",
        "dry_run": True,
    })

    assert result["action"] == "dry_run"
    assert f.read_text() == "x = 1\n"  # unchanged


def test_edit_block_no_match_returns_error(tmp_path: Path) -> None:
    """edit_block should return error when target is not found."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "NOT_IN_FILE",
        "replacement_content": "replacement",
    })

    assert result["error_code"] == "edit_target_error"


def test_edit_block_multiple_matches_returns_error(tmp_path: Path) -> None:
    """edit_block should refuse when target appears more than once."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\nx = 1\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "x = 1",
        "replacement_content": "x = 2",
    })

    assert result["error_code"] == "edit_target_error"


def test_edit_block_syntax_guard_blocks_bad_edit(tmp_path: Path) -> None:
    """edit_block should refuse edits that introduce syntax errors."""
    f = tmp_path / "valid.py"
    f.write_text("def foo():\n    return 1\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "def foo():\n    return 1",
        "replacement_content": "def foo(\n    return 1",  # broken syntax
    })

    assert result["error_code"] == "syntax_error"
    # File should be unchanged
    assert f.read_text() == "def foo():\n    return 1\n"


def test_edit_block_non_python_skips_syntax_check(tmp_path: Path) -> None:
    """edit_block on non-Python files should skip syntax validation."""
    f = tmp_path / "data.txt"
    f.write_text("old value\n")

    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(f),
        "target_content": "old value",
        "replacement_content": "new value",
    })

    assert result["action"] == "edited"
    assert f.read_text() == "new value\n"


def test_edit_block_blocks_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.py"
    outside.write_text("x = 1\n")
    result = _call(_rw_server(tmp_path), "edit_block", {
        "path": str(outside),
        "target_content": "x = 1",
        "replacement_content": "x = 2",
    })
    assert result["error_code"] == "path_not_allowed"


# =================================================================
# write_file tests (read-write mode)
# =================================================================

def test_write_file_creates_new(tmp_path: Path) -> None:
    """write_file should create a new file."""
    f = tmp_path / "brand_new.py"

    result = _call(_rw_server(tmp_path), "write_file", {
        "path": str(f), "content": "x = 42\n",
    })

    assert result["action"] == "created"
    assert f.read_text() == "x = 42\n"


def test_write_file_overwrites_existing(tmp_path: Path) -> None:
    """write_file should overwrite an existing file."""
    f = tmp_path / "existing.py"
    f.write_text("old\n")

    result = _call(_rw_server(tmp_path), "write_file", {
        "path": str(f), "content": "new\n",
    })

    assert result["action"] == "overwritten"
    assert f.read_text() == "new\n"


def test_write_file_syntax_guard_blocks_bad_python(tmp_path: Path) -> None:
    """write_file should refuse invalid Python."""
    f = tmp_path / "bad.py"

    result = _call(_rw_server(tmp_path), "write_file", {
        "path": str(f), "content": "def broken(\n",
    })

    assert result["error_code"] == "syntax_error"
    assert not f.exists()  # file should NOT be created


def test_write_file_blocks_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "new.py"
    result = _call(_rw_server(tmp_path), "write_file", {
        "path": str(outside), "content": "x = 1\n",
    })
    assert result["error_code"] == "path_not_allowed"


# =================================================================
# delete_entry tests (read-write mode)
# =================================================================

def test_delete_entry_moves_to_trash(tmp_path: Path) -> None:
    """delete_entry should move the file to .trash/."""
    f = tmp_path / "doomed.txt"
    f.write_text("delete me\n")

    result = _call(_rw_server(tmp_path), "delete_entry", {"path": str(f)})

    assert result["action"] == "moved_to_trash"
    assert not f.exists()
    assert Path(result["trash_path"]).exists()


def test_delete_entry_blocks_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope\n")
    result = _call(_rw_server(tmp_path), "delete_entry", {"path": str(outside)})
    assert result["error_code"] == "path_not_allowed"


# =================================================================
# apply_patch tests (read-write mode)
# =================================================================

def test_apply_patch_modifies_file(tmp_path: Path) -> None:
    """apply_patch should apply a unified diff to an existing file."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\ny = 2\nz = 3\n")

    patch = (
        "diff --git a/code.py b/code.py\n"
        "--- a/code.py\n"
        "+++ b/code.py\n"
        "@@ -1,3 +1,3 @@\n"
        " x = 1\n"
        "-y = 2\n"
        "+y = 42\n"
        " z = 3\n"
    )

    result = _call(_rw_server(tmp_path), "apply_patch", {
        "patch": patch, "path": str(tmp_path),
    })

    assert result["action"] == "patch_applied"
    assert result["files_modified"] == 1
    assert f.read_text() == "x = 1\ny = 42\nz = 3\n"


def test_apply_patch_empty_is_noop(tmp_path: Path) -> None:
    """An empty patch should produce no changes."""
    result = _call(_rw_server(tmp_path), "apply_patch", {
        "patch": "", "path": str(tmp_path),
    })
    assert result["action"] == "no_changes" or result.get("files_modified", 0) == 0
