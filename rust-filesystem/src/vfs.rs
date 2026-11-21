use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{BufRead, BufReader, Read, Write},
    path::Path,
};

use serde::Serialize;
use tempfile::NamedTempFile;
use uuid::Uuid;

use crate::{
    error::{AppError, AppResult},
    workspace::{CreatablePath, ExistingPath},
};

#[derive(Debug, Serialize)]
pub struct DirectoryEntry {
    pub name: String,
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Serialize)]
pub struct DirectoryListing {
    pub path: String,
    pub entry_count: usize,
    pub entries: Vec<DirectoryEntry>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,
}

#[derive(Debug, Serialize)]
pub struct LineRange {
    pub start: usize,
    pub end: usize,
    pub count: usize,
}

#[derive(Debug, Serialize)]
pub struct ReadFileResult {
    pub path: String,
    pub is_binary: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_lines: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub range: Option<LineRange>,
    pub size_bytes: u64,
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub truncated_by_bytes: bool,
}

#[derive(Debug, Serialize)]
pub struct WriteResult {
    pub path: String,
    pub size_bytes: u64,
    pub is_new: bool,
    pub action: &'static str,
}

#[derive(Debug, Serialize)]
pub struct TrashResult {
    pub path: String,
    pub trash_path: String,
    pub action: &'static str,
}

pub fn list_directory(path: &ExistingPath, max_output_bytes: usize) -> AppResult<DirectoryListing> {
    if !path.path().is_dir() {
        return Err(AppError::PathNotDirectory(
            path.path().display().to_string(),
        ));
    }
    let mut retained = BTreeMap::new();
    let mut entry_count = 0usize;
    let mut retained_bytes = 0usize;
    let mut truncated = false;
    for entry in fs::read_dir(path.path()).map_err(AppError::io)? {
        let entry = entry.map_err(AppError::io)?;
        entry_count += 1;
        let metadata = fs::symlink_metadata(entry.path()).map_err(AppError::io)?;
        let file_type = metadata.file_type();
        let (kind, size_bytes) = if file_type.is_symlink() {
            ("symlink", Some(metadata.len()))
        } else if file_type.is_dir() {
            ("directory", None)
        } else {
            ("file", Some(metadata.len()))
        };
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| AppError::invalid_argument("path", "entry name is not valid UTF-8"))?;
        let estimated_bytes = name.len().saturating_mul(2).saturating_add(96);
        retained_bytes = retained_bytes.saturating_add(estimated_bytes);
        let key = (kind_rank(kind), name.to_lowercase(), name.clone());
        retained.insert(
            key,
            DirectoryEntry {
                name,
                kind,
                size_bytes,
            },
        );
        while retained_bytes > max_output_bytes {
            let Some((_, removed)) = retained.pop_last() else {
                break;
            };
            retained_bytes = retained_bytes
                .saturating_sub(removed.name.len().saturating_mul(2).saturating_add(96));
            truncated = true;
        }
    }
    let entries = retained.into_values().collect();
    Ok(DirectoryListing {
        path: path.path().display().to_string(),
        entry_count,
        entries,
        truncated,
    })
}

pub fn read_file(
    path: &ExistingPath,
    offset: usize,
    limit: usize,
    max_read_bytes: u64,
    max_output_bytes: usize,
    default_read_lines: usize,
) -> AppResult<ReadFileResult> {
    if offset == 0 {
        return Err(AppError::invalid_argument("offset", "must be at least 1"));
    }
    if limit == 0 {
        return Err(AppError::invalid_argument("limit", "must be at least 1"));
    }
    if path.path().is_dir() {
        return Err(AppError::PathNotFile(path.path().display().to_string()));
    }
    let metadata = fs::metadata(path.path()).map_err(AppError::io)?;
    let requesting_full_read = offset == 1 && limit >= default_read_lines;
    if requesting_full_read && metadata.len() > max_read_bytes {
        return Err(AppError::FileTooLarge {
            path: path.path().display().to_string(),
            size_bytes: metadata.len(),
            max_bytes: max_read_bytes,
        });
    }
    if is_binary(path)? {
        return Ok(ReadFileResult {
            path: path.path().display().to_string(),
            is_binary: true,
            total_lines: None,
            range: None,
            size_bytes: metadata.len(),
            content: None,
            note: Some("Binary file detected. Use get_metadata for file info.".to_owned()),
            truncated_by_bytes: false,
        });
    }

    let file = File::open(path.path()).map_err(AppError::io)?;
    let slice = read_line_slice(BufReader::new(file), offset, limit, max_output_bytes)?;
    let count = slice.selected.len();
    let end = if count == 0 {
        slice.total_lines.min(offset.saturating_sub(1))
    } else {
        offset + count - 1
    };
    Ok(ReadFileResult {
        path: path.path().display().to_string(),
        is_binary: false,
        total_lines: Some(slice.total_lines),
        range: Some(LineRange {
            start: offset,
            end,
            count,
        }),
        size_bytes: metadata.len(),
        content: Some(slice.selected.join("\n")),
        note: None,
        truncated_by_bytes: slice.truncated_by_bytes,
    })
}

