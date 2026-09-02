"""Unit tests for the security path gate.

These test resolve_allowed_path in ISOLATION (no MCP, no server). Each test
name documents the attack it is proving we defend against - this file doubles
as living documentation of the threat model (PRODUCTION_PLAN §4.4).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_fs.errors import PathNotAllowedError
from mcp_fs.security import resolve_allowed_path


def test_allows_path_inside_root(tmp_path: Path) -> None:
    """The happy path: a normal file under the workspace is allowed."""
    target = tmp_path / "docs" / "notes.txt"
    target.parent.mkdir()
    target.touch()

    resolved = resolve_allowed_path(str(target), [str(tmp_path)], must_exist=True)

    assert resolved == target.resolve()


def test_allows_the_root_itself(tmp_path: Path) -> None:
    """Operating on the root directory is legal (equality case in _is_within)."""
    resolved = resolve_allowed_path(str(tmp_path), [str(tmp_path)], must_exist=True)
    assert resolved == tmp_path.resolve()


def test_blocks_traversal_out_of_root(tmp_path: Path) -> None:
    """Attack 1: ../../.. must never escape the workspace."""
    sibling = tmp_path.parent / "secret.txt"
    sibling.touch()

    escape = os.path.join(str(tmp_path), "..", "..", "secret.txt")
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(escape, [str(tmp_path)])


def test_blocks_sibling_prefix_confusion(tmp_path: Path) -> None:
    """Attack 2: /dataevil is NOT inside /data - string prefix is not enough."""
    allowed = tmp_path / "data"
    allowed.mkdir()
    evil = tmp_path / "dataevil"  # sibling that merely *looks* nested
    evil.mkdir()
    (evil / "pwned.txt").touch()

    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(str(evil / "pwned.txt"), [str(allowed)], must_exist=True)


def test_blocks_symlink_escape(tmp_path: Path) -> None:
    """Attack 3: a symlink inside the root that points outside must be refused."""
    outside = tmp_path.parent / "outside_target_dir"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.txt"
    secret.touch()

    link = tmp_path / "innocent_link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(str(link / "secret.txt"), [str(tmp_path)], must_exist=True)


def test_allows_symlink_that_stays_inside_root(tmp_path: Path) -> None:
    """A symlink resolving to a path INSIDE the workspace is fine."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").touch()
    link = tmp_path / "alias"
    link.symlink_to(real_dir, target_is_directory=True)

    resolved = resolve_allowed_path(str(link / "file.txt"), [str(tmp_path)], must_exist=True)
    assert resolved == (real_dir / "file.txt").resolve()


def test_resolves_tilde_and_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """'~' expands to the home dir; relative paths resolve against the cwd."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    (home / "x.txt").touch()
    assert resolve_allowed_path("~/x.txt", [str(home)], must_exist=True) == (home / "x.txt").resolve()

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "y.txt").touch()
    assert resolve_allowed_path("./proj/y.txt", [str(proj)], must_exist=True) == (proj / "y.txt").resolve()


def test_must_exist_raises_for_missing_path(tmp_path: Path) -> None:
    with pytest.raises(Exception) as excinfo:
        resolve_allowed_path(str(tmp_path / "nope"), [str(tmp_path)], must_exist=True)
    assert excinfo.type.__name__ == "PathNotFoundError"


def test_rejects_nul_byte(tmp_path: Path) -> None:
    """NUL bytes are a smuggling trick and can never name a real file."""
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(f"{tmp_path}/evil\x00.txt", [str(tmp_path)])


def test_denied_error_reports_allowed_roots(tmp_path: Path) -> None:
    """The error message must name the allowed roots so the model can recover."""
    outside = tmp_path / "x.txt"
    outside.touch()
    with pytest.raises(PathNotAllowedError) as excinfo:
        resolve_allowed_path(str(outside), [str(tmp_path / "allowed")])
    message = str(excinfo.value)
    assert "allowed" in message
    assert "path_not_allowed" == excinfo.value.code
