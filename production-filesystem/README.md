# mcp-filesystem-server — Production MCP File System Server

A workspace-scoped, production-grade MCP server built for software engineers
and autonomous SWE-bench benchmark workflows. It provides token-preserving code
inspection, surgical editing, pre-commit AST syntax validation, and native Git integration.

> 📚 **Complete Technical Documentation**:
> Deep architecture and algorithm specifications are documented in **[`../docs/`](../docs/README.md)**:
> - **[`Architecture & Layering`](../docs/architecture.md)**: Modular design, closure-based DI, and VFS isolation.
> - **[`Token Economics & Cost Optimization`](../docs/token_economics.md)**: Line slicing, AST outlines, and 90-98% token reduction.
> - **[`Security & VFS Invariants`](../docs/security_and_vfs.md)**: Single chokepoint gate, atomic swap writes, and scoped trash design.
> - **[`Git & SWE-Bench Lifecycle`](../docs/git_and_swebench.md)**: Patch export pipeline, status parsing, and autonomous rollback.
> - **[`Test Execution & Smart Failure Distillation`](../docs/test_runner.md)**: Bounded test runner and 90% token reduction on test results.


## The security design (read this once)

Every path a caller gives us runs through one gate, in this order:

```
expand "~" → make absolute → normpath (kills ../..) → realpath (kills symlink
escapes) → zone-check against allowed roots → reject or return canonical path
```

The gate lives in `src/mcp_fs/security/canonical_path.py` and is the module you
should read first. Its tests (`tests/unit/test_canonical_path.py`) are written
as attack proofs — each one is a real vulnerability a careless file server has.

Only `src/mcp_fs/vfs.py` may touch the actual filesystem. Tools are "thin":
they validate, call the gate, delegate to `vfs`, and return. See
`src/mcp_fs/server.py` for the wiring and `src/mcp_fs/__init__.py` for the
module map.

---

## Requirements

- Python **3.10+** (tested on 3.12)
- opencode (for the demo) — the MCP client we connect to

---

## Install (for development / running the tests)

```bash
cd production-filesystem

# create a venv (macOS/Linux)
python3 -m venv .venv
source .venv/bin/activate

# install the package + its dev tools (pytest)
pip install -e ".[dev]"

# run the whole test suite
pytest -v
```

You should see the security tests pass — including the ones proving that
`../../..`, symlink escapes, and sibling-prefix tricks are blocked.

## Run the server

The server reads its configuration from environment variables
(`MCP_FS_*` — see `.env.example`):

```bash
# which directories may the server touch?
export MCP_FS_ROOTS="$HOME/projects,$HOME/Documents"
# Phase 1 is read-only regardless; the knob exists for Phase 3.
export MCP_FS_MODE=read-only
export MCP_FS_TRANSPORT=stdio

# start it (it will wait quietly for an MCP client on stdio)
mcp-filesystem-server
```

If `MCP_FS_ROOTS` is unset the server defaults to the directory it was started
in. Logs go to **stderr as JSON** (never stdout — stdout is the protocol).

> 🐛 Quick sanity check that it answers: open a second terminal and pipe a raw
> `tools/list` request:
> ```bash
> echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' | mcp-filesystem-server
> ```

## Connect to opencode

The repo's `opencode.json` already registers this server (check the `filesystem`
entry). It uses the shared `.venv` at the repository root, so make sure that
venv exists and the package is installed in it first (see the top-level
`README.md`, or run from this folder):

```bash
# from the repo root: create the venv if you have not already
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./production-filesystem
```

Then check the `opencode.json` at the repo root and fix the paths for your
machine:

1. The `command` must point at the installed console script. Find it with
   `which mcp-filesystem-server` — inside the repo venv it lives at
   `.venv/bin/mcp-filesystem-server`.
2. The server reads its roots from the `environment` map. Set `MCP_FS_ROOTS` to
   the directories you want the server allowed to touch.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "filesystem": {
      "type": "local",
      "command": ["/Users/YOU/mcp-server/.venv/bin/mcp-filesystem-server"],
      "environment": {
        "MCP_FS_ROOTS": "/Users/YOU/projects,/Users/YOU/Documents",
        "MCP_FS_MODE": "read-only",
        "MCP_FS_TRANSPORT": "stdio"
      }
    }
  }
}
```

> On Windows, double every backslash `\` → `\\` in the JSON path, and point the
> command at `.venv\Scripts\mcp-filesystem-server.exe`.

3. **Quit and restart opencode** from the repo root (config is loaded once, at
   startup). The **File System Server** is now connected with one tool:
   `list_directory`.

Try prompts like:

- *"List the files in `~/projects`."*
- *"What's in my Documents folder?"*
- *"Try to read `/etc/passwd`"* → watch it return a clean `path_not_allowed`.

## Project layout

```
production-filesystem/
├── pyproject.toml            # packaging; console script `mcp-filesystem-server`
├── .env.example              # documented env configuration template
├── src/mcp_fs/
│   ├── __init__.py           # module map / design rules
│   ├── __main__.py           # `python -m mcp_fs`
│   ├── server.py             # assembles MCPServer + registers tools
│   ├── config.py             # env-driven Settings (validation via pydantic)
│   ├── errors.py             # typed errors → stable error_code for the model
│   ├── telemetry.py          # JSON logging to stderr
│   ├── security/             # ★ THE security gate (read me first)
│   │   └── canonical_path.py
│   ├── vfs.py                # the ONLY module that touches the filesystem
│   └── tools/
│       └── navigation.py     # thin read-only handlers
└── tests/
    ├── unit/                 # canonical_path attack tests
    └── integration/          # drives the real MCPServer via its MCP API
```

## Where Phase 2 goes from here

- `read_file` with size caps, `get_metadata`, files exposed as MCP **Resources**
  (`mcp-fs://`), and `search_files` — see `PRODUCTION_PLAN.md` §9.