struct SliceRead {
    total_lines: usize,
    selected: Vec<String>,
    truncated_by_bytes: bool,
}

fn read_line_slice(
    mut reader: impl BufRead,
    offset: usize,
    limit: usize,
    max_output_bytes: usize,
) -> AppResult<SliceRead> {
    let mut total_lines = 0usize;
    let mut selected = Vec::new();
    let mut output_bytes = 0usize;
    let mut truncated_by_bytes = false;
    let mut line_buffer = Vec::new();
    let mut line_has_bytes = false;
    loop {
        let available = reader.fill_buf().map_err(AppError::io)?;
        if available.is_empty() {
            if line_has_bytes {
                finish_line(
                    &mut total_lines,
                    &mut line_buffer,
                    offset,
                    limit,
                    max_output_bytes,
                    &mut output_bytes,
                    &mut selected,
                    &mut truncated_by_bytes,
                );
            }
            break;
        }
        let consumed = available.len();
        for byte in available {
            line_has_bytes = true;
            if *byte == b'\n' {
                finish_line(
                    &mut total_lines,
                    &mut line_buffer,
                    offset,
                    limit,
                    max_output_bytes,
                    &mut output_bytes,
                    &mut selected,
                    &mut truncated_by_bytes,
                );
                line_has_bytes = false;
            } else if total_lines + 1 >= offset && selected.len() < limit {
                let remaining = max_output_bytes.saturating_sub(output_bytes);
                if line_buffer.len() < remaining {
                    line_buffer.push(*byte);
                } else {
                    truncated_by_bytes = true;
                }
            }
        }
        reader.consume(consumed);
    }
    Ok(SliceRead {
        total_lines,
        selected,
        truncated_by_bytes,
    })
}

#[allow(clippy::too_many_arguments)]
fn finish_line(
    total_lines: &mut usize,
    line_buffer: &mut Vec<u8>,
    offset: usize,
    limit: usize,
    max_output_bytes: usize,
    output_bytes: &mut usize,
    selected: &mut Vec<String>,
    truncated: &mut bool,
) {
    *total_lines += 1;
    if *total_lines < offset || selected.len() >= limit {
        line_buffer.clear();
        return;
    }
    let separator = usize::from(!selected.is_empty());
    let prefix = format!("L{total_lines}: ");
    let available = max_output_bytes.saturating_sub(*output_bytes + separator);
    if available < prefix.len() {
        *truncated = true;
        line_buffer.clear();
        return;
    }
    let text_limit = available - prefix.len();
    let text = String::from_utf8_lossy(line_buffer);
    let trimmed = text.trim_end();
    let text = truncate_utf8(trimmed, text_limit);
    *truncated |= text.len() < trimmed.len();
    let formatted = format!("{prefix}{text}");
    *output_bytes += separator + formatted.len();
    selected.push(formatted);
    line_buffer.clear();
}

fn truncate_utf8(value: &str, max_bytes: usize) -> &str {
    if value.len() <= max_bytes {
        return value;
    }
    let mut end = max_bytes;
    while end > 0 && !value.is_char_boundary(end) {
        end -= 1;
    }
    &value[..end]
}

fn is_binary(path: &ExistingPath) -> AppResult<bool> {
    let mut file = File::open(path.path()).map_err(AppError::io)?;
    let mut probe = [0u8; 8192];
    let count = file.read(&mut probe).map_err(AppError::io)?;
    Ok(probe[..count].contains(&0))
}

fn kind_rank(kind: &str) -> u8 {
    match kind {
        "directory" => 0,
        "file" => 1,
        "symlink" => 2,
        _ => 9,
    }
}

