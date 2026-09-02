# Production MCP Server — File System Manager

**Status:** Plan / Phase 0
**Language:** Python (MCP SDK v2, `mcp.server.mcpserver.MCPServer`)
**Transports:** `stdio` (local) **and** `streamable-http` (remote), same core
**Related course project:** `terminal_server.py` (Lesson 1 — basic MCP server)

---

## 1. Why this project?

Lesson 1 taught *"how to bolt a tool onto MCP."* This project teaches the harder
question: **what does it take to ship an MCP server that real users trust with
their files?**

A file-system manager is an ideal production teaching vehicle because every
"boring" production concern maps to a concrete, scary bug students can actually
reproduce:

| Production concern | The file-server failure it prevents |
|---|---|
| Path scoping / allowlists | The agent reads `/etc/shadow` or `C:\Windows\system32` |
| Symlink & traversal defense | A symlink inside a workspace escapes to the whole disk |
| Read-only vs read-write modes | A prompt-injection wipes a project |
| Payload limits | `read_file` loads a 40 GB log into the model context |
| Auth + scopes (remote) | An internet-exposed server lets anyone read your files |
| Atomic writes | A crash mid-write corrupts a user's document |
| Observability / audit | You cannot explain who deleted what, or debug a slow call |
| Tested security | CI proves traversal is blocked on every OS |

> **Goal statement:** A file-system MCP server that a skeptical engineer would
> let connect to their laptop and their production server — because the attack
> surface is *designed away*, not just patched.

---

## 2. Product requirements

### 2.1 Personas & real uses
1. **Local power user** — opencode on their own machine, stdio transport,
   editing a project folder.
2. **Team / remote** — HTTP deployment (Docker) exposing a *specific* shared
   volume, protected by authentication, used by a web app or another remote
   MCP client.

### 2.2 Functional surface (target v1)

**Tools (verbs)**
| Tool | Modes | Notes |
|---|---|---|
| `list_directory` | R, W | paginated, depth-1, sortable |
| `read_file` | R | **size cap**, text/binary sniff, offset+limit lines |
| `write_file` | W | **atomic** (temp file + rename), parent must exist |
| `edit_file` | W | find/replace with **dry-run** + count; rejects no-match |
| `create_directory` | W | no recursive mkdir by default |
| `move_entry` / `copy_entry` | W | refuse cross-root moves |
| `delete_entry` | W | moves to **trash** first when available (safer) |
| `get_metadata` | R | size, mtime, perms, **is_symlink**, real path |
| `search_files` | R | glob, bounded depth + result count |
| `get_preview` | R | first N bytes/KB + detected MIME — keeps context small |

**Resources (nouns)** — files exposed so the agent can *reference* them without a
tool round-trip:
- `mcp-fs://<workspace>/<path>` — each file under a workspace is a Resource.
- `mcp-fs://<workspace>/**` — Resource *template* for glob discovery.

**Prompts (recipes)**
- `explain-file-tree` — given a root, summarize layout & roles of key files.
- `review-changes` — list files newer than a commit/date and summarize diffs.

### 2.3 Non-functional requirements
- **Security:** see §4 — treat it as the primary requirement.
- **Perf:** p50 tool call < 100 ms for listing/metadata over local disk.
- **Reliability:** server never dies on a bad request; typed errors, not crashes.
- **Operability:** structured JSON logs + OpenTelemetry traces; one-command deploy.
- **Portability:** macOS, Linux, Windows (CI-tested); Python 3.10–3.13.
- **Teachability:** heavily commented, incremental milestones.

---

## 3. System architecture

