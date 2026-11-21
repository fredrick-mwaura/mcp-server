use std::{fs, path::Path, process::Command};

use mcp_filesystem_server::{error::AppError, tools::git, workspace::WorkspaceSet};
use tempfile::tempdir;

#[test]
fn git_tools_report_changes_and_revert_tracked_and_untracked_files() {
    let root = tempdir().expect("repository");
    initialize_repository(root.path());
    let tracked = root.path().join("tracked.txt");
    fs::write(&tracked, "original\n").expect("tracked fixture");
    git_command(root.path(), &["add", "tracked.txt"]);
    git_command(root.path(), &["commit", "-qm", "initial"]);

    let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
    let repository = workspaces
        .resolve_existing(root.path().to_str().expect("utf8 path"))
        .expect("repository path");
    fs::write(&tracked, "changed\n").expect("modify fixture");
    let status = git::git_status(&repository, 1_000_000).expect("status");
    assert_eq!(status.counts.unstaged, 1);
    let diff = git::git_diff(&repository, false, None, &[], 500, 1_000_000).expect("diff");
    assert!(diff.diff.contains("+changed"));

    fs::remove_file(&tracked).expect("delete tracked file");
    let deleted = workspaces
        .resolve_creatable(tracked.to_str().expect("utf8 path"))
        .expect("deleted target");
    let reverted = git::revert_file(&deleted, 1_000_000).expect("revert tracked");
    assert_eq!(reverted.action, "reverted_to_head");
    assert_eq!(
        fs::read_to_string(&tracked).expect("restored"),
        "original\n"
    );

    let untracked = root.path().join("untracked.txt");
    fs::write(&untracked, "temporary\n").expect("untracked fixture");
    let approved = workspaces
        .resolve_creatable(untracked.to_str().expect("utf8 path"))
        .expect("untracked target");
    let reverted = git::revert_file(&approved, 1_000_000).expect("trash untracked");
    assert_eq!(reverted.action, "untracked_moved_to_trash");
    assert!(!untracked.exists());
    assert!(
        Path::new(reverted.trash_path.as_deref().expect("trash path")).starts_with(
            fs::canonicalize(root.path())
                .expect("canonical root")
                .join(".trash")
        )
    );
}

#[test]
fn repository_discovery_cannot_expand_above_allowed_root() {
    let repository = tempdir().expect("repository");
    initialize_repository(repository.path());
    let allowed = repository.path().join("allowed");
    fs::create_dir(&allowed).expect("allowed subdirectory");
    let workspaces = WorkspaceSet::new([allowed.clone()]).expect("workspace");
    let path = workspaces
        .resolve_existing(allowed.to_str().expect("utf8 path"))
        .expect("approved path");
    assert!(matches!(
        git::git_status(&path, 1_000_000),
        Err(AppError::GitRepositoryOutsideRoot { .. })
    ));
}

fn initialize_repository(path: &Path) {
    git_command(path, &["init", "-q"]);
    git_command(path, &["config", "user.name", "Contract Test"]);
    git_command(path, &["config", "user.email", "contract@example.invalid"]);
}

fn git_command(path: &Path, arguments: &[&str]) {
    let output = Command::new("git")
        .args(arguments)
        .current_dir(path)
        .env("GIT_TERMINAL_PROMPT", "0")
        .output()
        .expect("run git fixture command");
    assert!(
        output.status.success(),
        "git {} failed: {}",
        arguments.join(" "),
        String::from_utf8_lossy(&output.stderr)
    );
}