pub fn write_file_atomic(path: &CreatablePath, content: &str) -> AppResult<WriteResult> {
    let target = path.path();
    if target.is_dir() {
        return Err(AppError::PathNotFile(target.display().to_string()));
    }
    let parent = target
        .parent()
        .ok_or_else(|| AppError::invalid_argument("path", "target must have a parent directory"))?;
    if !parent.is_dir() {
        return Err(AppError::PathNotFound(parent.display().to_string()));
    }
    let is_new = !target.exists();
    let existing_permissions = if is_new {
        None
    } else {
        Some(fs::metadata(target).map_err(AppError::io)?.permissions())
    };
    let mut temporary = NamedTempFile::new_in(parent).map_err(AppError::io)?;
    temporary
        .as_file_mut()
        .write_all(content.as_bytes())
        .map_err(AppError::io)?;
    temporary.as_file_mut().flush().map_err(AppError::io)?;
    if let Some(permissions) = existing_permissions {
        temporary
            .as_file_mut()
            .set_permissions(permissions)
            .map_err(AppError::io)?;
    }
    temporary.as_file().sync_all().map_err(AppError::io)?;
    temporary
        .persist(target)
        .map_err(|error| AppError::io(error.error))?;
    #[cfg(unix)]
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(AppError::io)?;
    let metadata = fs::metadata(target).map_err(AppError::io)?;
    Ok(WriteResult {
        path: target.display().to_string(),
        size_bytes: metadata.len(),
        is_new,
        action: if is_new { "created" } else { "overwritten" },
    })
}

pub fn move_to_trash(path: &ExistingPath) -> AppResult<TrashResult> {
    if path.path() == path.root() {
        return Err(AppError::invalid_argument(
            "path",
            "the configured workspace root cannot be deleted",
        ));
    }
    let trash = path.root().join(".trash");
    if path.path() == trash || path.path().starts_with(&trash) {
        return Err(AppError::invalid_argument(
            "path",
            "entries inside the workspace trash cannot be trashed again",
        ));
    }
    fs::create_dir_all(&trash).map_err(AppError::io)?;
    let name = path
        .path()
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| AppError::invalid_argument("path", "file name is not valid UTF-8"))?;
    let destination = trash.join(format!("{name}.{}", Uuid::new_v4().simple()));
    fs::rename(path.path(), &destination).map_err(AppError::io)?;
    Ok(TrashResult {
        path: path.path().display().to_string(),
        trash_path: destination.display().to_string(),
        action: "moved_to_trash",
    })
}

pub fn read_text(path: &ExistingPath, max_bytes: usize) -> AppResult<String> {
    if path.path().is_dir() {
        return Err(AppError::PathNotFile(path.path().display().to_string()));
    }
    let metadata = fs::metadata(path.path()).map_err(AppError::io)?;
    if metadata.len() > max_bytes as u64 {
        return Err(AppError::FileTooLarge {
            path: path.path().display().to_string(),
            size_bytes: metadata.len(),
            max_bytes: max_bytes as u64,
        });
    }
    fs::read_to_string(path.path()).map_err(AppError::io)
}

pub fn path_is_python(path: &Path) -> bool {
    path.extension()
        .and_then(|suffix| suffix.to_str())
        .is_some_and(|suffix| matches!(suffix.to_ascii_lowercase().as_str(), "py" | "pyw"))
}

pub fn remove_created_file(path: &CreatablePath) -> AppResult<()> {
    if path.path().exists() {
        fs::remove_file(path.path()).map_err(AppError::io)?;
    }
    Ok(())
}

pub fn restore_from_trash(original: &Path, trash: &Path) -> AppResult<()> {
    if trash.exists() {
        fs::rename(trash, original).map_err(AppError::io)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use tempfile::tempdir;

    use super::*;
    use crate::workspace::WorkspaceSet;

    #[test]
    fn huge_line_is_streamed_into_bounded_output() {
        let mut content = vec![b'x'; 1_000_000];
        content.extend_from_slice(b"\nsmall\n");
        let result = read_line_slice(Cursor::new(content), 1, 2, 128).expect("slice");
        assert_eq!(result.total_lines, 2);
        assert!(result.truncated_by_bytes);
        assert!(result.selected.join("\n").len() <= 128);
    }

    #[test]
    fn directory_listing_counts_entries_without_retaining_unbounded_output() {
        let root = tempdir().expect("root");
        for index in 0..20 {
            fs::write(root.path().join(format!("file-{index:02}.txt")), "x").expect("fixture");
        }
        let workspaces =
            WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace capability");
        let approved = workspaces
            .resolve_existing(root.path().to_str().expect("utf8 root"))
            .expect("approved root");
        let result = list_directory(&approved, 300).expect("listing");
        assert_eq!(result.entry_count, 20);
        assert!(result.truncated);
        assert!(result.entries.len() < result.entry_count);
    }
}
