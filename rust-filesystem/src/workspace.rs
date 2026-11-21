use std::{
    env, fs,
    path::{Component, Path, PathBuf},
    sync::Arc,
};

use crate::error::{AppError, AppResult};

#[derive(Debug, Clone)]
pub struct WorkspaceSet {
    roots: Arc<[PathBuf]>,
}

#[derive(Debug, Clone)]
pub struct ExistingPath {
    path: PathBuf,
    root: PathBuf,
}

#[derive(Debug, Clone)]
pub struct CreatablePath {
    path: PathBuf,
    root: PathBuf,
}

impl WorkspaceSet {
    pub fn new(roots: impl IntoIterator<Item = PathBuf>) -> AppResult<Self> {
        let mut canonical_roots = Vec::new();
        for root in roots {
            let canonical = fs::canonicalize(&root).map_err(|error| {
                if error.kind() == std::io::ErrorKind::NotFound {
                    AppError::PathNotFound(root.display().to_string())
                } else {
                    AppError::io(error)
                }
            })?;
            if !canonical.is_dir() {
                return Err(AppError::PathNotDirectory(canonical.display().to_string()));
            }
            if !canonical_roots.contains(&canonical) {
                canonical_roots.push(canonical);
            }
        }
        canonical_roots.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
        if canonical_roots.is_empty() {
            return Err(AppError::invalid_argument(
                "MCP_FS_ROOTS",
                "must contain at least one directory",
            ));
        }
        Ok(Self {
            roots: canonical_roots.into(),
        })
    }

    pub fn roots(&self) -> &[PathBuf] {
        &self.roots
    }

    pub fn resolve_existing(&self, requested: &str) -> AppResult<ExistingPath> {
        reject_nul(requested)?;
        let candidate = absolute_candidate(requested)?;
        let canonical = fs::canonicalize(&candidate).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                AppError::PathNotFound(candidate.display().to_string())
            } else {
                AppError::io(error)
            }
        })?;
        let root = self
            .owner(&canonical)
            .ok_or_else(|| self.denied(requested, &canonical))?;
        Ok(ExistingPath {
            path: canonical,
            root: root.clone(),
        })
    }

    pub fn resolve_creatable(&self, requested: &str) -> AppResult<CreatablePath> {
        reject_nul(requested)?;
        let candidate = absolute_candidate(requested)?;
        if candidate.exists() {
            let existing = self.resolve_existing(requested)?;
            return Ok(CreatablePath {
                path: existing.path,
                root: existing.root,
            });
        }
        let parent = candidate.parent().ok_or_else(|| {
            AppError::invalid_argument("path", "target must have an existing parent directory")
        })?;
        let canonical_parent = fs::canonicalize(parent).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                AppError::PathNotFound(parent.display().to_string())
            } else {
                AppError::io(error)
            }
        })?;
        let root = self
            .owner(&canonical_parent)
            .ok_or_else(|| self.denied(requested, &canonical_parent))?;
        let name = candidate
            .file_name()
            .ok_or_else(|| AppError::invalid_argument("path", "target must include a file name"))?;
        Ok(CreatablePath {
            path: canonical_parent.join(name),
            root: root.clone(),
        })
    }

    pub fn resolve_existing_under(
        &self,
        base: &ExistingPath,
        relative: &str,
    ) -> AppResult<ExistingPath> {
        let relative = validate_relative(relative)?;
        if !base.path.is_dir() {
            return Err(AppError::PathNotDirectory(base.path.display().to_string()));
        }
        let requested = base.path.join(relative);
        let resolved = self.resolve_existing(&requested.display().to_string())?;
        if resolved.root != base.root || !resolved.path.starts_with(&base.path) {
            return Err(AppError::InvalidPatchPath {
                path: requested.display().to_string(),
                detail: "target escapes the selected patch base".to_owned(),
            });
        }
        Ok(resolved)
    }

    pub fn resolve_creatable_under(
        &self,
        base: &ExistingPath,
        relative: &str,
    ) -> AppResult<CreatablePath> {
        let relative = validate_relative(relative)?;
        if !base.path.is_dir() {
            return Err(AppError::PathNotDirectory(base.path.display().to_string()));
        }
        let requested = base.path.join(relative);
        let resolved = self.resolve_creatable(&requested.display().to_string())?;
        if resolved.root != base.root || !resolved.path.starts_with(&base.path) {
            return Err(AppError::InvalidPatchPath {
                path: requested.display().to_string(),
                detail: "target escapes the selected patch base".to_owned(),
            });
        }
        Ok(resolved)
    }

    fn owner(&self, candidate: &Path) -> Option<&PathBuf> {
        self.roots.iter().find(|root| candidate.starts_with(root))
    }

    fn denied(&self, requested: &str, resolved: &Path) -> AppError {
        AppError::PathNotAllowed {
            requested: requested.to_owned(),
            resolved: resolved.display().to_string(),
            roots: self
                .roots
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(", "),
        }
    }
}

impl ExistingPath {
    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn root(&self) -> &Path {
        &self.root
    }
}

