# System Architecture & Layered Design

## 1. High-Level Architecture

The server is built with a strictly modular, layered architecture that enforces single-responsibility boundaries across five distinct tiers:

```
[MCP Client / Model]
        │
        ▼  JSON-RPC (stdio or HTTP)
┌─────────────────────────────────────────────────────────────┐
│ 1. Configuration & Server Assembly (server.py, config.py)   │
│    - Env parsing (MCP_FS_*) via Pydantic                    │
│    - Closure-based dependency injection (no globals)         │
│    - Mode gate (read-only vs read-write)                     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Security Gates (security/canonical_path, syntax_guard)   │
│    - Canonical path resolution & boundary verification      │
│    - Pre-commit AST syntax validation for Python writes     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Thin Tool Handlers (tools/navigation, editing, git_ops)  │
│    - Request formatting and token-efficient response shaping│
│    - No business logic, no direct OS filesystem calls       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Virtual File System / VFS (vfs.py)                       │
│    - The ONLY module in the codebase permitted to make OS   │
│      filesystem syscalls (os, open, scandir, shutil)        │
│    - Atomic swap writes & safe trash buffer                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Physical Storage & Operating System                      │
│    - Host disk within verified workspace boundaries         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Closure-Based Dependency Injection

A common anti-pattern in MCP servers is storing server configuration (such as allowed roots, execution mode, or token caps) in module-level global variables.

### The Problem with Globals
* **Test Pollution**: Unit and integration tests cannot run concurrently or with different configurations without mutating global state.
* **Leakage**: If a future update introduces multi-workspace or multi-tenant HTTP runners, global variables cause cross-tenant path contamination.

### The Solution: Closure Injection in `server.py`
In `mcp_fs`, `build_server(settings: Settings) -> MCPServer` is a pure constructor function:
1. `settings` are passed into `build_server`.
2. Roots are canonicalized and verified to exist upfront (failing fast if misconfigured).
3. Tool functions are defined **inside** the scope of `build_server`, creating closures that capture `root_strings`, `server_mode`, and `limits`.

```python
def build_server(settings: Settings) -> MCPServer:
    roots = _validate_roots(settings)
    root_strings = [str(r) for r in roots]
    server_mode = settings.mode

    def _require_write_mode(tool_name: str) -> None:
        if server_mode != "read-write":
            raise ReadOnlyModeError(tool_name)

    @mcp.tool()
    def write_file(path: str, content: str) -> dict[str, object]:
        _require_write_mode("write_file")
        canonical = resolve_allowed_path(path, root_strings, must_exist=False)
        validate_python_syntax(content, canonical)
        return editing.write_file(canonical, content)
```

Each server instance has its own isolated, immutable security envelope.

---

## 3. Strict Layer Boundaries

### The Golden VFS Rule
> **No module other than `vfs.py` is permitted to import `os` or `pathlib` for file I/O, and no path can reach `vfs.py` without first passing through `security.canonical_path`.**

* **`tools/`**: Contains thin formatting handlers. A tool handler never performs path validation, never reads files directly, and contains no security policy.
* **`security/`**: The gatekeeper. Evaluates paths against allowed boundaries and code against AST syntax rules.
* **`vfs.py`**: The sole executor. Executes `scandir`, `open`, `replace`, and `move` only on pre-vetted canonical paths.

---

## 4. Structured Error Protocol (`errors.py`)

Standard MCP servers often return unstructured strings on error (e.g. `{"error": "Failed"}`). This forces LLMs to guess what went wrong, frequently triggering hallucinated repair attempts.

`mcp_fs` uses a strongly-typed error hierarchy derived from `FileSystemError`:

```
FileSystemError (base)
├── PathNotAllowedError       (code: "path_not_allowed")
├── PathNotFoundError         (code: "path_not_found")
├── PathNotADirectoryError    (code: "path_not_a_directory")
├── PathNotAFileError         (code: "path_not_a_file")
├── FileTooLargeError         (code: "file_too_large")
├── ReadOnlyModeError         (code: "read_only_mode")
├── EditTargetNotFoundError   (code: "edit_target_error")
├── SyntaxValidationError     (code: "syntax_error")
├── NotAGitRepositoryError    (code: "not_a_git_repo")
└── GitCommandError           (code: "git_command_error")
```

Every error implements `.to_result()` returning:
```json
{
  "error_code": "edit_target_error",
  "error": "Target content found 2 times in 'main.py'. edit_block requires a unique match."
}
```

The AI model branches on `error_code` immediately, enabling precise recovery in a single turn.

---

## 5. Stdio Transport Discipline & Telemetry

When running over standard I/O (`stdio` transport):
* **`stdout` is strictly reserved for JSON-RPC messages**. If any library, print statement, or subprocess writes raw text to `stdout`, the JSON-RPC framing is corrupted and the client crashes.
* **All logs, metrics, and diagnostics are sent to `stderr`** in structured JSON format (`telemetry.py`), timestamped with millisecond tool call durations.
