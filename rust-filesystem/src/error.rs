use std::io;

use serde::Serialize;
use thiserror::Error;

pub type AppResult<T> = Result<T, AppError>;

#[derive(Debug, Error)]
pub enum AppError {
    #[error(
        "Path '{requested}' (resolves to '{resolved}') is outside the allowed root(s): {roots}"
    )]
    PathNotAllowed {
        requested: String,
        resolved: String,
        roots: String,
    },
    #[error("Path does not exist: {0}")]
    PathNotFound(String),
    #[error("Path is not a directory: {0}")]
    PathNotDirectory(String),
    #[error("Path is a directory, not a file: {0}")]
    PathNotFile(String),
    #[error(
        "File '{path}' is {size_bytes} bytes, exceeding the {max_bytes} byte limit. Use offset/limit to read a slice."
    )]
    FileTooLarge {
        path: String,
        size_bytes: u64,
        max_bytes: u64,
    },
    #[error(
        "Tool '{tool}' requires read-write mode, but the server is configured as read-only (MCP_FS_MODE=read-only)."
    )]
    ReadOnly { tool: String },
    #[error(
        "Target content found {occurrences} times in '{path}'. edit_block requires exactly one occurrence."
    )]
    EditTarget { path: String, occurrences: usize },
    #[error("Syntax error in '{path}': {detail}. The file was NOT modified.")]
    Syntax { path: String, detail: String },
    #[error("Invalid patch path '{path}': {detail}")]
    InvalidPatchPath { path: String, detail: String },
    #[error("Patch conflict for '{path}': {detail}")]
    PatchConflict { path: String, detail: String },
    #[error("Path is not in a git repository: {0}")]
    NotGitRepository(String),
    #[error("Git repository '{repo}' is outside the owning allowed root '{root}'")]
    GitRepositoryOutsideRoot { repo: String, root: String },
    #[error("git command '{command}' failed (exit code {return_code}): {detail}")]
    GitCommand {
        command: String,
        return_code: i32,
        detail: String,
    },
    #[error("Tool '{tool}' requires MCP_FS_ALLOW_EXEC=true")]
    ExecutionDisabled { tool: String },
    #[error("Invalid value for '{field}': {detail}")]
    InvalidArgument { field: String, detail: String },
    #[error("I/O operation failed: {0}")]
    Io(#[source] io::Error),
}

impl AppError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::PathNotAllowed { .. } => "path_not_allowed",
            Self::PathNotFound(_) => "path_not_found",
            Self::PathNotDirectory(_) => "path_not_a_directory",
            Self::PathNotFile(_) => "path_not_a_file",
            Self::FileTooLarge { .. } => "file_too_large",
            Self::ReadOnly { .. } => "read_only_mode",
            Self::EditTarget { .. } => "edit_target_error",
            Self::Syntax { .. } => "syntax_error",
            Self::InvalidPatchPath { .. } => "invalid_patch_path",
            Self::PatchConflict { .. } => "patch_conflict",
            Self::NotGitRepository(_) => "not_a_git_repo",
            Self::GitRepositoryOutsideRoot { .. } => "git_repo_outside_root",
            Self::GitCommand { .. } => "git_command_error",
            Self::ExecutionDisabled { .. } => "execution_disabled",
            Self::InvalidArgument { .. } => "invalid_argument",
            Self::Io(_) => "internal_error",
        }
    }

    pub fn invalid_argument(field: impl Into<String>, detail: impl Into<String>) -> Self {
        Self::InvalidArgument {
            field: field.into(),
            detail: detail.into(),
        }
    }

    pub fn io(error: io::Error) -> Self {
        Self::Io(error)
    }

    pub fn to_payload(&self) -> ErrorPayload<'_> {
        ErrorPayload {
            error_code: self.code(),
            error: self.to_string(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ErrorPayload<'a> {
    pub error_code: &'a str,
    pub error: String,
}
