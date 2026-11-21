use std::path::Path;

use globset::Glob;
use serde::Serialize;
use walkdir::WalkDir;

use crate::{
    error::{AppError, AppResult},
    workspace::ExistingPath,
};

#[derive(Debug, Serialize)]
pub struct FileMatch {
    pub relative_path: String,
    pub name: String,
    pub size_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct FindFilesResult {
    pub root: String,
    pub pattern: String,
    pub match_count: usize,
    pub truncated: bool,
    pub matches: Vec<FileMatch>,
}

pub fn find_files(
    root: &ExistingPath,
    pattern: &str,
    max_results: usize,
    max_depth: usize,
    max_output_bytes: usize,
) -> AppResult<FindFilesResult> {
    if max_results == 0 {
        return Err(AppError::invalid_argument(
            "max_results",
            "must be at least 1",
        ));
    }
    if !root.path().is_dir() {
        return Err(AppError::PathNotDirectory(
            root.path().display().to_string(),
        ));
    }
    let matcher = Glob::new(pattern)
        .map_err(|error| AppError::invalid_argument("pattern", error.to_string()))?
        .compile_matcher();
    let mut matches = Vec::new();
    let mut output_bytes = 0usize;
    let mut output_truncated = false;
    let walker = WalkDir::new(root.path())
        .follow_links(false)
        .max_depth(max_depth)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !skip_directory(entry.path(), root.path()));

    for entry in walker {
        let entry = entry.map_err(|error| {
            AppError::io(
                error
                    .into_io_error()
                    .unwrap_or_else(|| std::io::Error::other("directory traversal failed")),
            )
        })?;
        if entry.depth() == 0 || !entry.file_type().is_file() {
            continue;
        }
        let name = entry
            .file_name()
            .to_str()
            .ok_or_else(|| AppError::invalid_argument("path", "file name is not valid UTF-8"))?;
        if !matcher.is_match(name) {
            continue;
        }
        let relative = entry
            .path()
            .strip_prefix(root.path())
            .map_err(|_| AppError::invalid_argument("path", "file escaped search root"))?;
        let relative_path = relative.display().to_string();
        let estimated_bytes = relative_path
            .len()
            .saturating_add(name.len())
            .saturating_add(96);
        if output_bytes.saturating_add(estimated_bytes) > max_output_bytes {
            output_truncated = true;
            break;
        }
        output_bytes += estimated_bytes;
        matches.push(FileMatch {
            relative_path,
            name: name.to_owned(),
            size_bytes: entry
                .metadata()
                .map_err(|error| {
                    AppError::io(
                        error
                            .into_io_error()
                            .unwrap_or_else(|| std::io::Error::other("metadata lookup failed")),
                    )
                })?
                .len(),
        });
        if matches.len() >= max_results {
            break;
        }
    }
    Ok(FindFilesResult {
        root: root.path().display().to_string(),
        pattern: pattern.to_owned(),
        match_count: matches.len(),
        truncated: matches.len() >= max_results || output_truncated,
        matches,
    })
}

fn skip_directory(path: &Path, root: &Path) -> bool {
    if path == root {
        return false;
    }
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with('.') || name == "__pycache__")
}
