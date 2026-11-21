use std::{collections::HashSet, path::PathBuf};

use diffy::{
    apply,
    patch_set::{FileOperation, ParseOptions, PatchSet},
};
use serde::Serialize;

use crate::{
    error::{AppError, AppResult},
    python_syntax, vfs,
    workspace::{CreatablePath, ExistingPath, WorkspaceSet},
};

#[derive(Debug, Serialize)]
pub struct PatchResult {
    pub root: String,
    pub action: &'static str,
    pub files_modified: usize,
    pub files: Vec<String>,
    pub errors: Vec<String>,
}

enum PlannedOperation {
    Write {
        relative: String,
        target: CreatablePath,
        content: String,
        previous: Option<String>,
    },
    Delete {
        relative: String,
        target: ExistingPath,
    },
}

enum CommittedOperation {
    Write {
        target: CreatablePath,
        previous: Option<String>,
    },
    Delete {
        original: PathBuf,
        trash: PathBuf,
    },
}

pub fn apply_patch(
    workspaces: &WorkspaceSet,
    base: &ExistingPath,
    patch_text: &str,
    max_bytes: usize,
) -> AppResult<PatchResult> {
    if patch_text.trim().is_empty() {
        return Ok(PatchResult {
            root: base.path().display().to_string(),
            action: "no_changes",
            files_modified: 0,
            files: Vec::new(),
            errors: Vec::new(),
        });
    }
    if patch_text.len() > max_bytes {
        return Err(AppError::invalid_argument(
            "patch",
            format!("exceeds the {max_bytes} byte write limit"),
        ));
    }

    let file_patches = PatchSet::parse(patch_text, ParseOptions::gitdiff())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| AppError::PatchConflict {
            path: "<patch>".to_owned(),
            detail: error.to_string(),
        })?;
    if file_patches.is_empty() {
        return Err(AppError::PatchConflict {
            path: "<patch>".to_owned(),
            detail: "contains no file patches".to_owned(),
        });
    }

    let plan = build_plan(workspaces, base, file_patches, max_bytes)?;
    let files = plan
        .iter()
        .map(|operation| match operation {
            PlannedOperation::Write { relative, .. }
            | PlannedOperation::Delete { relative, .. } => relative.clone(),
        })
        .collect::<Vec<_>>();
    commit(plan)?;

    Ok(PatchResult {
        root: base.path().display().to_string(),
        action: "patch_applied",
        files_modified: files.len(),
        files,
        errors: Vec::new(),
    })
}

fn build_plan(
    workspaces: &WorkspaceSet,
    base: &ExistingPath,
    file_patches: Vec<diffy::patch_set::FilePatch<'_, str>>,
    max_bytes: usize,
) -> AppResult<Vec<PlannedOperation>> {
    let mut plan = Vec::with_capacity(file_patches.len());
    let mut targets = HashSet::with_capacity(file_patches.len());
    for file_patch in file_patches {
        if matches!(
            (file_patch.old_mode(), file_patch.new_mode()),
            (Some(old), Some(new)) if old != new
        ) || file_patch
            .old_mode()
            .is_some_and(|mode| !matches!(mode, diffy::patch_set::FileMode::Regular))
            || file_patch
                .new_mode()
                .is_some_and(|mode| !matches!(mode, diffy::patch_set::FileMode::Regular))
        {
            return Err(AppError::PatchConflict {
                path: "<patch>".to_owned(),
                detail: "file mode, symlink, and submodule changes are not supported".to_owned(),
            });
        }
        let text_patch = file_patch
            .patch()
            .as_text()
            .ok_or_else(|| AppError::PatchConflict {
                path: "<binary>".to_owned(),
                detail: "binary patches are not supported".to_owned(),
            })?;
        match file_patch.operation() {
            FileOperation::Create(path) => {
                let relative = normalize_header_path(path)?;
                ensure_unique(&mut targets, &relative)?;
                let target = workspaces.resolve_creatable_under(base, &relative)?;
                if target.path().exists() {
                    return Err(conflict(&relative, "new-file target already exists"));
                }
                let content = apply("", text_patch)
                    .map_err(|error| conflict(&relative, &error.to_string()))?;
                python_syntax::validate(&content, &target.path().display().to_string())?;
                plan.push(PlannedOperation::Write {
                    relative,
                    target,
                    content,
                    previous: None,
                });
            }
            FileOperation::Delete(path) => {
                let relative = normalize_header_path(path)?;
                ensure_unique(&mut targets, &relative)?;
                let target = workspaces.resolve_existing_under(base, &relative)?;
                let original = vfs::read_text(&target, max_bytes)?;
                let result = apply(&original, text_patch)
                    .map_err(|error| conflict(&relative, &error.to_string()))?;
                if !result.is_empty() {
                    return Err(conflict(
                        &relative,
                        "delete patch did not produce an empty file",
                    ));
                }
                plan.push(PlannedOperation::Delete { relative, target });
            }
            FileOperation::Modify { original, modified } => {
                let original_relative = normalize_header_path(original)?;
                let modified_relative = normalize_header_path(modified)?;
                if original_relative != modified_relative {
                    return Err(conflict(
                        &modified_relative,
                        "renames through modify headers are not supported",
                    ));
                }
                ensure_unique(&mut targets, &original_relative)?;
                let existing = workspaces.resolve_existing_under(base, &original_relative)?;
                let original_content = vfs::read_text(&existing, max_bytes)?;
                let content = apply(&original_content, text_patch)
                    .map_err(|error| conflict(&original_relative, &error.to_string()))?;
                let target = CreatablePath::from_existing(&existing);
                python_syntax::validate(&content, &target.path().display().to_string())?;
                plan.push(PlannedOperation::Write {
                    relative: original_relative,
                    target,
                    content,
                    previous: Some(original_content),
                });
            }
            FileOperation::Rename { .. } | FileOperation::Copy { .. } => {
                return Err(AppError::PatchConflict {
                    path: "<patch>".to_owned(),
                    detail: "rename and copy patches are not supported".to_owned(),
                });
            }
        }
    }

    Ok(plan)
}

