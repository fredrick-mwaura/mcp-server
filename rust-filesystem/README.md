# Rust MCP filesystem server

This directory contains the side-by-side Rust implementation described in
[`python-to-rust.md`](../docs/implementation/python-to-rust.md). It exposes the
same 14 MCP tool names as the Python server while closing the documented path,
HTTP, patch, output-bounding, and process-tree gaps.

The Rust server is a migration candidate, not yet the repository-wide default.
The Python implementation remains the fallback until differential testing,
cross-platform release builds, benchmarks, and the cutover soak period pass.

## Build and verify

Rust is pinned by `rust-toolchain.toml`.

```sh
cargo fmt --check
cargo test --all-targets
cargo clippy --all-targets -- -D warnings
cargo build --locked --release
```

Run over stdio from the workspace that should be exposed:

```sh
MCP_FS_ROOTS="$PWD" \
MCP_FS_MODE=read-only \
cargo run --locked --release
```

`stdout` is reserved for MCP protocol traffic. Logs go to `stderr`.

## Secure HTTP

Loopback HTTP may run without application authentication under
`MCP_FS_AUTH_MODE=auto`:

```sh
MCP_FS_ROOTS="$PWD" \
MCP_FS_TRANSPORT=streamable-http \
MCP_FS_HOST=127.0.0.1 \
cargo run --locked --release
```

Non-loopback HTTP fails closed unless bearer authentication is configured:

```sh
MCP_FS_ROOTS="$PWD" \
MCP_FS_TRANSPORT=streamable-http \
MCP_FS_HOST=0.0.0.0 \
MCP_FS_AUTH_MODE=bearer \
MCP_FS_AUTH_TOKEN_FILE=/run/secrets/mcp_fs_token \
cargo run --locked --release
```

All `/mcp` methods and session operations use the same authentication
middleware. Bearer values are hashed during configuration and compared in
constant time. Browser `Origin` headers are rejected unless explicitly listed.
The SDK also validates inbound `Host` values.

## Permissions

- `MCP_FS_MODE=read-only` is the default.
- File mutation requires `MCP_FS_MODE=read-write`.
- `run_test` additionally requires `MCP_FS_ALLOW_EXEC=true`.
- Remote execution is rejected unless HTTP is bearer-authenticated.

See [`.env.example`](.env.example) and the
[contract manifest](../contract/server-manifest.json) for the complete settings
surface and defaults.

## Container

Build from this directory:

```sh
docker build -t mcp-filesystem-rust .
```

The image runs as UID 1000, defaults to read-only, and deliberately refuses to
start its non-loopback HTTP listener without a runtime secret:

```sh
docker run --rm -p 8000:8000 \
  -v "$PWD:/workspace" \
  -e MCP_FS_AUTH_TOKEN='replace-with-at-least-16-random-bytes' \
  mcp-filesystem-rust
```

Mount a secret file and use `MCP_FS_AUTH_TOKEN_FILE` in production instead of
putting a token in shell history.
