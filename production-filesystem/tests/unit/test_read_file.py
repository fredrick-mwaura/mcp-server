"""Unit tests for VFS read_file_slice.

Tests the line-slicing, binary detection, and size-cap logic in isolation
(no MCP, no server, no security gate). These validate the core token-saving
behavior that makes the server cheap to operate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_fs.errors import FileTooLargeError, PathNotAFileError, PathNotFoundError
from mcp_fs.vfs import read_file_slice


def test_read_slice_basic(tmp_path: Path) -> None:
    """Basic read returns all lines with L-prefixed line numbers."""
    f = tmp_path / "hello.py"
    f.write_text("import os\nprint('hi')\n# end\n")

    result = read_file_slice(f)

    assert result["is_binary"] is False
    assert result["total_lines"] == 3
    assert result["range"]["start"] == 1
    assert result["range"]["end"] == 3
    assert result["range"]["count"] == 3
    assert "L1: import os" in result["content"]
    assert "L2: print('hi')" in result["content"]
    assert "L3: # end" in result["content"]


def test_read_slice_offset_limit(tmp_path: Path) -> None:
    """Offset and limit select a precise window of lines."""
    f = tmp_path / "lines.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")

    result = read_file_slice(f, offset=3, limit=4)

    assert result["range"]["start"] == 3
    assert result["range"]["end"] == 6
    assert result["range"]["count"] == 4
    assert "L3: line3" in result["content"]
    assert "L6: line6" in result["content"]
    assert "L2:" not in result["content"]
    assert "L7:" not in result["content"]


def test_read_slice_offset_past_end(tmp_path: Path) -> None:
    """Offset past end of file returns empty content with correct metadata."""
    f = tmp_path / "short.txt"
    f.write_text("one\ntwo\n")

    result = read_file_slice(f, offset=100, limit=10)

    assert result["total_lines"] == 2
    assert result["range"]["count"] == 0
    assert result["content"] == ""


def test_read_slice_binary_detection(tmp_path: Path) -> None:
    """Binary files are detected and return is_binary=True with no content."""
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    result = read_file_slice(f)

    assert result["is_binary"] is True
    assert result["content"] is None


def test_read_slice_size_cap_on_full_read(tmp_path: Path) -> None:
    """Full reads on files above the byte cap should raise FileTooLargeError."""
    f = tmp_path / "big.log"
    f.write_text("x" * 2_000_000)

    with pytest.raises(FileTooLargeError) as exc_info:
        read_file_slice(f, max_read_bytes=1_000_000)

    assert exc_info.value.size_bytes == 2_000_000
    assert exc_info.value.max_bytes == 1_000_000


def test_read_slice_size_cap_bypassed_with_offset(tmp_path: Path) -> None:
    """Targeted reads (offset != 1 or small limit) bypass the size cap."""
    f = tmp_path / "big.log"
    lines = [f"log {i}" for i in range(1, 10001)]
    f.write_text("\n".join(lines) + "\n")

    # This file is large but we're reading a small slice.
    result = read_file_slice(f, offset=100, limit=5, max_read_bytes=100)

    assert result["range"]["start"] == 100
    assert result["range"]["count"] == 5


def test_read_slice_not_found(tmp_path: Path) -> None:
    """Reading a non-existent file raises PathNotFoundError."""
    with pytest.raises(PathNotFoundError):
        read_file_slice(tmp_path / "ghost.txt")


def test_read_slice_on_directory(tmp_path: Path) -> None:
    """Reading a directory raises PathNotAFileError."""
    sub = tmp_path / "subdir"
    sub.mkdir()

    with pytest.raises(PathNotAFileError):
        read_file_slice(sub)


def test_read_slice_empty_file(tmp_path: Path) -> None:
    """Empty files return total_lines=0 and empty content."""
    f = tmp_path / "empty.txt"
    f.write_text("")

    result = read_file_slice(f)

    assert result["total_lines"] == 0
    assert result["range"]["count"] == 0
    assert result["content"] == ""


def test_read_slice_preserves_indentation(tmp_path: Path) -> None:
    """Line-numbered output must preserve leading whitespace exactly."""
    f = tmp_path / "indented.py"
    f.write_text("class Foo:\n    def bar(self):\n        pass\n")

    result = read_file_slice(f)

    assert "L2:     def bar(self):" in result["content"]
    assert "L3:         pass" in result["content"]
