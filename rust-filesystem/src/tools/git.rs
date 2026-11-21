use std::{
    fmt::Write as _,
    path::{Component, Path, PathBuf},
    time::Duration,
};

use regex::Regex;
use serde::Serialize;

use crate::{
    error::{AppError, AppResult},
    process::{self, ProcessOutput},
    vfs,
    workspace::{CreatablePath, ExistingPath},
};

const GIT_TIMEOUT: Duration = Duration::from_secs(15);
const REPO_OUTPUT_LIMIT: usize = 16 * 1024;

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Change {
    pub path: String,
    pub status: String,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct ChangeCounts {
    pub staged: usize,
    pub unstaged: usize,
    pub untracked: usize,
}

#[derive(Debug, Serialize)]
pub struct GitStatusResult {
    pub repo_root: String,
    pub branch: String,
    pub upstream: Option<String>,
    pub ahead: usize,
    pub behind: usize,
    pub is_clean: bool,
    pub counts: ChangeCounts,
    pub staged: Vec<Change>,
    pub unstaged: Vec<Change>,
    pub untracked: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct GitDiffResult {
    pub repo_root: String,
    pub cached: bool,
    pub target: String,
    pub files_changed: usize,
    pub insertions: usize,
    pub deletions: usize,
    pub total_lines: usize,
    pub truncated: bool,
    pub diff: String,
}

#[derive(Debug, Serialize)]
pub struct SwebenchPatchResult {
    pub repo_root: String,
    pub patch: String,
    pub is_empty: bool,
    pub files_changed: usize,
    pub insertions: usize,
    pub deletions: usize,
    pub untracked_files: Vec<String>,
    pub untracked_warning: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct RevertResult {
    pub path: String,
    pub relative_path: String,
    pub action: &'static str,
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trash_path: Option<String>,
}

struct Repository {
    root: PathBuf,
}

pub fn git_status(path: &ExistingPath, max_output_bytes: usize) -> AppResult<GitStatusResult> {
    let repository = discover_repository(path.path(), path.root())?;
    let output = run_git(
        &["status", "--porcelain=v1", "-b"],
        &repository.root,
        max_output_bytes,
    )?;
    require_complete(&output, "git status")?;
    Ok(parse_status(&repository.root, &output.stdout))
}

pub fn git_diff(
    path: &ExistingPath,
    cached: bool,
    target: Option<&str>,
    paths: &[String],
    max_lines: usize,
    max_output_bytes: usize,
) -> AppResult<GitDiffResult> {
    let repository = discover_repository(path.path(), path.root())?;
    validate_target(target)?;
    validate_pathspecs(paths)?;

    let args = diff_arguments(cached, target, paths, false);
    let output = run_git_owned(&args, &repository.root, max_output_bytes)?;
    let stat_args = diff_arguments(cached, target, paths, true);
    let stat = run_git_owned(&stat_args, &repository.root, 64 * 1024)?;
    require_complete(&stat, "git diff --stat")?;
    let (files_changed, insertions, deletions) = parse_stats(&stat.stdout);

    let retained_lines = output.stdout.lines().take(max_lines).collect::<Vec<_>>();
    let line_truncated = output.stdout_total_lines > retained_lines.len();
    let truncated = output.stdout_truncated || line_truncated;
    let mut diff = if line_truncated {
        retained_lines.join("\n")
    } else {
        output.stdout
    };
    if truncated {
        let _ = write!(
            diff,
            "\n\n[... Diff truncated: {} total lines; limits are {} lines and {} bytes. Filter by paths for a smaller diff. ...]",
            output.stdout_total_lines, max_lines, max_output_bytes
        );
    }

    Ok(GitDiffResult {
        repo_root: repository.root.display().to_string(),
        cached,
        target: target.map_or_else(
            || if cached { "INDEX" } else { "HEAD" }.to_owned(),
            str::to_owned,
        ),
        files_changed,
        insertions,
        deletions,
        total_lines: output.stdout_total_lines,
        truncated,
        diff,
    })
}

pub fn export_swebench_patch(
    path: &ExistingPath,
    max_output_bytes: usize,
) -> AppResult<SwebenchPatchResult> {
    let repository = discover_repository(path.path(), path.root())?;
    let patch = run_git(&["diff", "HEAD"], &repository.root, max_output_bytes)?;
    if patch.stdout_truncated {
        return Err(AppError::invalid_argument(
            "patch",
            format!(
                "git diff HEAD is {} bytes and exceeds MCP_FS_MAX_OUTPUT_BYTES={max_output_bytes}",
                patch.stdout_total_bytes
            ),
        ));
    }
    let status = run_git(
        &["status", "--porcelain=v1"],
        &repository.root,
        max_output_bytes,
    )?;
    require_complete(&status, "git status")?;
    let untracked_files = status
        .stdout
        .lines()
        .filter_map(|line| line.strip_prefix("?? "))
        .map(str::trim)
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let stat = run_git(&["diff", "HEAD", "--stat"], &repository.root, 64 * 1024)?;
    require_complete(&stat, "git diff --stat")?;
    let (files_changed, insertions, deletions) = parse_stats(&stat.stdout);
    let untracked_warning = untracked_warning(&untracked_files);

    Ok(SwebenchPatchResult {
        repo_root: repository.root.display().to_string(),
        is_empty: patch.stdout.trim().is_empty(),
        patch: patch.stdout,
        files_changed,
        insertions,
        deletions,
        untracked_files,
        untracked_warning,
    })
}

pub fn revert_file(path: &CreatablePath, max_output_bytes: usize) -> AppResult<RevertResult> {
    let start = if path.path().exists() {
        path.path()
    } else {
        path.path()
            .parent()
            .ok_or_else(|| AppError::invalid_argument("path", "revert target must have a parent"))?
    };
    let repository = discover_repository(start, path.root())?;
    let relative = path.path().strip_prefix(&repository.root).map_err(|_| {
        AppError::GitRepositoryOutsideRoot {
            repo: repository.root.display().to_string(),
            root: path.root().display().to_string(),
        }
    })?;
    validate_pathspec(relative)?;
    let relative_text = relative.to_string_lossy().into_owned();
    let tracked = run_git_owned(
        &[
            "ls-files".to_owned(),
            "--error-unmatch".to_owned(),
            "--".to_owned(),
            relative_text.clone(),
        ],
        &repository.root,
        max_output_bytes,
    );
    if tracked.is_ok() {
        run_git_owned(
            &[
                "checkout".to_owned(),
                "HEAD".to_owned(),
                "--".to_owned(),
                relative_text.clone(),
            ],
            &repository.root,
            max_output_bytes,
        )?;
        return Ok(RevertResult {
            path: path.path().display().to_string(),
            relative_path: relative_text.clone(),
            action: "reverted_to_head",
            detail: format!("File '{relative_text}' successfully restored to HEAD state."),
            trash_path: None,
        });
    }
    let existing = path.to_existing()?;
    if existing.path().is_dir() {
        return Err(AppError::PathNotFile(existing.path().display().to_string()));
    }
    let trashed = vfs::move_to_trash(&existing)?;
    Ok(RevertResult {
        path: path.path().display().to_string(),
        relative_path: relative_text.clone(),
        action: "untracked_moved_to_trash",
        detail: format!("Untracked file '{relative_text}' safely moved to trash."),
        trash_path: Some(trashed.trash_path),
    })
}

fn discover_repository(path: &Path, owning_root: &Path) -> AppResult<Repository> {
    let start = if path.is_dir() {
        path
    } else {
        path.parent()
            .ok_or_else(|| AppError::NotGitRepository(path.display().to_string()))?
    };
    let output = process::run_bounded(
        "git",
        &["rev-parse".to_owned(), "--show-toplevel".to_owned()],
        start,
        Duration::from_secs(10),
        REPO_OUTPUT_LIMIT,
        &[("GIT_TERMINAL_PROMPT", "0"), ("LC_ALL", "C")],
    )
    .map_err(|error| map_git_spawn(error, "git rev-parse"))?;
    if !output.status.success() {
        return Err(AppError::NotGitRepository(path.display().to_string()));
    }
    require_complete(&output, "git rev-parse")?;
    let repository = std::fs::canonicalize(output.stdout.trim()).map_err(AppError::io)?;
    if !repository.starts_with(owning_root) {
        return Err(AppError::GitRepositoryOutsideRoot {
            repo: repository.display().to_string(),
            root: owning_root.display().to_string(),
        });
    }
    Ok(Repository { root: repository })
}

fn run_git(arguments: &[&str], cwd: &Path, limit: usize) -> AppResult<ProcessOutput> {
    let owned = arguments
        .iter()
        .map(|argument| (*argument).to_owned())
        .collect::<Vec<_>>();
    run_git_owned(&owned, cwd, limit)
}

fn run_git_owned(arguments: &[String], cwd: &Path, limit: usize) -> AppResult<ProcessOutput> {
    let command = format!("git {}", arguments.join(" "));
    let output = process::run_bounded(
        "git",
        arguments,
        cwd,
        GIT_TIMEOUT,
        limit,
        &[("GIT_TERMINAL_PROMPT", "0"), ("LC_ALL", "C")],
    )
    .map_err(|error| map_git_spawn(error, &command))?;
    if output.timed_out {
        return Err(git_error(&command, -1, "command timed out"));
    }
    if !output.status.success() {
        return Err(git_error(
            &command,
            output.status.code().unwrap_or(-1),
            &output.stderr,
        ));
    }
    Ok(output)
}

fn require_complete(output: &ProcessOutput, command: &str) -> AppResult<()> {
    if output.stdout_truncated || output.stderr_truncated {
        return Err(git_error(
            command,
            -1,
            "command output exceeded the configured byte limit",
        ));
    }
    Ok(())
}

fn map_git_spawn(error: AppError, command: &str) -> AppError {
    match error {
        AppError::Io(source) if source.kind() == std::io::ErrorKind::NotFound => {
            git_error(command, -1, "git executable not found on system PATH")
        }
        other => other,
    }
}

fn git_error(command: &str, return_code: i32, detail: &str) -> AppError {
    AppError::GitCommand {
        command: command.to_owned(),
        return_code,
        detail: sanitize_detail(detail),
    }
}

fn sanitize_detail(detail: &str) -> String {
    let trimmed = detail.trim();
    if trimmed.len() <= 4096 {
        trimmed.to_owned()
    } else {
        format!("{}…", &trimmed[..4096])
    }
}

fn parse_status(repo_root: &Path, output: &str) -> GitStatusResult {
    let mut lines = output.lines();
    let header = lines.next().unwrap_or_default();
    let (branch, upstream, ahead, behind) = parse_branch(header);
    let mut staged = Vec::new();
    let mut unstaged = Vec::new();
    let mut untracked = Vec::new();
    for line in lines {
        let bytes = line.as_bytes();
        if bytes.len() < 3 {
            continue;
        }
        let index = char::from(bytes[0]);
        let working_tree = char::from(bytes[1]);
        let mut file = line[3..].trim();
        if let Some((_, renamed)) = file.rsplit_once(" -> ") {
            file = renamed;
        }
        if index == '?' && working_tree == '?' {
            untracked.push(file.to_owned());
            continue;
        }
        if !matches!(index, ' ' | '?') {
            staged.push(Change {
                path: file.to_owned(),
                status: index.to_string(),
            });
        }
        if !matches!(working_tree, ' ' | '?') {
            unstaged.push(Change {
                path: file.to_owned(),
                status: working_tree.to_string(),
            });
        }
    }
    let counts = ChangeCounts {
        staged: staged.len(),
        unstaged: unstaged.len(),
        untracked: untracked.len(),
    };
    GitStatusResult {
        repo_root: repo_root.display().to_string(),
        branch,
        upstream,
        ahead,
        behind,
        is_clean: counts.staged == 0 && counts.unstaged == 0 && counts.untracked == 0,
        counts,
        staged,
        unstaged,
        untracked,
    }
}

fn parse_branch(header: &str) -> (String, Option<String>, usize, usize) {
    let value = header.strip_prefix("## ").unwrap_or(header);
    if let Some(branch) = value.strip_prefix("No commits yet on ") {
        return (branch.trim().to_owned(), None, 0, 0);
    }
    if let Some(branch) = value.strip_prefix("Initial commit on ") {
        return (branch.trim().to_owned(), None, 0, 0);
    }
    let Some((branch, tracking)) = value.split_once("...") else {
        return (value.trim().to_owned(), None, 0, 0);
    };
    let (upstream, state) = tracking
        .split_once(' ')
        .map_or((tracking, ""), |(upstream, state)| (upstream, state));
    (
        branch.trim().to_owned(),
        Some(upstream.trim().to_owned()),
        tracking_count(state, "ahead"),
        tracking_count(state, "behind"),
    )
}

fn tracking_count(value: &str, label: &str) -> usize {
    let expression = Regex::new(&format!(r"{label} (\d+)")).expect("static tracking regex");
    expression
        .captures(value)
        .and_then(|captures| captures.get(1))
        .and_then(|value| value.as_str().parse().ok())
        .unwrap_or(0)
}

fn diff_arguments(cached: bool, target: Option<&str>, paths: &[String], stat: bool) -> Vec<String> {
    let mut arguments = vec!["diff".to_owned()];
    if cached {
        arguments.push("--cached".to_owned());
    }
    if stat {
        arguments.push("--stat".to_owned());
    }
    if let Some(target) = target {
        arguments.push(target.to_owned());
    }
    if !paths.is_empty() {
        arguments.push("--".to_owned());
        arguments.extend_from_slice(paths);
    }
    arguments
}

fn validate_target(target: Option<&str>) -> AppResult<()> {
    if let Some(target) = target
        && (target.is_empty() || target.starts_with('-') || target.contains('\0'))
    {
        return Err(AppError::invalid_argument(
            "target",
            "must be a non-empty revision and must not begin with '-'",
        ));
    }
    Ok(())
}

fn validate_pathspecs(paths: &[String]) -> AppResult<()> {
    for path in paths {
        validate_pathspec(Path::new(path))?;
    }
    Ok(())
}

fn validate_pathspec(path: &Path) -> AppResult<()> {
    let text = path.to_string_lossy();
    if text.is_empty()
        || text.starts_with(['/', '\\', ':'])
        || text.contains('\\')
        || text.as_bytes().get(1).is_some_and(|byte| *byte == b':')
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(AppError::invalid_argument(
            "paths",
            format!("unsafe Git pathspec: {text}"),
        ));
    }
    Ok(())
}

fn parse_stats(output: &str) -> (usize, usize, usize) {
    let expression = Regex::new(
        r"(?m)(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
    )
    .expect("static stats regex");
    let Some(captures) = expression.captures(output) else {
        return (0, 0, 0);
    };
    let number = |index| {
        captures
            .get(index)
            .and_then(|value| value.as_str().parse().ok())
            .unwrap_or(0)
    };
    (number(1), number(2), number(3))
}

fn untracked_warning(files: &[String]) -> Option<String> {
    if files.is_empty() {
        return None;
    }
    let shown = files.iter().take(5).cloned().collect::<Vec<_>>().join(", ");
    let suffix = if files.len() <= 5 {
        ". Use write_file or git add to track them if required."
    } else {
        ", ..."
    };
    Some(format!(
        "There are {} untracked file(s) that are NOT included in the patch: {shown}{suffix}",
        files.len()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_status_sections() {
        let status = parse_status(
            Path::new("/repo"),
            "## main...origin/main [ahead 2, behind 1]\nM  staged.rs\n M unstaged.rs\n?? new.rs\n",
        );
        assert_eq!(status.branch, "main");
        assert_eq!(status.upstream.as_deref(), Some("origin/main"));
        assert_eq!((status.ahead, status.behind), (2, 1));
        assert_eq!(status.counts.staged, 1);
        assert_eq!(status.counts.unstaged, 1);
        assert_eq!(status.untracked, ["new.rs"]);
    }

    #[test]
    fn rejects_git_option_and_magic_injection() {
        assert!(validate_target(Some("--output=/tmp/x")).is_err());
        assert!(validate_pathspecs(&["../outside".to_owned()]).is_err());
        assert!(validate_pathspecs(&[":(top)outside".to_owned()]).is_err());
    }

    #[test]
    fn parses_stat_summary() {
        assert_eq!(
            parse_stats(" 2 files changed, 10 insertions(+), 3 deletions(-)\n"),
            (2, 10, 3)
        );
    }
}
