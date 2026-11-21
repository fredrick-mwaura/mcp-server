use serde::Serialize;

use crate::{
    error::{AppError, AppResult},
    python_syntax, vfs,
    workspace::{CreatablePath, ExistingPath},
};

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum EditResult {
    DryRun(DryRunResult),
    Edited(EditedResult),
}

#[derive(Debug, Serialize)]
pub struct DryRunResult {
    pub path: String,
    pub action: &'static str,
    pub would_replace: bool,
    pub target_found: bool,
    pub occurrences: usize,
}

#[derive(Debug, Serialize)]
pub struct EditedResult {
    pub path: String,
    pub action: &'static str,
    pub size_bytes: u64,
}

pub fn edit_block(
    existing: &ExistingPath,
    target: &str,
    replacement: &str,
    dry_run: bool,
    max_bytes: usize,
) -> AppResult<EditResult> {
    if target.is_empty() {
        return Err(AppError::invalid_argument(
            "target_content",
            "must not be empty",
        ));
    }
    if existing.path().is_dir() {
        return Err(AppError::PathNotFile(existing.path().display().to_string()));
    }
    let original = vfs::read_text(existing, max_bytes)?;
    let occurrences = original.match_indices(target).count();
    if occurrences != 1 {
        return Err(AppError::EditTarget {
            path: existing.path().display().to_string(),
            occurrences,
        });
    }
    if dry_run {
        return Ok(EditResult::DryRun(DryRunResult {
            path: existing.path().display().to_string(),
            action: "dry_run",
            would_replace: true,
            target_found: true,
            occurrences,
        }));
    }
    let content = original.replacen(target, replacement, 1);
    python_syntax::validate(&content, &existing.path().display().to_string())?;
    let creatable = CreatablePath::from_existing(existing);
    let write = vfs::write_file_atomic(&creatable, &content)?;
    Ok(EditResult::Edited(EditedResult {
        path: write.path,
        action: "edited",
        size_bytes: write.size_bytes,
    }))
}

pub fn write_file(
    path: &CreatablePath,
    content: &str,
    max_bytes: usize,
) -> AppResult<vfs::WriteResult> {
    if content.len() > max_bytes {
        return Err(AppError::invalid_argument(
            "content",
            format!("exceeds the {max_bytes} byte write limit"),
        ));
    }
    python_syntax::validate(content, &path.path().display().to_string())?;
    vfs::write_file_atomic(path, content)
}
