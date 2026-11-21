# mcp-filesystem-server

The Python compatibility implementation of the workspace-scoped MCP filesystem server. It exposes the same 14-tool contract as the Rust candidate: bounded navigation/search, syntax-checked editing, strict unified patches, contained Git operations, recoverable deletion, and opt-in test execution.

## Security defaults

- Every direct or derived path is canonicalized beneath an allowed root. Patch targets and Git roots/pathspecs receive their own point-of-use checks.
- The default mode is `read-only`; `run_test` additionally requires `MCP_FS_ALLOW_EXEC=true`.
- stdio and loopback HTTP may run without application authentication.
- non-loopback HTTP requires a bearer token unless the conspicuous development override `MCP_FS_ALLOW_UNSAFE_REMOTE=true` is set.
- reads, writes, search results, diffs, HTTP bodies, and subprocess output have configurable ceilings.

The supported unified-diff subset creates, modifies, and deletes UTF-8 text files. It rejects binary patches, renames, copies, mode changes, symlink creation, traversal, stale context, and duplicate targets. Multi-file patches are fully preflighted before mutation and rolled back if commit fails.

## Install and verify

```bash
cd production-filesystem
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

The installed commands are `mcp-filesystem-server` and `python -m mcp_fs`.

## Local stdio

```bash
export MCP_FS_ROOTS="$PWD"
export MCP_FS_MODE=read-only
export MCP_FS_TRANSPORT=stdio
mcp-filesystem-server
```

For write tools, explicitly set `MCP_FS_MODE=read-write`. To enable `run_test`, also set `MCP_FS_ALLOW_EXEC=true`; commands are tokenized without a shell, output is capped, and timeouts terminate the process tree.

## Authenticated HTTP

```bash
export MCP_FS_ROOTS=/workspace
export MCP_FS_TRANSPORT=streamable-http
export MCP_FS_HOST=0.0.0.0
export MCP_FS_AUTH_TOKEN_FILE=/run/secrets/mcp_fs_token
mcp-filesystem-server
```

Clients send `Authorization: Bearer <token>`. Tokens must contain at least 16 characters and are never logged. Use `MCP_FS_ALLOWED_HOSTS` and `MCP_FS_ALLOWED_ORIGINS` to tighten browser-facing deployments.

## Container

```bash
docker build -t mcp-filesystem-python production-filesystem
docker run --rm -p 127.0.0.1:8000:8000 \
  -v "$PWD:/workspace:ro" \
  -e MCP_FS_AUTH_TOKEN="replace-with-a-runtime-secret" \
  mcp-filesystem-python
```

The image runs as UID 1000, serves read-only by default, and deliberately fails startup on its non-loopback bind if no token is injected. Mount a writable workspace and opt into `MCP_FS_MODE=read-write` only when needed.

See [`.env.example`](.env.example), the [architecture documentation](../docs/architecture.md), and the [hardening record](../docs/implementation/fix-gaps.md) for the complete configuration and verification matrix.
