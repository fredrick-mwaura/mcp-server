"""Unit tests for VFS write operations (Phase 3).

Tests atomic writes, crash safety, and safe trash deletion in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_fs.errors import PathNotFoundError
from mcp_fs.vfs import write_file_atomic, delete_to_trash


# =================================================================
# write_file_atomic
# =================================================================

def test_atomic_write_creates_new_file(tmp_path: Path) -> None:
    """Writing to a non-existent path should create the file."""
    target = tmp_path / "new_file.py"
    result = write_file_atomic(target, "print('hello')\n")

    assert target.exists()
    assert target.read_text() == "print('hello')\n"
    assert result["is_new"] is True
    assert result["action"] == "created"


def test_atomic_write_overwrites_existing_file(tmp_path: Path) -> None:
    """Writing to an existing file should overwrite it atomically."""
    target = tmp_path / "existing.py"
    target.write_text("old content\n")

    result = write_file_atomic(target, "new content\n")

    assert target.read_text() == "new content\n"
    assert result["is_new"] is False
    assert result["action"] == "overwritten"


def test_atomic_write_no_temp_file_left(tmp_path: Path) -> None:
    """After a successful write, no temp files should remain."""
    target = tmp_path / "clean.txt"
    write_file_atomic(target, "content\n")

    # Check no .mcpfs-tmp files remain.
    temp_files = list(tmp_path.glob("*.mcpfs-tmp-*"))
    assert temp_files == []


def test_atomic_write_missing_parent_raises(tmp_path: Path) -> None:
    """Writing to a path whose parent doesn't exist should raise."""
    target = tmp_path / "nonexistent_dir" / "file.txt"

    with pytest.raises(PathNotFoundError):
        write_file_atomic(target, "content\n")


def test_atomic_write_preserves_encoding(tmp_path: Path) -> None:
    """Unicode content should be preserved through atomic write."""
    target = tmp_path / "unicode.txt"
    content = "Hello 🌍\nCafé résumé naïve\n日本語テキスト\n"
    write_file_atomic(target, content)

    assert target.read_text(encoding="utf-8") == content


# =================================================================
# delete_to_trash
# =================================================================

def test_trash_moves_file(tmp_path: Path) -> None:
    """Deleting a file should move it to .trash/ under the root."""
    target = tmp_path / "doomed.txt"
    target.write_text("delete me\n")

    result = delete_to_trash(target, tmp_path)

    assert not target.exists()
    assert result["action"] == "moved_to_trash"
    trash_path = Path(result["trash_path"])
    assert trash_path.exists()
    assert trash_path.read_text() == "delete me\n"


def test_trash_moves_directory(tmp_path: Path) -> None:
    """Deleting a directory should move the entire tree to .trash/."""
    target_dir = tmp_path / "subdir"
    target_dir.mkdir()
    (target_dir / "file.txt").write_text("inside\n")

    result = delete_to_trash(target_dir, tmp_path)

    assert not target_dir.exists()
    trash_path = Path(result["trash_path"])
    assert trash_path.is_dir()
    assert (trash_path / "file.txt").read_text() == "inside\n"


def test_trash_creates_trash_dir(tmp_path: Path) -> None:
    """The .trash/ directory should be created automatically."""
    target = tmp_path / "file.txt"
    target.write_text("content\n")

    assert not (tmp_path / ".trash").exists()
    delete_to_trash(target, tmp_path)
    assert (tmp_path / ".trash").is_dir()


def test_trash_nonexistent_raises(tmp_path: Path) -> None:
    """Trashing a non-existent path should raise PathNotFoundError."""
    with pytest.raises(PathNotFoundError):
        delete_to_trash(tmp_path / "ghost.txt", tmp_path)


def test_trash_no_collision(tmp_path: Path) -> None:
    """Deleting two files with the same name should not collide in trash."""
    f1 = tmp_path / "file.txt"
    f1.write_text("first\n")
    result1 = delete_to_trash(f1, tmp_path)

    f2 = tmp_path / "file.txt"
    f2.write_text("second\n")
    result2 = delete_to_trash(f2, tmp_path)

    assert result1["trash_path"] != result2["trash_path"]
    assert Path(result1["trash_path"]).read_text() == "first\n"
    assert Path(result2["trash_path"]).read_text() == "second\n"
