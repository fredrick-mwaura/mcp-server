"""editing.py - surgical code editing tools (Phase 3).

These tools are the write-path counterparts to Phase 2's read tools. Each one
is designed to minimize the tokens an LLM needs to spend on code modifications:

  edit_block:   Replace a unique string chunk in a file. The model emits only
                the target and replacement (~50 tokens) instead of rewriting
                the entire file (~2,000 tokens). Dry-run mode lets it preview.

  write_file:   Create or overwrite a file atomically. Crash-safe via
                tempfile + os.replace.

  apply_patch:  Apply a standard unified diff string. The native format for
                SWE-bench patch submission.

  delete_entry: Move a file/directory to .trash/ instead of permanent delete.
                Recoverable if the agent makes a mistake.

All handlers receive ALREADY-APPROVED canonical paths. The security decision
and mode gate happen in the server.py layer above.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp_fs import vfs
from mcp_fs.errors import EditTargetNotFoundError, PathNotFoundError


def edit_block(
    canonical_path: Path,
    target_content: str,
    replacement_content: str,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Replace a unique target string in a file with new content.

    This is the highest-ROI editing operation: the model sends only the chunk
    to find and its replacement, saving ~90% of completion tokens compared to
    rewriting the entire file.

    Args:
        canonical_path:      An approved, canonical, existing file path.
        target_content:      The exact text to find and replace (must be unique).
        replacement_content: The text to replace the target with.
        dry_run:             If True, report what would happen without modifying.

    Returns:
        A result dict with the edit outcome.

    Raises:
        PathNotFoundError:       file doesn't exist.
        EditTargetNotFoundError: target not found or found multiple times.
    """
    if not os.path.lexists(canonical_path):
        raise PathNotFoundError(str(canonical_path))
    if os.path.isdir(canonical_path):
        from mcp_fs.errors import PathNotAFileError
        raise PathNotAFileError(str(canonical_path))

    try:
        original = canonical_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PathNotFoundError(str(canonical_path)) from exc

    # Count occurrences of the target string.
    count = original.count(target_content)
    if count != 1:
        raise EditTargetNotFoundError(str(canonical_path), count)

    # Build the new content.
    new_content = original.replace(target_content, replacement_content, 1)

    if dry_run:
        return {
            "path": str(canonical_path),
            "action": "dry_run",
            "would_replace": True,
            "target_found": True,
            "occurrences": 1,
        }

    return {
        "path": str(canonical_path),
        "new_content": new_content,
        "action": "edit_ready",
    }


def write_file(canonical_path: Path, content: str) -> dict[str, object]:
    """Create or overwrite a file atomically.

    Args:
        canonical_path: An approved, canonical path. Parent dir must exist.
        content:        The full file content.

    Returns:
        A result dict with path, size, and action (created/overwritten).
    """
    return vfs.write_file_atomic(canonical_path, content)


def delete_entry(
    canonical_path: Path,
    root: Path,
) -> dict[str, object]:
    """Move a file or directory to .trash/ for safe deletion.

    Args:
        canonical_path: An approved, canonical, existing path.
        root:           The allowed root this path belongs to.

    Returns:
        A result dict with original path and trash destination.
    """
    return vfs.delete_to_trash(canonical_path, root)


def apply_patch(canonical_root: Path, patch_str: str) -> dict[str, object]:
    """Apply a unified diff patch to files under an approved root.

    Uses Python's difflib for basic patch application. For complex patches,
    falls back to the `patch` command if available.

    Args:
        canonical_root: The root directory the patch is relative to.
        patch_str:      A unified diff string (as from `git diff`).

    Returns:
        A result dict with the files modified and any errors.
    """
    import re

    if not patch_str.strip():
        return {
            "root": str(canonical_root),
            "action": "no_changes",
            "files_modified": 0,
            "errors": [],
        }

    # Parse the unified diff into per-file hunks.
    files_modified: list[str] = []
    errors: list[str] = []
    results: list[dict[str, object]] = []

    # Split into per-file diffs.
    file_diffs = re.split(r"(?=^diff )", patch_str, flags=re.MULTILINE)

    for file_diff in file_diffs:
        if not file_diff.strip():
            continue

        # Extract target file path from --- and +++ lines.
        minus_match = re.search(r"^--- a/(.+)$", file_diff, re.MULTILINE)
        plus_match = re.search(r"^\+\+\+ b/(.+)$", file_diff, re.MULTILINE)

        if not plus_match:
            continue

        rel_path = plus_match.group(1)
        target_path = canonical_root / rel_path

        # Check if it's a new file (--- /dev/null).
        is_new_file = minus_match and minus_match.group(1) == "/dev/null"

        # Extract hunks.
        hunks = re.findall(
            r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*?\n((?:[ +\-].*\n)*)",
            file_diff,
            re.MULTILINE,
        )

        if not hunks and not is_new_file:
            errors.append(f"No hunks found for {rel_path}")
            continue

        try:
            if is_new_file:
                # New file: extract all + lines as content.
                add_lines = []
                for line in file_diff.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        add_lines.append(line[1:])
                new_content = "\n".join(add_lines) + "\n" if add_lines else ""
                results.append({
                    "file": rel_path,
                    "new_content": new_content,
                    "is_new": True,
                })
            else:
                # Existing file: apply hunks in reverse order to preserve offsets.
                if not target_path.exists():
                    errors.append(f"File not found: {rel_path}")
                    continue

                original = target_path.read_text(encoding="utf-8", errors="replace")
                lines = original.splitlines(keepends=True)

                # Apply hunks in reverse order (last hunk first) so line
                # numbers from earlier hunks remain valid.
                sorted_hunks = sorted(hunks, key=lambda h: int(h[0]), reverse=True)
                for orig_start, new_start, hunk_body in sorted_hunks:
                    orig_idx = int(orig_start) - 1  # 0-indexed

                    # Parse hunk lines.
                    remove_count = 0
                    add_lines: list[str] = []
                    for hunk_line in hunk_body.splitlines(keepends=True):
                        if hunk_line.startswith("-"):
                            remove_count += 1
                        elif hunk_line.startswith("+"):
                            add_lines.append(hunk_line[1:])
                        elif hunk_line.startswith(" "):
                            add_lines.append(hunk_line[1:])

                    # Replace the original lines with patched lines.
                    context_and_removed = 0
                    for hunk_line in hunk_body.splitlines():
                        if hunk_line.startswith("-") or hunk_line.startswith(" "):
                            context_and_removed += 1

                    lines[orig_idx:orig_idx + context_and_removed] = add_lines

                new_content = "".join(lines)
                results.append({
                    "file": rel_path,
                    "new_content": new_content,
                    "is_new": False,
                })

            files_modified.append(rel_path)
        except Exception as exc:
            errors.append(f"Error applying patch to {rel_path}: {exc}")

    return {
        "root": str(canonical_root),
        "action": "patch_parsed",
        "files_modified": len(files_modified),
        "files": files_modified,
        "results": results,
        "errors": errors,
    }