```
                       ┌──────────────────────────────────────────────┐
   opencode ──────────►│                 MCP CLIENT                    │
   (local, stdio)      └───────────────┬──────────────────────────────┘
                                       │ JSON-RPC (stdio or HTTP/SSE)
┌──────────────────────────────────────▼─────────────────────────────────────┐
│                         TRANSPORT LAYER                                     │
│   stdio: mcp.run(transport="stdio")           (selected by env CONFIG)      │
│   http : streamable-http + auth (Docker/TLS)                                │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼─────────────────────────────────────┐
│                          CORE (the MCPServer)                              │
│   Server assembly: name, instructions, tool/resource/prompt registration   │
│   + Auth mode (none | bearer) + scope checks before every call              │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                       │
┌───────────────┬──────────────────────┼───────────────────┬────────────────┐
│  TOOLS        │  RESOURCES           │   SECURITY        │  CROSS-CUTTING │
│  list/read/   │  mcp-fs:// URIs      │  Workspace root   │  Config        │
│  write/edit/  │  template + schema   │  Path zone check  │  Validation    │
│  delete/...   │  read-only facade    │  Symlink resolve  │  Limits        │
│  (thin, dumb) │                      │  RBAC mode gate   │  Telemetry     │
└───────────────┴──────────────────────┼───────────────────┼────────────────┘
                                       │
                              ┌────────▼────────┐
                              │  FILESYSTEM     │  <- the ONLY place os.*
                              │  layer (vfs.py) │     calls are allowed
                              └─────────────────┘
```

**Golden rules of the layout (teach these):**
1. **Tools are thin.** They parse/validate arguments, call security checks, then
   delegate to the filesystem layer. No tool ever calls `os.open` itself.
2. **One chokepoint.** *Every* path the server touches goes through
   `security/canonical_path()` first. This is the "defense in depth single
   gate" pattern.
3. **Transport is a config switch.** The core registers tools once; stdio and
   HTTP are just two ways to serve the same object.

### 3.1 Proposed package layout

```
production-filesystem/
├── pyproject.toml            # modern packaging, console script, deps
├── Dockerfile                # non-root, read-only FS, volume mount
├── .env.example              # documented config template (no real secrets)
├── README.md                 # ops + user docs (differs from this PLAN)
├── src/
│   └── mcp_fs/
│       ├── __init__.py
│       ├── __main__.py       # python -m mcp_fs
│       ├── server.py         # assembles MCPServer + registers everything
│       ├── config.py         # pydantic-settings: env/CLI/config-file layering
│       ├── transports.py     # picks stdio vs http from config; wires auth
│       ├── tools/            # one module per tool (thin handlers)
│       │   ├── __init__.py
│       │   ├── read_write.py
│       │   ├── navigation.py
│       │   ├── search.py
│       │   └── preview.py
│       ├── resources.py      # mcp-fs:// resources + template + schema
│       ├── prompts.py        # recipe prompts
│       ├── security/         # ★ the heart of the product
│       │   ├── __init__.py
│       │   ├── canonical_path.py   # zone check, symlink, traversal
│       │   ├── modes.py            # read-only vs read-write gate
│       │   ├── scopes.py           # bearer scopes -> mode mapping (HTTP)
│       │   └── trash.py            # safe delete
│       ├── vfs.py            # ONLY module allowed to touch the real FS
│       ├── errors.py         # typed, structured MCP errors
│       ├── limits.py         # size/depth/result caps (from config)
│       └── telemetry.py      # JSON logging + OpenTelemetry spans + audit
└── tests/
    ├── unit/                 # pure logic: canonical_path, limits, parsing
    ├── integration/          # real temp dirs, real operations
    ├── security/             # adversarial: traversal, symlink escape, TOCTOU
    └── e2e/                  # full stdio + http handshakes (like Lesson 1)
```

> **Course note:** compare with Lesson 1 — a *single* `terminal_server.py` grows
> into a *package*. Show students that modularity is what makes each concern
> testable.

---

## 4. Security model (primary requirement)

### 4.1 Workspace roots
- Config declares one or more **allowed roots** (workspaces), e.g.
  `FS_ROOTS=/data/project-a,/data/shared`.
- **Default-deny:** a path that is not inside an allowed root is rejected with a
  typed error — before any OS call.
- Relative paths, `~`, `.`/`..` are resolved & normalized by `canonical_path()`.

### 4.2 `canonical_path()` — the single gate
Every incoming path runs through:
1. `expanduser` + `abspath` + `normpath` + `normcase`.
2. `os.path.realpath()` resolves symlinks.
3. **Zone check:** the resolved real path must be inside one of the allowed
   roots' realpaths (prefix + separator boundary, not naive `startswith`).
4. Return the canonical absolute path + which root it belongs to.

> Reproduce the classic bug for students: `"/data/evil".startswith("/data")
> == True` — but `/data2` also passes. And a symlink `project/link -> /etc`
> looks fine until `realpath`. These two bugs *are* the lesson.

