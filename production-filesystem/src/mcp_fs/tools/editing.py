"""Workspace-scoped, transactional editing operations."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from mcp_fs import vfs
from mcp_fs.errors import (
    EditTargetNotFoundError,
    InvalidArgumentError,
    InvalidPatchPathError,
    PatchConflictError,
    PathNotFoundError,
)
from mcp_fs.security import resolve_patch_target, validate_python_syntax


def edit_block(
    canonical_path: Path,
    target_content: str,
    replacement_content: str,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    if not os.path.lexists(canonical_path):
        raise PathNotFoundError(str(canonical_path))
    if os.path.isdir(canonical_path):
        from mcp_fs.errors import PathNotAFileError
        raise PathNotAFileError(str(canonical_path))
    original = canonical_path.read_text(encoding="utf-8", errors="replace")
    count = original.count(target_content)
    if count != 1:
        raise EditTargetNotFoundError(str(canonical_path), count)
    if dry_run:
        return {
            "path": str(canonical_path), "action": "dry_run",
            "would_replace": True, "target_found": True, "occurrences": 1,
        }
    return {
        "path": str(canonical_path),
        "new_content": original.replace(target_content, replacement_content, 1),
        "action": "edit_ready",
    }


def write_file(canonical_path: Path, content: str) -> dict[str, object]:
    return vfs.write_file_atomic(canonical_path, content)


def delete_entry(canonical_path: Path, root: Path) -> dict[str, object]:
    return vfs.delete_to_trash(canonical_path, root)


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    relative_path: str
    operation: str
    old_content: str | None
    new_content: str | None
    old_mode: int | None


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _header_path(line: str, marker: str) -> str | None:
    raw = line[len(marker):].strip().split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    return raw


def _parse_patch(patch: str) -> list[FilePatch]:
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise InvalidArgumentError("patch", "binary patches are not supported")
    forbidden = ("rename from ", "rename to ", "copy from ", "copy to ", "old mode ", "new mode ")
    if any(line.startswith(forbidden) for line in patch.splitlines()):
        raise InvalidArgumentError("patch", "renames, copies, mode changes, and symlinks are not supported")
    for line in patch.splitlines():
        if line.startswith(("new file mode ", "deleted file mode ")) and not line.endswith("100644"):
            raise InvalidArgumentError("patch", "symlink and executable file modes are not supported")

    lines = patch.splitlines(keepends=True)
    files: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        old_path = _header_path(lines[index], "--- ")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise InvalidArgumentError("patch", "each --- header must be followed by +++")
        new_path = _header_path(lines[index], "+++ ")
        index += 1
        if old_path is None and new_path is None:
            raise InvalidPatchPathError("/dev/null", "a patch must have a source or target")
        if old_path is not None and new_path is not None and old_path != new_path:
            raise InvalidArgumentError("patch", "renames are not supported")
        hunks: list[Hunk] = []
        while index < len(lines):
            if lines[index].startswith("--- ") or lines[index].startswith("diff --git "):
                break
            match = _HUNK.match(lines[index])
            if not match:
                index += 1
                continue
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            index += 1
            body: list[str] = []
            while index < len(lines):
                line = lines[index]
                if _HUNK.match(line) or line.startswith("--- ") or line.startswith("diff --git "):
                    break
                if line.startswith("\\ No newline at end of file"):
                    if body:
                        body[-1] = body[-1].rstrip("\r\n")
                    index += 1
                    continue
                if not line or line[0] not in " +-":
                    break
                body.append(line)
                index += 1
            actual_old = sum(1 for line in body if line[0] in " -")
            actual_new = sum(1 for line in body if line[0] in " +")
            if actual_old != old_count or actual_new != new_count:
                raise InvalidArgumentError("patch", "hunk line counts do not match its header")
            hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(body)))
        if not hunks:
            raise InvalidArgumentError("patch", "file patch contains no hunks")
        files.append(FilePatch(old_path, new_path, tuple(hunks)))
    if not files:
        raise InvalidArgumentError("patch", "no unified-diff file headers found")
    return files


def _apply_hunks(path: str, original: str, hunks: tuple[Hunk, ...]) -> str:
    source = original.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = max(0, hunk.old_start - 1)
        if start < cursor or start > len(source):
            raise PatchConflictError(path, f"invalid hunk location {hunk.old_start}")
        output.extend(source[cursor:start])
        position = start
        for raw in hunk.lines:
            marker, text = raw[0], raw[1:]
            if marker in " -":
                if position >= len(source) or source[position] != text:
                    raise PatchConflictError(path, f"context mismatch near source line {position + 1}")
                if marker == " ":
                    output.append(source[position])
                position += 1
            elif marker == "+":
                output.append(text)
        cursor = position
    output.extend(source[cursor:])
    return "".join(output)


def _build_plan(
    root: Path,
    patches: list[FilePatch],
    allowed_roots: Sequence[str | Path],
    max_write_bytes: int,
) -> list[PlannedChange]:
    plan: list[PlannedChange] = []
    seen: set[Path] = set()
    for file_patch in patches:
        relative = file_patch.new_path or file_patch.old_path
        assert relative is not None
        is_create = file_patch.old_path is None
        is_delete = file_patch.new_path is None
        target = resolve_patch_target(root, relative, allowed_roots, must_exist=not is_create)
        if is_create and target.exists():
            raise PatchConflictError(relative, "create target already exists")
        if not is_create and not target.is_file():
            raise PatchConflictError(relative, "patch targets must be regular files")
        if target in seen:
            raise InvalidArgumentError("patch", f"duplicate target '{relative}'")
        seen.add(target)
        old_content = None if is_create else target.read_text(encoding="utf-8")
        new_content = _apply_hunks(relative, old_content or "", file_patch.hunks)
        if is_delete:
            if new_content:
                raise PatchConflictError(relative, "deletion patch did not remove the complete file")
            operation = "deleted"
            final_content = None
        else:
            operation = "created" if is_create else "modified"
            final_content = new_content
            if len(final_content.encode("utf-8")) > max_write_bytes:
                raise InvalidArgumentError("patch", f"result for '{relative}' exceeds the write limit")
            validate_python_syntax(final_content, target)
        old_mode = None if is_create else target.stat().st_mode & 0o7777
        plan.append(PlannedChange(target, relative, operation, old_content, final_content, old_mode))
    return plan


def apply_patch(
    canonical_root: Path,
    patch_str: str,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
    max_write_bytes: int = 5_000_000,
) -> dict[str, object]:
    """Preflight and atomically commit the supported unified-diff subset."""
    if not patch_str.strip():
        return {"root": str(canonical_root), "action": "no_changes", "files_modified": 0, "files": [], "errors": []}
    plan = _build_plan(canonical_root, _parse_patch(patch_str), allowed_roots or [canonical_root], max_write_bytes)
    committed: list[PlannedChange] = []
    created_directories: set[Path] = set()
    try:
        for change in plan:
            if change.new_content is None:
                change.path.unlink()
            else:
                parent = change.path.parent
                while not parent.exists() and parent != canonical_root:
                    created_directories.add(parent)
                    parent = parent.parent
                vfs.ensure_parent_directory(change.path)
                vfs.write_file_atomic(change.path, change.new_content)
            committed.append(change)
    except Exception:
        for change in reversed(committed):
            try:
                if change.old_content is None:
                    change.path.unlink(missing_ok=True)
                else:
                    vfs.ensure_parent_directory(change.path)
                    vfs.write_file_atomic(change.path, change.old_content)
                    if change.old_mode is not None:
                        change.path.chmod(change.old_mode)
            except Exception:
                pass
        for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return {
        "root": str(canonical_root),
        "action": "patch_applied",
        "files_modified": len(plan),
        "files": [change.relative_path for change in plan],
        "operations": [{"file": change.relative_path, "action": change.operation} for change in plan],
        "errors": [],
    }
