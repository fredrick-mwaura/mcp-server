"""git_ops.py - Git and SWE-bench lifecycle tools (Phase 4).

These tools provide token-efficient, first-class Git integration designed
for daily software development and autonomous SWE-bench workflows:

  git_status:            Structured status reporting branch, staged, unstaged,
                         and untracked files with counts.
  git_diff:              Unified diff generation with token-conscious line capping,
                         caching toggles, and path filtering.
  export_swebench_patch: Standardized `git diff HEAD` patch export matching the
                         exact format required by SWE-bench evaluation harnesses.
  revert_file:           Instant rollback of modifications to a file back to HEAD
                         (or safe cleanup if untracked).

All handlers receive ALREADY-APPROVED canonical paths from the security layer.
Write operations (like revert_file) must pass the server mode gate before execution.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from mcp_fs.errors import GitCommandError, NotAGitRepositoryError, PathNotFoundError


def _get_repo_root(canonical_path: Path) -> Path:
    """Find the root directory of the git repository enclosing canonical_path."""
    start_dir = canonical_path if canonical_path.is_dir() else canonical_path.parent
    if not os.path.lexists(start_dir):
        raise PathNotFoundError(str(start_dir))

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise GitCommandError("git", -1, "git executable not found on system PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError("git rev-parse", -1, "git command timed out") from exc

    if res.returncode != 0:
        raise NotAGitRepositoryError(str(canonical_path))

    return Path(res.stdout.strip()).resolve()


def _run_git(cmd: list[str], cwd: Path, timeout: float = 15.0) -> str:
    """Execute a git command within `cwd` and return stdout.

    Raises NotAGitRepositoryError if cwd is not in a git repo,
    or GitCommandError if execution fails.
    """
    try:
        res = subprocess.run(
            ["git", *cmd],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitCommandError("git", -1, "git executable not found on system PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(f"git {' '.join(cmd)}", -1, f"command timed out after {timeout}s") from exc

    if res.returncode != 0:
        stderr_lower = res.stderr.lower()
        if "not a git repository" in stderr_lower:
            raise NotAGitRepositoryError(str(cwd))
        raise GitCommandError(f"git {' '.join(cmd)}", res.returncode, res.stderr)

    return res.stdout


def git_status(canonical_path: Path) -> dict[str, object]:
    """Retrieve structured Git status for the repository enclosing canonical_path.

    Returns a clean, machine-readable summary of the current branch, staged
    modifications, unstaged changes, and untracked files.

    Args:
        canonical_path: An approved, canonical directory or file path.

    Returns:
        A dictionary containing branch info, change lists, counts, and cleanliness.
    """
    repo_root = _get_repo_root(canonical_path)
    output = _run_git(["status", "--porcelain=v1", "-b"], cwd=repo_root)

    branch = "unknown"
    upstream: str | None = None
    ahead = 0
    behind = 0
    staged: list[dict[str, str]] = []
    unstaged: list[dict[str, str]] = []
    untracked: list[str] = []

    lines = output.splitlines()
    if lines and lines[0].startswith("##"):
        header = lines[0][2:].strip()
        # Header formats:
        # "No commits yet on main"
        # "Initial commit on main"
        # "main...origin/main [ahead 1, behind 2]"
        # "main"
        if "..." in header:
            branch_part, rest = header.split("...", 1)
            branch = branch_part.strip()
            if " " in rest:
                upstream_part, track_part = rest.split(" ", 1)
                upstream = upstream_part.strip()
                ahead_match = re.search(r"ahead (\d+)", track_part)
                behind_match = re.search(r"behind (\d+)", track_part)
                if ahead_match:
                    ahead = int(ahead_match.group(1))
                if behind_match:
                    behind = int(behind_match.group(1))
            else:
                upstream = rest.strip()
        elif "No commits yet on " in header:
            branch = header.replace("No commits yet on ", "").strip()
        elif "Initial commit on " in header:
            branch = header.replace("Initial commit on ", "").strip()
        else:
            branch = header.strip()
        file_lines = lines[1:]
    else:
        file_lines = lines

    for line in file_lines:
        if len(line) < 3:
            continue
        code_idx = line[0]
        code_wt = line[1]
        file_path = line[3:].strip()
        if " -> " in file_path:
            # Handle renames e.g. "R  old.py -> new.py"
            file_path = file_path.split(" -> ")[-1]

        if code_idx == "?" and code_wt == "?":
            untracked.append(file_path)
        else:
            if code_idx not in (" ", "?"):
                staged.append({"path": file_path, "status": code_idx})
            if code_wt not in (" ", "?"):
                unstaged.append({"path": file_path, "status": code_wt})

    is_clean = len(staged) == 0 and len(unstaged) == 0 and len(untracked) == 0

    return {
        "repo_root": str(repo_root),
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "is_clean": is_clean,
        "counts": {
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
        },
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


def git_diff(
    canonical_path: Path,
    *,
    cached: bool = False,
    target: str | None = None,
    paths: list[str] | None = None,
    max_lines: int = 500,
) -> dict[str, object]:
    """Generate a unified git diff with token-conscious line capping.

    Args:
        canonical_path: An approved, canonical directory or file path.
        cached:         If True, diff staged changes against HEAD.
        target:         Specific revision, commit, or branch to diff against (e.g. 'HEAD').
        paths:          Optional list of relative file paths to restrict diff scope.
        max_lines:      Maximum diff lines returned to protect context window.

    Returns:
        A dictionary with diff text, stats, and truncation status.
    """
    repo_root = _get_repo_root(canonical_path)

    cmd = ["diff"]
    if cached:
        cmd.append("--cached")
    if target:
        cmd.append(target)
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    diff_output = _run_git(cmd, cwd=repo_root)

    # Compute stat summary
    stat_cmd = [*cmd, "--stat"]
    try:
        stat_output = _run_git(stat_cmd, cwd=repo_root)
    except GitCommandError:
        stat_output = ""

    lines = diff_output.splitlines()
    total_lines = len(lines)
    truncated = False

    if total_lines > max_lines:
        selected_lines = lines[:max_lines]
        selected_lines.append(
            f"\n[... Diff truncated: {total_lines} total lines exceed {max_lines} limit. "
            "Filter by specific paths or use read_file on changed files ...]"
        )
        diff_text = "\n".join(selected_lines)
        truncated = True
    else:
        diff_text = diff_output

    # Parse insertions / deletions from stat output (e.g., " 2 files changed, 10 insertions(+), 3 deletions(-)")
    files_changed = 0
    insertions = 0
    deletions = 0
    summary_match = re.search(
        r"(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
        stat_output,
    )
    if summary_match:
        files_changed = int(summary_match.group(1))
        insertions = int(summary_match.group(2)) if summary_match.group(2) else 0
        deletions = int(summary_match.group(3)) if summary_match.group(3) else 0

    return {
        "repo_root": str(repo_root),
        "cached": cached,
        "target": target or ("HEAD" if not cached else "INDEX"),
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "total_lines": total_lines,
        "truncated": truncated,
        "diff": diff_text,
    }


def export_swebench_patch(canonical_path: Path) -> dict[str, object]:
    """Export a standardized `git diff HEAD` patch for SWE-bench evaluation.

    SWE-bench benchmark evaluation harnesses apply model submissions as unified
    diff patches against the base commit. This tool exports the full working
    tree diff (both staged and unstaged tracked changes), audits for untracked
    files that would otherwise be omitted, and calculates patch statistics.

    Args:
        canonical_path: An approved, canonical directory or file path.

    Returns:
        A dictionary with the patch string, change metrics, and untracked warnings.
    """
    repo_root = _get_repo_root(canonical_path)

    # Full diff against HEAD encompasses both staged and unstaged modifications
    patch = _run_git(["diff", "HEAD"], cwd=repo_root)

    # Check for untracked files
    status_out = _run_git(["status", "--porcelain=v1"], cwd=repo_root)
    untracked = [
        line[3:].strip()
        for line in status_out.splitlines()
        if line.startswith("??")
    ]

    # Calculate statistics
    stat_out = _run_git(["diff", "HEAD", "--stat"], cwd=repo_root)
    files_changed = 0
    insertions = 0
    deletions = 0
    summary_match = re.search(
        r"(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
        stat_out,
    )
    if summary_match:
        files_changed = int(summary_match.group(1))
        insertions = int(summary_match.group(2)) if summary_match.group(2) else 0
        deletions = int(summary_match.group(3)) if summary_match.group(3) else 0

    untracked_warning = None
    if untracked:
        untracked_warning = (
            f"There are {len(untracked)} untracked file(s) that are NOT included in the patch: "
            + ", ".join(untracked[:5])
            + (". Use write_file or git add to track them if required." if len(untracked) <= 5 else ", ...")
        )

    return {
        "repo_root": str(repo_root),
        "patch": patch,
        "is_empty": len(patch.strip()) == 0,
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "untracked_files": untracked,
        "untracked_warning": untracked_warning,
    }


def revert_file(canonical_path: Path) -> dict[str, object]:
    """Revert uncommitted modifications to a file, restoring it to HEAD.

    If the file was modified or deleted in the working directory or staging,
    it is restored to HEAD. If the file is untracked, it is safely moved to
    the repository's .trash/ buffer to avoid accidental data destruction.

    Args:
        canonical_path: An approved, canonical existing path to revert.

    Returns:
        A dictionary describing the revert action taken.
    """
    repo_root = _get_repo_root(canonical_path)

    try:
        rel_path = canonical_path.relative_to(repo_root)
    except ValueError:
        rel_path = canonical_path

    # Check if the file is tracked in git
    ls_files = _run_git(["ls-files", str(rel_path)], cwd=repo_root).strip()

    if ls_files:
        # Tracked file: checkout from HEAD
        _run_git(["checkout", "HEAD", "--", str(rel_path)], cwd=repo_root)
        return {
            "path": str(canonical_path),
            "relative_path": str(rel_path),
            "action": "reverted_to_head",
            "detail": f"File '{rel_path}' successfully restored to HEAD state.",
        }
    else:
        # Untracked file: move to trash for safety
        from mcp_fs.vfs import delete_to_trash
        trash_res = delete_to_trash(canonical_path, repo_root)
        return {
            "path": str(canonical_path),
            "relative_path": str(rel_path),
            "action": "untracked_moved_to_trash",
            "detail": f"Untracked file '{rel_path}' safely moved to trash.",
            "trash_path": trash_res.get("trash_path"),
        }