### 4.3 Operation modes & scope gate
| Config / scope | Allows |
|---|---|
| `mode = read-only` (default) | list, read, metadata, search, preview, resources |
| `mode = read-write` | + write, edit, create, move/copy, delete |

- Local stdio: mode from `FS_MODE` env (default `read-write` for UX, but README
  strongly recommends `read-only` unless needed).
- Remote HTTP: mode is the **maximum**; each request's OAuth/bearer **scope**
  further narrows it (`files:read` / `files:write`).

### 4.4 Threat scenarios → mitigations (use as tests)
| Threat | Mitigation | Test name |
|---|---|---|
| `../../../etc/passwd` | normalize + zone check | `test_traversal_blocked` |
| symlink inside root → `/etc` | `realpath` before check | `test_symlink_escape_blocked` |
| `read_file` on 40 GB file | hard byte cap + offset/limit | `test_read_size_cap` |
| write outside root | gate applies to *all* verbs | `test_write_outside_root_blocked` |
| directory depth bomb | depth cap on recursive ops | `test_glob_depth_cap` |
| overwrite a symlink target | never follow symlinks for writes; refuse | `test_write_refuses_symlink_target` |
| prompt injection → `delete_entry` | read-only default + human confirm on bulk | `test_readonly_blocks_delete` |
| unauthenticated HTTP caller | bearer/OAuth + scope check per request | `test_http_requires_token` |

### 4.5 Safe FS operations (vfs.py)
- **Atomic write:** write to `.<name>.mcpfs-tmp-<rand>` in the same directory,
  `fsync`, then `os.replace()` (atomic on POSIX & Windows).
- **Delete:** `trash.py` moves to an OS trash / server-local trash dir first;
  permanent delete is a separate, opt-in flag.
- **Do not follow symlinks on write targets;** reject or resolve-to-real-path.
- **TOCTOU note:** document that a perfectly race-free local filesystem
  server needs `os.open(..., dir_fd=...)`; treat as a *stretch* security
  milestone (§10), not v1 — taught as "this is where file descriptors matter".

---

## 5. Multi-transport strategy

| | Local | Remote |
|---|---|---|
| Transport | `stdio` | `streamable-http` |
| Started by | opencode | Docker/`uvicorn`-style ASGI app |
| Auth | none (OS user boundary) | bearer tokens / OAuth (MCP-spec flow) |
| Config trigger | `MCP_FS_TRANSPORT=stdio` (default) | `=streamable-http` + `MCP_FS_BEARER_TOKEN` |
| TLS | n/a | reverse proxy (Caddy/Traefik) or server TLS |

Implementation note (SDK v2): the **same `MCPServer` instance** is served by
either `server.run(transport="stdio")` or the SDK's streamable-http/ASGI app —
`transports.py` just selects per config and layers auth middleware onto HTTP.

### 5.1 Auth (remote)
1. v1 minimal: static **bearer token** from env (`MCP_FS_BEARER_TOKEN`),
   scope-mapped via `scopes.py` (`files:read`/`files:write`). Compare in
   constant time (`secrets.compare_digest`).
2. v2 (stretch): MCP-spec OAuth 2.1 authorization-server flow — the SDK v2
   `auth_server_provider` hook — issuing short-lived tokens for the filesystem
   provider.
3. Always: rate-limit per token/IP at the proxy + fail closed on missing header.

---

## 6. Data contracts & validation

- **Args:** pydantic models per tool → automatic JSON-schema for MCP +
  validation errors that read like a human wrote them.
- **Typed errors** (`errors.py`): `WorkspaceNotAllowed`, `PathNotFound`,
  `PathIsDirectory`, `TooLarge`, `ReadOnlyMode`, `Unauthorized` … each maps to
  a structured error payload, so the agent *understands* why it failed and can
  recover (vs a stack trace).
- **Limits from config** (`limits.py`): `max_read_bytes`, `max_results`,
  `max_glob_depth`, `max_entry_count` per listing. Pagination cursor for
  `list_directory` and `search_files`.

---

## 7. Observability & operations

- **JSON structured logs** to stderr/stdout with `request_id`, `tool`,
  `duration_ms`, `root`, `mode`. One log line per tool call.
