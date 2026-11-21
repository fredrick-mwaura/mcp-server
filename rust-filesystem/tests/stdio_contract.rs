use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
};

use serde_json::{Value, json};
use tempfile::tempdir;

struct ServerProcess {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl ServerProcess {
    fn start(root: &std::path::Path) -> Self {
        Self::start_with_exec(root, false)
    }

    fn start_with_exec(root: &std::path::Path, allow_exec: bool) -> Self {
        let mut command = Command::new(env!("CARGO_BIN_EXE_mcp-filesystem-server"));
        command
            .current_dir(root)
            .env("MCP_FS_ROOTS", root)
            .env("MCP_FS_MODE", "read-write")
            .env(
                "MCP_FS_ALLOW_EXEC",
                if allow_exec { "true" } else { "false" },
            )
            .env(
                "MCP_FS_AUTH_TOKEN",
                "sensitive-test-token-at-least-16-bytes",
            )
            .env("MCP_FS_TRANSPORT", "stdio")
            .env("MCP_FS_LOG_FORMAT", "text")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = command.spawn().expect("spawn Rust MCP server");
        let input = child.stdin.take().expect("server stdin");
        let output = BufReader::new(child.stdout.take().expect("server stdout"));
        Self {
            child,
            input,
            output,
        }
    }

    fn send(&mut self, message: &Value) {
        writeln!(self.input, "{message}").expect("write JSON-RPC message");
        self.input.flush().expect("flush JSON-RPC message");
    }

    fn request(&mut self, message: &Value) -> Value {
        self.send(message);
        let mut line = String::new();
        self.output
            .read_line(&mut line)
            .expect("read JSON-RPC response");
        assert!(
            !line.is_empty(),
            "server exited before returning a response"
        );
        serde_json::from_str(&line).expect("valid JSON-RPC response")
    }
}

fn initialize(server: &mut ServerProcess) -> Value {
    let initialize = server.request(&json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "contract-test", "version": "1.0"}
        }
    }));
    assert_eq!(initialize["id"], 1);
    server.send(&json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {}
    }));
    initialize
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[test]
fn stdio_lists_exact_tool_surface_and_calls_a_tool() {
    let root = tempdir().expect("workspace");
    std::fs::write(root.path().join("hello.txt"), "hello\n").expect("fixture");
    let mut server = ServerProcess::start(root.path());
    let initialized = initialize(&mut server);
    assert_eq!(
        initialized["result"]["serverInfo"]["name"],
        "File System Server"
    );

    let listed = server.request(&json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }));
    let mut names = listed["result"]["tools"]
        .as_array()
        .expect("tool array")
        .iter()
        .map(|tool| tool["name"].as_str().expect("tool name"))
        .collect::<Vec<_>>();
    names.sort_unstable();
    assert_eq!(
        names,
        [
            "apply_patch",
            "delete_entry",
            "edit_block",
            "export_swebench_patch",
            "find_files",
            "git_diff",
            "git_status",
            "grep_search",
            "list_directory",
            "read_file",
            "revert_file",
            "run_test",
            "symbol_outline",
            "write_file",
        ]
    );

    let called = server.request(&json!({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "list_directory", "arguments": {"path": "."}}
    }));
    let content = called["result"]["content"][0]["text"]
        .as_str()
        .expect("text content");
    let payload: Value = serde_json::from_str(content).expect("tool JSON payload");
    assert_eq!(payload["entry_count"], 1);
    assert_eq!(payload["entries"][0]["name"], "hello.txt");
}

#[cfg(unix)]
#[test]
fn run_test_does_not_inherit_http_auth_secrets() {
    let root = tempdir().expect("workspace");
    let mut server = ServerProcess::start_with_exec(root.path(), true);
    let _ = initialize(&mut server);
    let called = server.request(&json!({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "run_test",
            "arguments": {"command": "env", "path": ".", "distill": false}
        }
    }));
    let content = called["result"]["content"][0]["text"]
        .as_str()
        .expect("text content");
    let payload: Value = serde_json::from_str(content).expect("tool JSON payload");
    let stdout = payload["stdout"].as_str().expect("command stdout");
    assert!(!stdout.contains("MCP_FS_AUTH_TOKEN="));
    assert!(!stdout.contains("sensitive-test-token"));
}

#[test]
fn configuration_errors_exit_with_status_two() {
    let status = Command::new(env!("CARGO_BIN_EXE_mcp-filesystem-server"))
        .arg("--check-config")
        .env("MCP_FS_TRANSPORT", "streamable-http")
        .env("MCP_FS_HOST", "0.0.0.0")
        .env("MCP_FS_AUTH_MODE", "auto")
        .env_remove("MCP_FS_AUTH_TOKEN")
        .env_remove("MCP_FS_AUTH_TOKEN_FILE")
        .status()
        .expect("run config check");
    assert_eq!(status.code(), Some(2));
}