fn ensure_unique(targets: &mut HashSet<String>, path: &str) -> AppResult<()> {
    if targets.insert(path.to_owned()) {
        Ok(())
    } else {
        Err(conflict(
            path,
            "the patch contains duplicate target operations",
        ))
    }
}

fn commit(plan: Vec<PlannedOperation>) -> AppResult<()> {
    let mut committed = Vec::new();
    for operation in plan {
        let result = match operation {
            PlannedOperation::Write {
                target,
                content,
                previous,
                ..
            } => vfs::write_file_atomic(&target, &content).map(|_| {
                committed.push(CommittedOperation::Write { target, previous });
            }),
            PlannedOperation::Delete { target, .. } => vfs::move_to_trash(&target).map(|result| {
                committed.push(CommittedOperation::Delete {
                    original: target.path().to_path_buf(),
                    trash: PathBuf::from(result.trash_path),
                });
            }),
        };
        if let Err(error) = result {
            rollback(&committed)?;
            return Err(error);
        }
    }

    Ok(())
}

fn normalize_header_path(path: &str) -> AppResult<String> {
    let stripped = path
        .strip_prefix("a/")
        .or_else(|| path.strip_prefix("b/"))
        .unwrap_or(path);
    if stripped.is_empty() {
        return Err(AppError::InvalidPatchPath {
            path: path.to_owned(),
            detail: "path is empty".to_owned(),
        });
    }
    Ok(stripped.to_owned())
}

fn rollback(committed: &[CommittedOperation]) -> AppResult<()> {
    for operation in committed.iter().rev() {
        match operation {
            CommittedOperation::Write { target, previous } => {
                if let Some(content) = previous {
                    vfs::write_file_atomic(target, content)?;
                } else {
                    vfs::remove_created_file(target)?;
                }
            }
            CommittedOperation::Delete { original, trash } => {
                vfs::restore_from_trash(original, trash)?;
            }
        }
    }
    Ok(())
}

fn conflict(path: &str, detail: &str) -> AppError {
    AppError::PatchConflict {
        path: path.to_owned(),
        detail: detail.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    fn workspace() -> (tempfile::TempDir, WorkspaceSet, ExistingPath) {
        let root = tempdir().expect("workspace");
        let workspaces =
            WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace capability");
        let base = workspaces
            .resolve_existing(root.path().to_str().expect("utf8 root"))
            .expect("base");
        (root, workspaces, base)
    }

    #[test]
    fn applies_context_checked_text_patch() {
        let (root, workspaces, base) = workspace();
        fs::write(root.path().join("sample.txt"), "old\n").expect("fixture");
        let result = apply_patch(
            &workspaces,
            &base,
            "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-old\n+new\n",
            10_000,
        )
        .expect("patch applies");
        assert_eq!(result.action, "patch_applied");
        assert_eq!(
            fs::read_to_string(root.path().join("sample.txt")).expect("result"),
            "new\n"
        );
    }

    #[test]
    fn invalid_second_python_file_leaves_first_unchanged() {
        let (root, workspaces, base) = workspace();
        fs::write(root.path().join("first.py"), "x = 1\n").expect("first fixture");
        fs::write(root.path().join("second.py"), "y = 2\n").expect("second fixture");
        let patch = "diff --git a/first.py b/first.py\n--- a/first.py\n+++ b/first.py\n@@ -1 +1 @@\n-x = 1\n+x = 3\ndiff --git a/second.py b/second.py\n--- a/second.py\n+++ b/second.py\n@@ -1 +1 @@\n-y = 2\n+def broken(:\n";
        assert!(matches!(
            apply_patch(&workspaces, &base, patch, 10_000),
            Err(AppError::Syntax { .. })
        ));
        assert_eq!(
            fs::read_to_string(root.path().join("first.py")).expect("first result"),
            "x = 1\n"
        );
    }

    #[test]
    fn rejects_patch_traversal_without_touching_outside_file() {
        let (root, workspaces, base) = workspace();
        let outside_name = format!(
            "outside-{}.txt",
            root.path()
                .file_name()
                .and_then(|name| name.to_str())
                .expect("root name")
        );
        let outside = root.path().parent().expect("parent").join(&outside_name);
        fs::write(&outside, "safe\n").expect("outside fixture");
        let patch = format!(
            "diff --git a/../{outside_name} b/../{outside_name}\n--- a/../{outside_name}\n+++ b/../{outside_name}\n@@ -1 +1 @@\n-safe\n+unsafe\n"
        );
        assert!(matches!(
            apply_patch(&workspaces, &base, &patch, 10_000),
            Err(AppError::InvalidPatchPath { .. })
        ));
        assert_eq!(
            fs::read_to_string(outside).expect("outside result"),
            "safe\n"
        );
    }
}