- **Audit trail:** write/create/delete/move are additionally emitted to an
  audit stream (read-only ops are not) — answers "who changed what, when".
- **OpenTelemetry** spans per tool (name + root + outcome); optional OTLP
  export in HTTP mode. Env-off for local stdio.
- **Health endpoint** in HTTP mode (`/healthz`) for orchestrators.

---

## 8. Packaging, CI/CD, deployment

### 8.1 Packaging
- `pyproject.toml` (PEP 621); console script
  `mcp-filesystem-server = mcp_fs.server:main`.
- Versioned, tagged; students `pip install .` or `uv tool install .`; local
  opencode config points at the installed binary (clean upgrade story vs
  Lesson 1 where opencode ran the `.py` file directly).

### 8.2 CI (GitHub Actions)
| Job | Matrix |
|---|---|
| lint + format | `ruff` (lint+format) |
| type check | `mypy` / `pyright` |
| unit + integration | Python 3.10/3.12/3.13 |
| **security tests** | all OS: ubuntu / macos / windows |
| e2e | stdio handshake + HTTP handshake with auth |
| build | wheel publish on tag |

### 8.3 Docker (HTTP mode)
- Multi-arch image, runs as **non-root UID**, read-only root FS, single writable
  mount per allowed root, `HEALTHCHECK`, no shell baked in.
- Compose example wires Caddy for TLS termination.

---

## 9. Phased roadmap (course-shaped)

Each milestone = one lesson with its own acceptance criteria.

### Phase 1 — Scaffold & core `list_directory` (read-only)
- Package layout, config, `canonical_path`, one tool, JSON logging.
- **Accept:** running via stdio from opencode lists only allowed roots;
  `../../etc/passwd` returns a typed error.
- **Teaches:** why package not single file; the path-gate pattern; env config.

### Phase 2 — Read path: `read_file`, `get_metadata`, Resources, search
- Size caps, line offsets, `mcp-fs://` Resources + template, `search_files`.
- **Accept:** the agent can "open" files via resources; a >cap file is refused.
- **Teaches:** Resources vs Tools; content limits protect context windows.

### Phase 3 — Write path (opt-in): atomic write/edit/move/delete, trash
- Read-only default; enabling write is explicit.
- **Accept:** crash-safe write (kill during write → old file intact); delete
  goes to trash; symlink write-target refused.
- **Teaches:** atomicity, least privilege, safe deletion.

### Phase 4 — HTTP transport + bearer auth + scopes
- streamable-http behind token; scope→mode gate; Docker + Caddy.
- **Accept:** `curl -H "Authorization: Bearer …"` succeeds; no token → 401;
  `files:read` token cannot write.
- **Teaches:** why local stdio is easier *and* why network needs auth; scope
  is authorization, mode is configuration.

### Phase 5 — Observability, audit, packaging, CI
- OTel spans, audit log, wheel, CI matrix incl. security tests on all 3 OS.
- **Accept:** every delete appears in audit log; `pip install` binary runs from
  opencode config; CI green on macOS/Linux/Windows.
- **Teaches:** shipping is a feature; security tests are just tests.

### Phase 6 — Hardening stretch
- TOCTOU via `dir_fd` open, OAuth 2.1 flow, multi-root isolation, fuzzing
  path inputs, performance benchmarks.

---

## 10. Open questions / decisions log
- Trash: OS trash (platform-specific) vs server-local `.trash` dir? (Lean
  local-first; OS trash on macOS is fragile in CI.)
- Binary sniffing: magic-number library vs naive heuristics? (Lean
  `python-magic` optional, heuristics default.)
- Do Resources require a schema per file type in v1? (No — plain content.)
- Should preview generate image thumbnails? (Nice stretch; skip v1.)
- Env var names to avoid clashing with a future *database* server: prefix
  everything `MCP_FS_`.

---

## 11. Definition of done (production "ready for real life")
1. Security tests green on macOS/Linux/Windows in CI.
2. Runs from an installed package (`pip install`), not a source file.
3. Read-only default for anything network-reachable; writes are opt-in + audited.
4. No tool can touch a path outside configured roots, even via symlinks.
5. Structured logs + trace per call; `/healthz` when HTTP.
6. README documents threat model, config reference, and a "before you expose
   this" security checklist.
7. Every module in the package carries teaching comments (course requirement).
```
