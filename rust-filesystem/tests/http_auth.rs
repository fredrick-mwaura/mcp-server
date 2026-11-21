use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde_json::json;
use tempfile::tempdir;

struct HttpServer(Child);

impl Drop for HttpServer {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn bearer_auth_protects_every_mcp_request() {
    let root = tempdir().expect("workspace");
    let port = available_port();
    let token = "test-token-with-at-least-16-bytes";
    let child = Command::new(env!("CARGO_BIN_EXE_mcp-filesystem-server"))
        .env("MCP_FS_ROOTS", root.path())
        .env("MCP_FS_TRANSPORT", "streamable-http")
        .env("MCP_FS_HOST", "127.0.0.1")
        .env("MCP_FS_PORT", port.to_string())
        .env("MCP_FS_AUTH_MODE", "bearer")
        .env("MCP_FS_AUTH_TOKEN", token)
        .env("MCP_FS_LOG_FORMAT", "text")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn HTTP server");
    let _server = HttpServer(child);
    wait_until_ready(port);

    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "http-test", "version": "1.0"}
        }
    })
    .to_string();
    assert!(http_post(port, &body, None, None).starts_with("HTTP/1.1 401"));
    assert!(http_post(port, &body, Some("wrong-token"), None).starts_with("HTTP/1.1 401"));
    assert!(
        http_post(port, &body, Some(token), Some("https://untrusted.invalid"))
            .starts_with("HTTP/1.1 403")
    );
    let authorized = http_post(port, &body, Some(token), None);
    assert!(authorized.starts_with("HTTP/1.1 200"), "{authorized}");
    assert!(authorized.contains("File System Server"), "{authorized}");
}

fn available_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .expect("ephemeral port")
        .local_addr()
        .expect("local address")
        .port()
}

fn wait_until_ready(port: u16) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(20));
    }
    panic!("HTTP server did not become ready");
}

fn http_post(port: u16, body: &str, token: Option<&str>, origin: Option<&str>) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("HTTP connection");
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .expect("read timeout");
    let authorization = token.map_or_else(String::new, |token| {
        format!("Authorization: Bearer {token}\r\n")
    });
    let origin = origin.map_or_else(String::new, |origin| format!("Origin: {origin}\r\n"));
    write!(
        stream,
        "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\n{authorization}{origin}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .expect("write HTTP request");
    stream.flush().expect("flush HTTP request");
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .expect("read HTTP response");
    response
}