impl CreatablePath {
    pub fn from_existing(path: &ExistingPath) -> Self {
        Self {
            path: path.path.clone(),
            root: path.root.clone(),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn to_existing(&self) -> AppResult<ExistingPath> {
        if !self.path.exists() {
            return Err(AppError::PathNotFound(self.path.display().to_string()));
        }
        Ok(ExistingPath {
            path: fs::canonicalize(&self.path).map_err(AppError::io)?,
            root: self.root.clone(),
        })
    }
}

fn absolute_candidate(requested: &str) -> AppResult<PathBuf> {
    let value = if requested.trim().is_empty() {
        PathBuf::from(".")
    } else if let Some(rest) = requested.strip_prefix("~/") {
        let home = env::var_os("HOME").ok_or_else(|| {
            AppError::invalid_argument("path", "cannot expand '~': HOME is unset")
        })?;
        PathBuf::from(home).join(rest)
    } else {
        PathBuf::from(requested)
    };
    if value.is_absolute() {
        Ok(value)
    } else {
        Ok(env::current_dir().map_err(AppError::io)?.join(value))
    }
}

fn reject_nul(requested: &str) -> AppResult<()> {
    if requested.contains('\0') {
        return Err(AppError::invalid_argument(
            "path",
            "must not contain a NUL byte",
        ));
    }
    Ok(())
}

fn validate_relative(value: &str) -> AppResult<PathBuf> {
    reject_nul(value).map_err(|_| AppError::InvalidPatchPath {
        path: value.to_owned(),
        detail: "must not contain a NUL byte".to_owned(),
    })?;
    if value.is_empty()
        || value.starts_with(['/', '\\'])
        || value.as_bytes().get(1).is_some_and(|byte| *byte == b':')
        || value.contains('\\')
    {
        return Err(AppError::InvalidPatchPath {
            path: value.to_owned(),
            detail: "must be a non-empty portable relative path".to_owned(),
        });
    }
    let path = Path::new(value);
    if path.components().any(|component| {
        matches!(
            component,
            Component::ParentDir | Component::RootDir | Component::Prefix(_)
        )
    }) {
        return Err(AppError::InvalidPatchPath {
            path: value.to_owned(),
            detail: "absolute paths and parent traversal are forbidden".to_owned(),
        });
    }
    Ok(path.to_path_buf())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn allows_existing_path_inside_root() {
        let root = tempdir().expect("tempdir");
        let file = root.path().join("inside.txt");
        fs::write(&file, "ok").expect("write fixture");
        let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
        assert_eq!(
            workspaces
                .resolve_existing(file.to_str().expect("utf8 path"))
                .expect("allowed")
                .path(),
            fs::canonicalize(file).expect("canonical fixture")
        );
    }

    #[test]
    fn blocks_sibling_prefix() {
        let parent = tempdir().expect("tempdir");
        let root = parent.path().join("data");
        let sibling = parent.path().join("dataevil");
        fs::create_dir_all(&root).expect("create root");
        fs::create_dir_all(&sibling).expect("create sibling");
        let workspaces = WorkspaceSet::new([root]).expect("workspace");
        assert!(matches!(
            workspaces.resolve_existing(sibling.to_str().expect("utf8 path")),
            Err(AppError::PathNotAllowed { .. })
        ));
    }

    #[cfg(unix)]
    #[test]
    fn blocks_symlink_escape() {
        use std::os::unix::fs::symlink;

        let root = tempdir().expect("root");
        let outside = tempdir().expect("outside");
        let link = root.path().join("escape");
        symlink(outside.path(), &link).expect("symlink");
        let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
        assert!(matches!(
            workspaces.resolve_existing(link.to_str().expect("utf8 path")),
            Err(AppError::PathNotAllowed { .. })
        ));
    }

    #[test]
    fn creatable_path_requires_allowed_existing_parent() {
        let root = tempdir().expect("root");
        let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
        let target = root.path().join("new.txt");
        let approved = workspaces
            .resolve_creatable(target.to_str().expect("utf8 path"))
            .expect("allowed");
        assert_eq!(
            approved.path(),
            fs::canonicalize(root.path())
                .expect("canonical root")
                .join("new.txt")
        );
    }

    #[test]
    fn derived_path_cannot_escape_patch_base() {
        let root = tempdir().expect("root");
        let base_dir = root.path().join("base");
        fs::create_dir(&base_dir).expect("base");
        let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
        let base = workspaces
            .resolve_existing(base_dir.to_str().expect("utf8 path"))
            .expect("base allowed");
        assert!(matches!(
            workspaces.resolve_creatable_under(&base, "../escape.txt"),
            Err(AppError::InvalidPatchPath { .. })
        ));
    }

    #[test]
    fn derived_path_rejects_windows_and_backslash_paths() {
        let root = tempdir().expect("root");
        let workspaces = WorkspaceSet::new([root.path().to_path_buf()]).expect("workspace");
        let base = workspaces
            .resolve_existing(root.path().to_str().expect("utf8 path"))
            .expect("base allowed");
        for path in [r"C:\\escape.txt", r"folder\\escape.txt", r"\\server\\share"] {
            assert!(matches!(
                workspaces.resolve_creatable_under(&base, path),
                Err(AppError::InvalidPatchPath { .. })
            ));
        }
    }
}
