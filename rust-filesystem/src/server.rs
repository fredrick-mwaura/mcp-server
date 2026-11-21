use std::sync::{Arc, RwLock, RwLockReadGuard};

use rmcp::{
    ErrorData as McpError, ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, ContentBlock, Implementation, ServerCapabilities, ServerConfig},
    schemars, tool, tool_handler, tool_router,
};
use serde::{Deserialize, Serialize};

use crate::{
    Settings,
    error::AppError,
    python_syntax,
    tools::{editing, execution, git, navigation, patch, search},
    vfs,
    workspace::WorkspaceSet,
};

#[derive(Clone)]
pub struct FileSystemServer {
    settings: Arc<Settings>,
    workspaces: WorkspaceSet,
    tool_router: ToolRouter<Self>,
    operation_lock: Arc<RwLock<()>>,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ListDirectoryRequest {
    #[serde(default = "default_dot")]
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ReadFileRequest {
    pub path: String,
    #[serde(default = "default_one")]
    pub offset: usize,
    #[serde(default = "default_read_limit")]
    pub limit: usize,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct FindFilesRequest {
    #[serde(default = "default_star")]
    pub pattern: String,
    #[serde(default = "default_dot")]
    pub path: String,
    #[serde(default = "default_max_results")]
    pub max_results: usize,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct GrepSearchRequest {
    pub query: String,
    #[serde(default = "default_dot")]
    pub path: String,
    #[serde(default)]
    pub regex: bool,
    #[serde(default)]
    pub include_globs: Option<Vec<String>>,
    #[serde(default = "default_search_results")]
    pub max_results: usize,
    #[serde(default = "default_one")]
    pub context_lines: usize,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct SymbolOutlineRequest {
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct EditBlockRequest {
    pub path: String,
    pub target_content: String,
    pub replacement_content: String,
    #[serde(default)]
    pub dry_run: bool,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct WriteFileRequest {
    pub path: String,
    pub content: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct DeleteEntryRequest {
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ApplyPatchRequest {
    pub patch: String,
    #[serde(default = "default_dot")]
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct GitStatusRequest {
    #[serde(default = "default_dot")]
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct GitDiffRequest {
    #[serde(default = "default_dot")]
    pub path: String,
    #[serde(default)]
    pub cached: bool,
    #[serde(default)]
    pub target: Option<String>,
    #[serde(default)]
    pub paths: Option<Vec<String>>,
    #[serde(default = "default_diff_lines")]
    pub max_lines: usize,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct RevertFileRequest {
    pub path: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct RunTestRequest {
    pub command: String,
    #[serde(default = "default_dot")]
    pub path: String,
    #[serde(default = "default_test_timeout")]
    pub timeout: f64,
    #[serde(default = "default_true")]
    pub distill: bool,
}

impl FileSystemServer {
    pub fn new(settings: Settings) -> Result<Self, AppError> {
        let workspaces = WorkspaceSet::new(settings.roots.clone())?;
        Ok(Self {
            settings: Arc::new(settings),
            workspaces,
            tool_router: Self::tool_router(),
            operation_lock: Arc::new(RwLock::new(())),
        })
    }

    fn payload<T: Serialize>(result: Result<T, AppError>) -> Result<CallToolResult, McpError> {
        let value = match result {
            Ok(value) => serde_json::to_value(value),
            Err(error) => serde_json::to_value(error.to_payload()),
        }
        .map_err(|error| McpError::internal_error(error.to_string(), None))?;
        Ok(CallToolResult::success(vec![ContentBlock::text(
            value.to_string(),
        )]))
    }

    fn require_write(&self, tool: &str) -> Result<(), AppError> {
        if self.settings.mode == crate::config::Mode::ReadWrite {
            Ok(())
        } else {
            Err(AppError::ReadOnly {
                tool: tool.to_owned(),
            })
        }
    }

    fn require_execution(&self, tool: &str) -> Result<(), AppError> {
        if self.settings.allow_exec {
            Ok(())
        } else {
            Err(AppError::ExecutionDisabled {
                tool: tool.to_owned(),
            })
        }
    }

    fn read_guard(&self) -> Result<RwLockReadGuard<'_, ()>, AppError> {
        self.operation_lock
            .read()
            .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))
    }
}

#[tool_router]
impl FileSystemServer {
    #[tool(description = "List the files and folders inside an allowed directory.", input_schema = crate::tool_contract::input_schema("list_directory"))]
    fn list_directory(
        &self,
        Parameters(request): Parameters<ListDirectoryRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = (|| {
            let _guard = self.read_guard()?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            vfs::list_directory(&path, self.settings.max_output_bytes)
        })();
        Self::payload(result)
    }

    #[tool(description = "Read a bounded, line-numbered slice from an allowed file.", input_schema = crate::tool_contract::input_schema("read_file"))]
    fn read_file(
        &self,
        Parameters(request): Parameters<ReadFileRequest>,
    ) -> Result<CallToolResult, McpError> {
        let limit = request.limit.min(self.settings.max_read_lines);
        let result = (|| {
            let _guard = self.read_guard()?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            vfs::read_file(
                &path,
                request.offset,
                limit,
                self.settings.max_read_bytes,
                self.settings.max_output_bytes,
                self.settings.max_read_lines,
            )
        })();
        Self::payload(result)
    }

    #[tool(description = "Find files matching a glob under an allowed directory.", input_schema = crate::tool_contract::input_schema("find_files"))]
    fn find_files(
        &self,
        Parameters(request): Parameters<FindFilesRequest>,
    ) -> Result<CallToolResult, McpError> {
        let max_results = request.max_results.min(200);
        let result = (|| {
            let _guard = self.read_guard()?;
            let root = self.workspaces.resolve_existing(&request.path)?;
            navigation::find_files(
                &root,
                &request.pattern,
                max_results,
                8,
                self.settings.max_output_bytes,
            )
        })();
        Self::payload(result)
    }

    #[tool(description = "Search for text or a regular expression under an allowed directory.", input_schema = crate::tool_contract::input_schema("grep_search"))]
    fn grep_search(
        &self,
        Parameters(request): Parameters<GrepSearchRequest>,
    ) -> Result<CallToolResult, McpError> {
        let max_results = request.max_results.min(100);
        let context_lines = request.context_lines.min(5);
        let include_globs = request.include_globs.unwrap_or_default();
        let result = (|| {
            let _guard = self.read_guard()?;
            let root = self.workspaces.resolve_existing(&request.path)?;
            search::grep_search(
                &root,
                &request.query,
                &search::SearchOptions {
                    regex: request.regex,
                    include_globs: &include_globs,
                    max_results,
                    context_lines,
                    max_file_bytes: self.settings.max_read_bytes,
                    max_output_bytes: self.settings.max_output_bytes,
                },
            )
        })();
        Self::payload(result)
    }

    #[tool(description = "Extract top-level Python classes, methods, and functions.", input_schema = crate::tool_contract::input_schema("symbol_outline"))]
    fn symbol_outline(
        &self,
        Parameters(request): Parameters<SymbolOutlineRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = (|| {
            let _guard = self.read_guard()?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            if path.path().is_dir() {
                return Err(AppError::PathNotFile(path.path().display().to_string()));
            }
            let max_bytes = usize::try_from(self.settings.max_read_bytes).unwrap_or(usize::MAX);
            let source = vfs::read_text(&path, max_bytes)?;
            Ok(python_syntax::outline(
                &source,
                &path.path().display().to_string(),
            ))
        })();
        Self::payload(result)
    }

    #[tool(description = "Replace one unique text block and atomically save the result.", input_schema = crate::tool_contract::input_schema("edit_block"))]
    fn edit_block(
        &self,
        Parameters(request): Parameters<EditBlockRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self.require_write("edit_block").and_then(|()| {
            let _guard = self
                .operation_lock
                .write()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            editing::edit_block(
                &path,
                &request.target_content,
                &request.replacement_content,
                request.dry_run,
                self.settings.max_write_bytes,
            )
        });
        Self::payload(result)
    }

    #[tool(description = "Create or overwrite a file using an atomic sibling-file replacement.", input_schema = crate::tool_contract::input_schema("write_file"))]
    fn write_file(
        &self,
        Parameters(request): Parameters<WriteFileRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self.require_write("write_file").and_then(|()| {
            let _guard = self
                .operation_lock
                .write()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_creatable(&request.path)?;
            editing::write_file(&path, &request.content, self.settings.max_write_bytes)
        });
        Self::payload(result)
    }

    #[tool(description = "Move a file or directory to the owning workspace's recoverable trash.", input_schema = crate::tool_contract::input_schema("delete_entry"))]
    fn delete_entry(
        &self,
        Parameters(request): Parameters<DeleteEntryRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self.require_write("delete_entry").and_then(|()| {
            let _guard = self
                .operation_lock
                .write()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            vfs::move_to_trash(&path)
        });
        Self::payload(result)
    }

    #[tool(description = "Apply a validated unified diff beneath an allowed directory.", input_schema = crate::tool_contract::input_schema("apply_patch"))]
    fn apply_patch(
        &self,
        Parameters(request): Parameters<ApplyPatchRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self.require_write("apply_patch").and_then(|()| {
            let _guard = self
                .operation_lock
                .write()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let base = self.workspaces.resolve_existing(&request.path)?;
            patch::apply_patch(
                &self.workspaces,
                &base,
                &request.patch,
                self.settings.max_write_bytes,
            )
        });
        Self::payload(result)
    }

    #[tool(description = "Inspect Git branch, index, working tree, and untracked status.", input_schema = crate::tool_contract::input_schema("git_status"))]
    fn git_status(
        &self,
        Parameters(request): Parameters<GitStatusRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = (|| {
            let _guard = self
                .operation_lock
                .read()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            git::git_status(&path, self.settings.max_output_bytes)
        })();
        Self::payload(result)
    }

    #[tool(description = "Generate a bounded unified Git diff with change statistics.", input_schema = crate::tool_contract::input_schema("git_diff"))]
    fn git_diff(
        &self,
        Parameters(request): Parameters<GitDiffRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = (|| {
            let _guard = self
                .operation_lock
                .read()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            git::git_diff(
                &path,
                request.cached,
                request.target.as_deref(),
                request.paths.as_deref().unwrap_or_default(),
                request.max_lines.clamp(1, 5_000),
                self.settings.max_output_bytes,
            )
        })();
        Self::payload(result)
    }

    #[tool(description = "Export the bounded git diff HEAD patch used by SWE-bench.", input_schema = crate::tool_contract::input_schema("export_swebench_patch"))]
    fn export_swebench_patch(
        &self,
        Parameters(request): Parameters<GitStatusRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = (|| {
            let _guard = self
                .operation_lock
                .read()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_existing(&request.path)?;
            git::export_swebench_patch(&path, self.settings.max_output_bytes)
        })();
        Self::payload(result)
    }

    #[tool(description = "Restore a tracked file from HEAD or trash an untracked file.", input_schema = crate::tool_contract::input_schema("revert_file"))]
    fn revert_file(
        &self,
        Parameters(request): Parameters<RevertFileRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self.require_write("revert_file").and_then(|()| {
            let _guard = self
                .operation_lock
                .write()
                .map_err(|_| AppError::invalid_argument("server", "workspace lock is poisoned"))?;
            let path = self.workspaces.resolve_creatable(&request.path)?;
            git::revert_file(&path, self.settings.max_output_bytes)
        });
        Self::payload(result)
    }

    #[tool(description = "Execute a bounded test command with process-tree timeout cleanup.", input_schema = crate::tool_contract::input_schema("run_test"))]
    fn run_test(
        &self,
        Parameters(request): Parameters<RunTestRequest>,
    ) -> Result<CallToolResult, McpError> {
        let result = self
            .require_write("run_test")
            .and_then(|()| self.require_execution("run_test"))
            .and_then(|()| {
                let _guard = self.operation_lock.write().map_err(|_| {
                    AppError::invalid_argument("server", "workspace lock is poisoned")
                })?;
                let path = self.workspaces.resolve_existing(&request.path)?;
                execution::run_test(
                    &path,
                    &request.command,
                    request.timeout,
                    request.distill,
                    self.settings.max_output_bytes,
                )
            });
        Self::payload(result)
    }
}

#[allow(clippy::unused_async_trait_impl)]
#[tool_handler(router = self.tool_router)]
impl ServerHandler for FileSystemServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(
                "File System Server",
                env!("CARGO_PKG_VERSION"),
            ))
            .with_instructions(
                "Secure, token-efficient filesystem access. Rust migration candidate.".to_owned(),
            )
    }
}

fn default_dot() -> String {
    ".".to_owned()
}

fn default_star() -> String {
    "*".to_owned()
}

const fn default_one() -> usize {
    1
}

const fn default_read_limit() -> usize {
    200
}

const fn default_max_results() -> usize {
    100
}

const fn default_search_results() -> usize {
    30
}

const fn default_diff_lines() -> usize {
    500
}

const fn default_test_timeout() -> f64 {
    90.0
}

const fn default_true() -> bool {
    true
}
