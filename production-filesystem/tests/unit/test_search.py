from pathlib import Path

import pytest

from mcp_fs.errors import InvalidArgumentError
from mcp_fs.tools import search


def test_python_search_returns_before_and_after_context(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sample.txt").write_text("before\nneedle\nafter\nlast\n")
    monkeypatch.setattr(search.shutil, "which", lambda _: None)
    result = search.grep_search(tmp_path, "needle", context_lines=1)
    assert result["match_count"] == 1
    assert [hit["line"] for hit in result["matches"][0]["hits"]] == [1, 2, 3]
    assert [hit["is_match"] for hit in result["matches"][0]["hits"]] == [False, True, False]


def test_search_skips_symlinks(tmp_path: Path, monkeypatch) -> None:
    outside = tmp_path.parent / "outside-search.txt"
    outside.write_text("needle\n")
    try:
        (tmp_path / "escape.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(search.shutil, "which", lambda _: None)
    assert search.grep_search(tmp_path, "needle")["match_count"] == 0


def test_invalid_regex_is_typed_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidArgumentError):
        search.grep_search(tmp_path, "[", regex=True)
