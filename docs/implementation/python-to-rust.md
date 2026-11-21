# Python-to-Rust Transition Plan

**Status:** Implementation in progress (Rust candidate operational; cutover gates remain)

**Scope:** Replace the production implementation in `production-filesystem` with a Rust implementation while preserving the supported MCP, CLI, configuration, security, and operational contracts.

**Target outcome:** A standalone Rust binary and container become the primary production artifacts; the Python server remains available during a measured compatibility window and is retired only after parity, security, and rollback gates pass.

---

## 1. Executive summary

The current Python server is a compact, understandable implementation with 14 MCP tools, stdio and streamable HTTP transports, workspace scoping, typed errors, token-conscious file operations, atomic writes, Git/SWE-bench helpers, and a test runner. Rust can improve startup time, steady-state memory use, distribution, concurrency control, and resistance to entire classes of runtime errors, but a rewrite can also silently change paths, schemas, error payloads, Unicode handling, subprocess behavior, or security boundaries.

This transition must therefore be a **contract-preserving, side-by-side migration**, not a big-bang rewrite and not an in-process Python/Rust hybrid. The Python implementation will act as the compatibility oracle for intended behavior, while `docs/implementation/fix-gaps.md` defines security and correctness changes that must be incorporated rather than copied as legacy behavior.

The migration has four governing rules:

1. Freeze and test the external contract before porting tool logic.
2. Build a rooted workspace capability before implementing any filesystem tool.
3. Port read-only tools before mutations, Git, or process execution.
4. Cut over by artifact and deployment configuration, with the Python binary kept as an immediate rollback target for a defined support window.

The target is not “all code rewritten in Rust.” The target is a demonstrably safer and operationally better server with no accidental client-facing breakage.

### Implementation checkpoint — 2026-11-21

The side-by-side Rust candidate now exists under `rust-filesystem/` and is
usable over both stdio and streamable HTTP. The implementation includes:

- the exact 14-tool name and input-schema surface of the Python 0.5.0 server;
- immutable environment configuration and conventional exit code `2` for
  invalid configuration;
- canonical existing, creatable, and derived workspace paths with longest-root
  ownership, symlink-escape checks, and portable patch-path rejection;
- bounded streaming reads, directory discovery, search responses, Git output,
  and test-process output;
- Ruff-native Python syntax validation and symbol outlines;
- syntax-gated atomic file writes, scoped recoverable trash, strict text patch
  preflight, duplicate-target rejection, and rollback material;
- all four Git/SWE-bench tools with repository-root containment and pathspec
  validation;
- a no-shell test runner with a separate execution permission, hard timeout,
  and cross-platform process-group/job cleanup;
- authenticated streamable HTTP with fail-closed non-loopback defaults,
  constant-time bearer verification, SDK Host/Origin validation, request-body
  limits, and graceful shutdown;
- a pinned toolchain/lockfile, strict lint policy, release build, non-root
  container definition, CI jobs, and black-box stdio/HTTP tests.

The Rust test suite currently contains unit, integration, security, real Git,
stdio protocol, and HTTP-authentication coverage. Local `cargo test
--locked --all-targets`, `cargo clippy --locked --all-targets -- -D warnings`,
and `cargo build --locked --release` pass. A local container build was not run
because the Docker daemon was unavailable; CI includes that smoke test.

This checkpoint does **not** authorize production cutover. The remaining gates
are the full Python-versus-Rust differential fixture corpus, Linux/macOS/Windows
CI results, dependency/license audit execution, SBOM/provenance automation,
benchmark acceptance, native artifact publishing, canary/soak operation,
rollback rehearsal, and the defined Python support window. The Python server
therefore remains the repository default and rollback implementation.

---

## 2. Relationship to the gap-closure plan

The migration and production-hardening work are related but distinct:

- `fix-gaps.md` defines the correct product behavior and production gates.
- this document defines how that behavior moves from Python to Rust.
- known Python vulnerabilities must not become golden compatibility behavior.
- security-sensitive contract fixtures should encode the fixed result, even if the current Python implementation initially fails them.

Before Rust becomes the default, all P0 and P1 acceptance criteria in `fix-gaps.md` must either:

1. be implemented in both languages during the overlap period; or
2. be explicitly implemented and verified in Rust, with the Python version clearly marked unsuitable for the affected deployment mode.

The preferred path is to close P0 boundary and remote-exposure issues in Python first. That provides a safer fallback and gives the Rust port a stable behavioral baseline. Performance refactors that would soon be discarded can be Rust-only when doing so does not leave the fallback dangerously exposed.

---

## 3. Why migrate, and how to decide whether to continue

Rust is a means, not a release criterion by itself. The migration should continue only if it produces measurable value.

### Expected benefits

- A single native executable without a Python interpreter or virtual environment.
- Faster cold startup for stdio clients and short-lived benchmark workers.
- Lower and more predictable resident memory use.
- Streaming I/O and bounded buffers without relying on Python object-heavy line collections.
- Explicit ownership for workspace roots, validated paths, patch plans, and temporary files.
- Safer concurrent HTTP serving with deliberate mutation locks and cancellation.
- Strong enums for configuration, errors, tool outcomes, and patch operations.
- Cross-platform artifacts and a smaller runtime container attack surface.

### Costs and risks

- The Rust MCP SDK and Python-parser adapter introduce their own API evolution risk.
- Filesystem semantics differ across Linux, macOS, and Windows.
- Python `ast` behavior is not automatically identical to a Rust-native parser.
- More build targets and supply-chain tooling are required.
- A rewrite can pass unit tests while breaking JSON schemas or edge-case output relied upon by clients.
- Team maintenance cost increases while both implementations exist.

### Continue/stop decision gate

After the read-only Rust milestone, compare it with the corrected Python baseline. Continue to the mutation phase only if:

- contract parity is complete for the five read-only tools;
- no workspace-boundary regression is open;
- cold startup or steady-state memory shows a material measured improvement;
- the official SDK behaves correctly for both required transports;
- maintainers are comfortable reviewing and operating the Rust code;
- the dependency and cross-compilation plan is sustainable.

If those conditions are not met, keep the Rust prototype isolated and continue hardening Python. Sunk implementation effort is not a reason to accept a worse operational result.

---

## 4. Goals and non-goals

### Goals

- Preserve the names, descriptions, input schemas, defaults, and supported output shapes of all 14 MCP tools unless a versioned correction is documented.
- Preserve `mcp-filesystem-server` as the canonical command name.
- Preserve existing `MCP_FS_*` environment variable names during the compatibility period.
- Preserve stdio protocol discipline: stdout is exclusively MCP protocol traffic.
- Support streamable HTTP with the authentication and safe-default model defined in `fix-gaps.md`.
- Make filesystem and process output memory-bounded.
- Produce native artifacts for the supported macOS, Linux, and Windows targets plus a non-root container.
- Keep rollback to the Python release operationally simple.
- Retire Python runtime dependencies from the final production artifact.

### Non-goals

- Changing tool names for Rust naming conventions.
- Adding tools during the parity phase.
- Preserving known vulnerabilities or demonstrably incorrect responses.
- Replacing Git or ripgrep with pure-Rust libraries merely to eliminate subprocesses.
- Embedding CPython in the final Rust server.
- Calling Rust through Python FFI as the primary migration strategy.
- Introducing a distributed service architecture or persistent database.
- Guaranteeing byte-identical log formatting when semantic field parity is sufficient.

---

## 5. Compatibility policy

Every observed difference must be classified before it is accepted.

### Category A — exact compatibility required

- MCP tool names and tool count.
- Required versus optional arguments.
- Default values and hard caps.
- Success response field names and JSON types.
- Stable `error_code` values and error object shape.
- read-only/read-write behavior and the future execution permission gate.
- line numbering, offsets, sorting, and truncation semantics.
- Git diff/patch text, modulo documented platform line-ending behavior.
- exit codes for configuration failure and normal shutdown.
- environment variable names.

### Category B — semantic compatibility required

- Absolute path formatting across operating systems.
- human-readable error messages, when `error_code` and structured fields match.
- log timestamps, ordering of independent log fields, and duration values.
- temporary filenames and trash UUID suffixes.
- execution duration and process IDs.
- JSON object key order.

### Category C — intentional versioned corrections

- patch traversal must be rejected;
- Git repository roots outside an allowed root must be rejected;
- unsafe remote HTTP configurations must fail closed;
- negative or non-finite bounds must be rejected;
- search context must actually be returned;
- file and subprocess production must be memory-bounded;
- execution permission may be separated from write permission.

Category C changes require release notes, contract fixtures for the new behavior, and—where practical—the same correction in the Python fallback.

### Compatibility manifest

Create a machine-readable manifest under `contract/` containing:

- server name and version policy;
- protocol versions supported;
- tool list and JSON schemas;
- environment variables, defaults, ranges, and interactions;
- stable response and error fields;
- intentional differences by operating system;
- deprecated behavior and removal version.

The manifest becomes the source of truth for both implementations and documentation.

---

## 6. Target repository layout

Develop Rust beside Python until cutover:

```text
mcp-server/
├── contract/
│   ├── server-manifest.json
│   ├── tool-schemas/
│   ├── cases/
│   ├── fixtures/
│   └── normalize.py
├── production-filesystem/          # Python reference/fallback
├── rust-filesystem/
│   ├── Cargo.toml
│   ├── Cargo.lock
│   ├── rust-toolchain.toml
│   ├── deny.toml
│   ├── src/
│   │   ├── main.rs
│   │   ├── lib.rs
│   │   ├── config.rs
│   │   ├── error.rs
│   │   ├── server.rs
│   │   ├── telemetry.rs
│   │   ├── workspace.rs
│   │   ├── vfs.rs
│   │   ├── python_syntax.rs
│   │   └── tools/
│   │       ├── mod.rs
│   │       ├── navigation.rs
│   │       ├── search.rs
│   │       ├── outline.rs
│   │       ├── editing.rs
│   │       ├── patch.rs
│   │       ├── git.rs
│   │       └── execution.rs
│   ├── tests/
│   │   ├── contract.rs
│   │   ├── integration/
│   │   ├── security/
│   │   └── fixtures/
│   ├── benches/
│   └── fuzz/
└── docs/implementation/
```

Start with one library crate plus one binary. The codebase is not large enough to justify many independently versioned crates at the beginning. Split crates later only when a real boundary needs independent compilation or reuse, for example a fuzzable patch parser or a parser adapter with volatile dependencies.

After cutover, rename or archive directories in a separate mechanical change. Do not combine a large path move with logic changes.

---

## 7. Target architecture

```text
MCP client
    │
    ├── stdio transport ─────────────┐
    └── authenticated HTTP transport │
                                     ▼
                           Server/router layer
                                     │
                         typed request/response DTOs
                                     │
                  ┌──────────────────┴──────────────────┐
                  ▼                                     ▼
            read operations                       mutations/exec
           shared read lock                      exclusive policy lock
                  │                                     │
                  └──────────────────┬──────────────────┘
                                     ▼
                         Workspace capability
                      validated root + relative path
                                     │
                ┌──────────────┬─────┴──────┬──────────────┐
                ▼              ▼            ▼              ▼
               VFS       Python parser      Git        Process runner
```

### Architectural boundaries

1. **Transport layer:** MCP SDK integration, authentication, cancellation, and protocol errors only.
2. **Server layer:** tool registration, input DTOs, mode/permission gates, telemetry, and response mapping.
3. **Workspace layer:** owns allowed roots and is the only source of validated path handles.
4. **Domain tools:** operate on validated workspace paths, never raw user strings.
5. **VFS/process adapters:** contain OS-specific behavior and raw I/O.

No tool should accept a plain `PathBuf` after the server boundary. Introduce types such as:

```rust
struct WorkspaceId(usize);
struct RelativeWorkspacePath(PathBuf);
struct ExistingPath { workspace: WorkspaceId, relative: RelativeWorkspacePath }
struct CreatablePath { workspace: WorkspaceId, relative: RelativeWorkspacePath }
```

Exact names may change, but the type system must distinguish untrusted input from an approved workspace-relative target.

---

## 8. Proposed Rust technology stack

Versions must be pinned in `Cargo.lock` and updated through reviewed dependency pull requests. Avoid hard-coding version numbers in documentation.

| Concern | Preferred choice | Rationale and constraint |
|---|---|---|
| MCP | official `rmcp` SDK | Official Tokio-based Rust SDK; supports stdio and streamable HTTP. Keep SDK use inside `server`/`transport` modules. |
| Async runtime | `tokio` | Required by the SDK and suitable for transport, subprocess, cancellation, and bounded channels. |
| Serialization/schema | `serde`, `serde_json`, SDK schema support/`schemars` | Typed DTOs and generated JSON schemas. Snapshot schemas because derive output can change. |
| Errors | `thiserror` plus a project `AppError` enum | Stable mapping from internal errors to current tool error objects and protocol errors. |
| Logging | `tracing`, `tracing-subscriber` | Structured stderr logging and request spans without stdout contamination. |
| Rooted filesystem | project `Workspace` abstraction; evaluate `cap-std` | Capability-relative file access reduces accidental ambient-path use. Complete a cross-platform spike before committing. |
| Traversal | `ignore` or a small explicit walker | Depth and symlink controls; default ignore behavior must be configured to match the current contract rather than accepted implicitly. |
| Globs/regex | `globset`, `regex` as needed | Bounded in-process fallback behavior. Invalid patterns map to typed errors. |
| Temporary files | carefully wrapped `tempfile` or direct same-directory creation | Temp files must be siblings, synced, and atomically replaced according to documented OS guarantees. |
| Python syntax/AST | adapter around Ruff’s parser crates | Supports syntax parsing and AST traversal without CPython. The parser API is isolated because its public crate versions and AST API may evolve. |
| Git | existing `git` CLI through `tokio::process::Command` | Preserves Git semantics and patch output; avoids behavior drift from `libgit2`. |
| Fast search | existing `rg` CLI plus Rust fallback | Preserves performance where available while retaining a dependency-free fallback. |
| HTTP policy | SDK HTTP service plus Tower/Axum middleware where needed | Authentication, host/origin validation, limits, and request correlation. Avoid a second competing server stack. |
| Testing | built-in tests, `proptest`, snapshot/golden fixtures, optional `cargo-fuzz` | Contract, parser, path, and patch coverage. |

### Parser decision

Do not use an error-tolerant Tree-sitter parse as the Python syntax gate: a syntax gate must reject invalid code with semantics close to Python’s parser. The Ruff parser is the preferred native option, but it must be hidden behind a small interface:

```rust
trait PythonAnalyzer {
    fn validate(&self, source: &str, path: &Path) -> Result<(), SyntaxError>;
    fn outline(&self, source: &str, path: &Path) -> Result<SymbolOutline, SyntaxError>;
}
```

Golden tests must compare it against the supported CPython versions for decorators, async definitions, positional-only and keyword-only arguments, type parameters, docstrings, match statements, exception groups, and version-specific syntax. If parity is not adequate, retain a documented optional CPython validator temporarily rather than weakening validation silently; the final production goal remains no Python runtime.

---

## 9. Concurrency and cancellation model

The Python implementation is mostly synchronous. A Rust HTTP server may handle multiple calls concurrently, which creates new races unless policy is explicit.

### Workspace operation classes

- **Read:** directory listing, file read, discovery, grep, outline, Git status/diff/export.
- **Mutation:** edit, write, patch, trash, revert.
- **Execution:** test command; treated as potentially mutating the entire workspace.

Use a per-workspace asynchronous read/write coordination policy:

- compatible reads may run concurrently;
- mutations take an exclusive workspace lock for validation through commit;
- execution takes an exclusive workspace lock by default because tests can modify arbitrary files;
- transport cancellation is propagated to searches and subprocesses;
- blocking filesystem parsing/work is moved to bounded blocking tasks rather than occupying Tokio workers;
- global semaphores cap concurrent searches, Git commands, and test processes.

Do not hold an async mutex across uncontrolled blocking calls. Encapsulate the complete operation in an appropriate blocking task or use async subprocess APIs with a clearly owned permit.

### Race and TOCTOU policy

Canonicalize-then-open alone is vulnerable when an attacker can replace a path component between validation and I/O. The workspace spike must evaluate handle-relative/capability-based operations on each supported OS. At minimum:

- retain a root directory handle/capability;
- resolve relative components without accepting `..`;
- disallow or safely resolve symlinks according to one documented policy;
- validate the nearest existing parent for creation;
- keep workspace mutation locked from final validation through rename;
- test path replacement races where the OS provides practical hooks.

---

## 10. Configuration migration

### Preserve during compatibility window

- `MCP_FS_ROOTS`
- `MCP_FS_MODE`
- `MCP_FS_TRANSPORT`
- `MCP_FS_LOG_LEVEL`
- `MCP_FS_LOG_FORMAT`
- `MCP_FS_MAX_READ_LINES`
- `MCP_FS_MAX_READ_BYTES`
- `MCP_FS_HOST`
- `MCP_FS_PORT`
- authentication and execution settings added by `fix-gaps.md`

### Rust configuration design

1. Parse environment variables into an immutable `Settings` struct.
2. Use enums for mode, transport, log format, and auth mode.
3. Validate numeric ranges and cross-field rules before initializing logging or transport where possible.
4. Preserve comma-separated and JSON-array root formats.
5. Canonicalize roots once and record the longest matching owner for nested roots.
6. Reject unsafe HTTP/auth combinations at startup.
7. Return exit code `2` for configuration/usage failures to match the Python CLI.
8. Print configuration errors to stderr only.
9. Add `--version`, `--check-config`, and optionally `--print-effective-config` with secrets redacted. These are additive CLI features and must not alter no-argument behavior.

### Environment compatibility tests

Run the same table of valid and invalid environment maps against Python and Rust. Normalize only human-readable Pydantic/Rust wording; compare exit status, accepted values, effective settings, and security decisions.

---

## 11. Error and response model

Define a central Rust error enum with variants corresponding to current and planned stable codes:

- `path_not_allowed`
- `path_not_found`
- `path_not_a_directory`
- `path_not_a_file`
- `file_too_large`
- `read_only_mode`
- `execution_disabled`
- `edit_target_error`
- `syntax_error`
- `invalid_patch_path`
- `patch_conflict`
- `not_a_git_repo`
- `git_repo_outside_root`
- `git_command_error`
- `invalid_argument`
- `authentication_required`
- `internal_error`

The current Python tools generally return structured error objects as tool content rather than MCP protocol errors. Preserve that behavior during parity unless an MCP-wide versioned change is approved. Internal errors should:

- log a correlation ID and full internal cause to stderr;
- return a stable, sanitized message to the client;
- never expose tokens, outside-workspace contents, raw environment variables, or uncontrolled subprocess stderr.

Derive tool response structs with Serde. Avoid constructing large anonymous `serde_json::Value` trees throughout the code; convert to dynamic JSON only at the MCP boundary.

---

## 12. Tool-by-tool porting map

| Tool | Rust module | Primary implementation notes | Parity hazards |
|---|---|---|---|
| `list_directory` | `tools/navigation.rs` | capability-relative `read_dir`, `symlink_metadata`, deterministic sort | case folding, non-UTF-8 names, symlink type, size |
| `read_file` | `tools/navigation.rs` + `vfs.rs` | streaming buffered read, line/byte caps, binary probe | newline stripping, UTF-8 replacement, offsets, total lines |
| `find_files` | `tools/navigation.rs` | bounded non-following walk and explicit glob matching | hidden directories, depth definition, symlink metadata |
| `grep_search` | `tools/search.rs` | parse `rg --json`; bounded Rust fallback | context events, regex dialect, global match cap, ordering |
| `symbol_outline` | `tools/outline.rs` + parser adapter | Ruff AST visitor, same symbol response | Python-version grammar, docstrings, decorators, line spans |
| `edit_block` | `tools/editing.rs` | exact unique substring operation, validate, atomic write | Unicode matching, newline preservation, occurrence count |
| `write_file` | `tools/editing.rs` + `vfs.rs` | syntax gate then sibling-temp atomic replacement | permissions, fsync, Windows replacement semantics |
| `apply_patch` | `tools/patch.rs` | validated patch plan, context checks, transaction/rollback | traversal, `/dev/null`, no-newline, partial commits |
| `delete_entry` | `tools/editing.rs` + `vfs.rs` | root-scoped `.trash`, collision-safe name, rename | deleting root/trash itself, cross-device behavior |
| `git_status` | `tools/git.rs` | bounded Git CLI, porcelain parser | paths with spaces/quotes, repo root, worktrees |
| `git_diff` | `tools/git.rs` | Git CLI, pathspec validation, line/byte caps | stat parsing, binary diffs, output truncation |
| `export_swebench_patch` | `tools/git.rs` | exact `git diff HEAD`, untracked audit | outside-root repo data, untracked quoting |
| `revert_file` | `tools/git.rs` | tracked restore or root-scoped trash | deleted tracked files, untracked paths, index behavior |
| `run_test` | `tools/execution.rs` | no shell, bounded streams, process groups, cancellation | quoting, child cleanup, Windows behavior, output parser |

### Non-UTF-8 filesystem names

Rust paths are not necessarily UTF-8, while MCP JSON strings are. Create an ADR before implementation. Recommended policy:

- perform filesystem operations with `OsStr`/`Path` without lossy conversion;
- return a typed `unsupported_path_encoding` result when a name must cross the JSON boundary and is not valid UTF-8;
- do not silently replace characters in security decisions;
- add Unix-only fixtures for non-UTF-8 names.

---

## 13. Security model in Rust

### Workspace capability

Create a `WorkspaceSet` at startup. It owns canonical root capabilities and provides all path resolution:

```rust
impl WorkspaceSet {
    fn resolve_existing(&self, input: &str) -> Result<ExistingPath, AppError>;
    fn resolve_creatable(&self, input: &str) -> Result<CreatablePath, AppError>;
    fn resolve_under(
        &self,
        base: &ExistingDirectory,
        relative: &str,
    ) -> Result<CreatablePath, AppError>;
}
```

Tools must not call `std::fs::canonicalize` independently or combine approved bases with raw child paths. Patch paths, Git pathspecs, trash paths, and search traversal all go through the same capability.

### Security invariants

- No raw user path reaches VFS, Git, or process working-directory code.
- An approved path records its owning root.
- Derived paths cannot jump to a second configured root.
- Symlinks are resolved or rejected consistently before access.
- Root directories and `.trash` receive explicit protection from self-deletion.
- Git repository discovery cannot expand authority above the owning root.
- HTTP non-loopback startup is authenticated and fail-closed.
- process execution requires a separate permission and authenticated remote context.
- authorization secrets use secrecy-aware wrappers and never implement accidental debug output.

### Security testing

Port every Python adversarial case, then add:

- property tests generating path component sequences;
- Windows drive, device, UNC, alternate separator, and case-normalization cases;
- Unix non-UTF-8 and symlink race cases;
- patch filename fuzzing;
- Git porcelain/path quoting fixtures;
- HTTP auth/header fuzz cases;
- subprocess output and process-tree stress tests.

---

## 14. Atomic writes and patch transactions

### Single-file writes

The Rust VFS must define and test the sequence explicitly:

1. resolve and hold the validated parent capability;
2. create a collision-resistant temporary file in the target directory;
3. write the complete content with bounded input size;
4. flush and sync the file;
5. preserve or intentionally set permissions according to documented policy;
6. atomically replace/rename using the best supported operation per OS;
7. sync the parent directory where meaningful and supported;
8. clean up the temp file on every recoverable failure.

Do not assume a crate’s `persist` operation provides identical atomicity and durability on every platform. Wrap platform behavior behind VFS and verify it with fault-injection tests.

### Multi-file patches

Represent a parsed patch as immutable operations over validated paths:

```rust
enum PatchOperation {
    Create { target: CreatablePath, content: Vec<u8> },
    Modify { target: ExistingPath, original_digest: Digest, content: Vec<u8> },
    Delete { target: ExistingPath, original_digest: Digest },
}
```

Parse, resolve, load, validate context, construct results, syntax-check all Python outputs, and stage all temporary files before committing. Hold the workspace mutation permit throughout final validation and commit. Record rollback material before the first rename. If full cross-platform transaction guarantees are impossible, document the exact guarantee and never report success after an incomplete commit.

---

## 15. Git and search subprocess strategy

Retaining `git` and optionally `rg` is deliberate:

- SWE-bench expects Git-compatible patch semantics.
- the current response behavior already derives from Git CLI output.
- replacing Git with `libgit2` creates a second semantic migration with little immediate value.
- ripgrep’s JSON format is suited to structured bounded parsing.

### Command adapter requirements

- use argument arrays, never a shell;
- fixed executable name resolved from a controlled `PATH` policy;
- `GIT_TERMINAL_PROMPT=0` and noninteractive settings;
- bounded stdout/stderr readers;
- timeout and cancellation support;
- full process-tree termination where applicable;
- sanitized error text;
- per-command concurrency permits;
- no user-controlled option injection before `--` path separators.

Search must retain a Rust fallback for environments without `rg`. Contract tests run both backends over the same corpus and require equivalent match/context semantics.

---

## 16. Contract and differential test harness

### Black-box testing

Exercise both implementations as real child processes over stdio. Do not compare only internal function calls. For each case:

1. create an isolated fixture workspace;
2. start the server with a specified environment;
3. initialize MCP using the same negotiated protocol version;
4. list tools and snapshot schemas;
5. call the target tool;
6. normalize approved nondeterminism;
7. compare JSON results and the resulting filesystem/Git state;
8. stop the server and assert clean process exit.

HTTP contract tests repeat initialization and selected calls through authenticated streamable HTTP.

### Fixture classes

- small text/Python repositories;
- binary files and mixed encodings;
- long lines and large sparse/generated files;
- nested, missing, symlinked, and adversarial paths;
- staged, unstaged, renamed, deleted, and untracked Git states;
- valid/invalid Python across supported grammar features;
- passing, failing, hanging, child-spawning, and noisy test commands;
- valid, stale, malicious, multi-file, and unsupported patches.

### Normalization rules

Normalize only fields declared nondeterministic:

- temporary directory prefixes;
- duration/timestamp values;
- UUID trash suffixes;
- OS path separators when the semantic path is equivalent;
- JSON key order.

Do not normalize away line ranges, error codes, match ordering, truncation flags, schemas, file contents, or security decisions.

### Write-tool comparison

Never run Python and Rust mutations against the same fixture directory. Clone an identical fixture for each server, call the same operation, then compare:

- response;
- full file tree including modes where relevant;
- Git status/diff;
- absence of leftover temporary files;
- absence of changes outside the root.

---

## 17. Performance and resource baseline

Measure before setting absolute targets. Add a reproducible benchmark harness that records:

- cold start to successful MCP initialize;
- idle resident memory after initialization;
- `tools/list` latency;
- small and large sliced reads;
- traversal over representative repositories;
- grep with `rg` and fallback;
- Python outline extraction;
- edit/write/patch latency and temporary allocation;
- Git status/diff on small and large repositories;
- successful, failing, noisy, and timed-out test execution;
- HTTP concurrency under read-heavy and mixed workloads.

### Initial success criteria

- no benchmark regresses materially without a documented tradeoff;
- cold startup and idle RSS improve enough to justify native distribution;
- memory for file reads and subprocess output stays within configured caps regardless of source size;
- concurrent reads scale without allowing concurrent mutations to corrupt state;
- artifact size and container size are tracked, not assumed to improve;
- results are stored as CI artifacts and compared on dedicated benchmark runs, not used as flaky per-commit gates initially.

Use representative fixtures rather than microbenchmarks alone. Optimize only after contract and security parity.

---

## 18. Migration phases

### Phase 0 — approve architecture and freeze scope

Deliverables:

- ADR: side-by-side rewrite and rollback strategy;
- ADR: rooted filesystem/capability approach;
- ADR: Python parser selection and supported Python grammar;
- ADR: non-UTF-8 path policy;
- ADR: native/PyPI distribution strategy;
- initial baseline benchmarks;
- feature freeze for new Python tool names during parity work.

Exit criteria:

- maintainers agree on compatibility categories and supported platforms;
- no unresolved question changes the fundamental package or security architecture.

### Phase 1 — create the language-neutral contract

Deliverables:

- tool manifest and schema snapshots;
- environment/configuration cases;
- golden fixture corpus;
- black-box stdio runner;
- normalization rules;
- expected security behavior from `fix-gaps.md`.

Exit criteria:

- current intended Python behavior is reproducible from a clean checkout;
- known bugs are marked as expected failures or corrected expectations, never silently blessed.

### Phase 2 — Rust skeleton and transport handshake

Deliverables:

- `rust-filesystem` crate and pinned toolchain;
- formatting, linting, unit-test, audit, and license CI;
- config and error types;
- tracing restricted to stderr;
- official SDK stdio server exposing placeholder or minimal tools;
- streamable HTTP startup with authentication policy scaffolding;
- graceful shutdown and signal handling.

Exit criteria:

- MCP initialize and `tools/list` work through both transports;
- stdout protocol-discipline tests pass;
- invalid configuration matches expected exit behavior.

### Phase 3 — workspace capability and VFS foundation

Deliverables:

- canonical root initialization;
- existing/creatable/derived path APIs;
- symlink and owning-root policy;
- atomic single-file write primitive;
- scoped trash primitive;
- unit, property, adversarial, and OS-specific tests.

Exit criteria:

- no tool work proceeds until boundary tests pass on Linux, macOS, and Windows;
- threat-model review signs off on the rooted path design.

### Phase 4 — read-only tool parity

Port in this order:

1. `list_directory`
2. `read_file`
3. `find_files`
4. `grep_search`
5. `symbol_outline`

Deliverables:

- exact/semantic differential tests;
- bounded-memory read and search implementations;
- parser parity corpus;
- read-only benchmark comparison.

Exit criteria:

- five tools pass contract and security cases on the OS matrix;
- the continue/stop decision in section 3 is recorded.

### Phase 5 — mutation tool parity

Port in this order:

1. `edit_block`
2. `write_file`
3. `delete_entry`
4. `apply_patch`

Deliverables:

- syntax-gated atomic writes;
- root-scoped trash;
- secure patch parser/plan/commit flow;
- fault injection and rollback tests;
- filesystem state differential comparison.

Exit criteria:

- no partial mutation on validation failure;
- every path is capability-derived;
- known patch traversal and correctness gaps are closed.

### Phase 6 — Git and execution parity

Port Git tools before process execution:

1. `git_status`
2. `git_diff`
3. `export_swebench_patch`
4. `revert_file`
5. `run_test`

Deliverables:

- bounded command adapter;
- repo-root and pathspec enforcement;
- process-group cancellation;
- test-output distillation parity;
- explicit execution permission gate.

Exit criteria:

- Git output and resulting states match fixtures;
- timeouts leave no descendant processes;
- remote execution requires authenticated opt-in.

### Phase 7 — operational and packaging parity

Deliverables:

- release-mode native binaries;
- non-root multi-stage/multi-architecture container;
- artifact checksums and SBOM;
- configuration reference and migration guide;
- authenticated HTTP load/cancellation tests;
- startup/RSS/artifact benchmarks;
- observability field mapping and secret-redaction tests.

Exit criteria:

- all documented installation paths are tested from built artifacts;
- the container completes a real authenticated MCP handshake.

### Phase 8 — beta and shadow comparison

Deliverables:

- Rust prerelease distributed separately but with the final command interface;
- automated nightly differential corpus;
- opt-in canary deployments;
- compatibility issue template and dashboard;
- rollback runbook exercised in staging.

For read-only traffic, shadow requests may be replayed against an isolated Rust fixture. Do not replay live mutations or test commands. Compare sanitized results offline.

Exit criteria:

- no open P0/P1 parity or security issue;
- soak period completes with no unexplained crashes, hangs, or memory growth;
- rollback drill succeeds.

### Phase 9 — controlled cutover

1. Tag the final Python fallback release.
2. Publish Rust native artifacts and container under a release-candidate tag.
3. Switch internal/opencode configurations to the Rust executable for read-only use first.
4. Enable write tools, then execution, in separate rollout steps.
5. Promote the Rust container tag only after telemetry and contract probes remain healthy.
6. Keep the Python artifact and documentation immediately available for rollback.

Exit criteria:

- Rust is the default in repository examples, container, and release documentation;
- all production definition-of-done checks pass from built artifacts.

### Phase 10 — Python retirement

After the compatibility window:

- mark the Python package deprecated;
- stop feature development and accept only critical security fixes until end-of-support;
- remove it from the default container and CI fast path;
- preserve source/tag history and the differential corpus;
- archive or move it in a dedicated cleanup pull request;
- remove the fallback only after the rollback window closes.

Do not delete Python history or fixtures: they explain compatibility decisions and remain useful regression inputs.

---

## 19. Distribution strategy

### Primary artifacts

- standalone native binaries for supported targets;
- OCI image for Linux deployments;
- source archive with locked dependencies;
- checksums, provenance/attestations, and SBOM.

### Target matrix

At minimum evaluate and document:

- Linux x86_64 GNU;
- Linux ARM64 GNU;
- macOS x86_64;
- macOS ARM64;
- Windows x86_64 MSVC.

Musl/static Linux should be added only after Git, CA certificates, DNS, process, and parser behavior are verified. “Static binary” is not automatically a simpler production artifact when the server still invokes Git and optional ripgrep.

### PyPI compatibility decision

The current install story uses `pip install` and a console script. Choose one policy in Phase 0:

1. **Native-first:** document direct binary/package-manager installation and retain the Python package only as the old implementation during the transition.
2. **Transitional PyPI launcher:** publish platform wheels that contain or securely locate the native binary while retaining the `mcp-filesystem-server` command.

Native-first is simpler and preferred unless actual consumers require PyPI. Do not publish a launcher that downloads an executable at runtime without pinning, verification, offline behavior, and a clear supply-chain review.

### Command compatibility

The installed executable remains:

```text
mcp-filesystem-server
```

Do not repeat the existing Docker mismatch by introducing an undocumented alias as the container entrypoint.

---

## 20. CI and supply-chain plan

### Required pull-request checks

- `cargo fmt --check`
- `cargo clippy` with warnings denied under an agreed lint policy
- unit, integration, contract, and security tests
- supported OS build/test matrix
- schema snapshot comparison
- Python-versus-Rust differential tests while Python is supported
- dependency vulnerability audit
- license/source policy with `cargo-deny` or equivalent
- no duplicate critical dependency versions without review
- release build and binary smoke test
- container build and MCP handshake

### Scheduled/deeper checks

- property tests with extended case counts;
- patch/path/parser fuzzing;
- sanitizer runs on supported nightly targets;
- Miri for suitable unsafe-free core components where useful;
- dependency update trials;
- benchmark and memory regression runs;
- multi-architecture container builds.

### Dependency policy

- Commit `Cargo.lock` because this is an application.
- Minimize default features.
- Keep MCP SDK, parser, HTTP, and capability filesystem dependencies behind narrow adapters.
- Review build scripts, native dependencies, licenses, and maintenance state.
- Forbid new `unsafe` code by default; any exception requires a documented safety invariant and focused review.
- Generate an SBOM and archive dependency audit output with releases.

---

## 21. Observability and operations

Preserve the useful Python log fields while moving to tracing spans:

- request/correlation ID;
- transport and authenticated principal category, never token;
- tool name;
- workspace identifier or redacted root label;
- outcome and stable error code;
- duration;
- bytes read/returned;
- truncation flags;
- subprocess exit/timeout status.

### Operational requirements

- stdout remains protocol-only in stdio mode;
- secrets, file content, patches, raw authorization headers, and uncontrolled subprocess output are never logged by default;
- log format and level honor existing environment settings;
- panic hooks produce stderr diagnostics and a failing exit without protocol garbage;
- graceful shutdown cancels or terminates owned subprocesses and stops accepting HTTP calls;
- health/readiness reveals no workspace or authentication details;
- metrics are optional and must not require a new network listener by default.

During canary rollout, compare crash rate, startup latency, RSS, tool error-code distribution, truncation rate, timeout cleanup, and HTTP request cancellation with the Python baseline.

---

## 22. Rollout and rollback runbook

### Rollout units

Roll out capabilities independently:

1. stdio read-only;
2. stdio mutations;
3. stdio execution;
4. loopback HTTP read-only;
5. authenticated remote HTTP read-only;
6. authenticated remote mutations;
7. authenticated remote execution, only where explicitly required.

### Rollback triggers

- any workspace boundary violation;
- schema/tool-list incompatibility that blocks a supported client;
- file corruption or partial patch success;
- orphaned test processes after timeout;
- authentication bypass or secret leakage;
- unexplained panic/crash loop;
- sustained resource regression beyond the approved threshold;
- Git patch output unsuitable for SWE-bench.

### Rollback procedure

1. Disable new write/execute traffic if the deployment platform supports capability flags.
2. Switch command/container tag back to the pinned Python fallback.
3. Preserve Rust logs, version, configuration checksum, and affected fixture/workspace evidence.
4. Verify workspace Git state before retrying mutations.
5. Reproduce through the contract harness.
6. Add the case as a permanent regression fixture before re-enabling Rust.

Rollback must not require source changes or a rebuild. Keep immutable known-good Python and Rust artifact references throughout the support window.

---

## 23. Major risks and mitigations

| Risk | Likelihood/impact | Mitigation |
|---|---|---|
| Port reproduces known security gaps | High/high | Treat `fix-gaps.md` as target behavior; security fixtures precede tools. |
| MCP SDK breaking change | Medium/high | Pin lockfile; isolate SDK adapter; snapshot schemas; upgrade separately. |
| Python parser semantic drift | Medium/high | Parser trait, CPython corpus, target-version policy, delayed parser upgrades. |
| Cross-platform atomic-write mismatch | High/high | VFS adapter, fault injection, OS CI, documented guarantees. |
| Path TOCTOU remains despite canonicalization | Medium/high | capability/handle-relative spike, mutation locks, adversarial tests. |
| Async server introduces races | Medium/high | per-workspace read/write policy and bounded concurrency. |
| Differential tests bless bugs | High/high | classify differences; corrected security expectations override oracle. |
| Dual implementation period lasts indefinitely | Medium/medium | feature freeze, milestone exit gates, published Python end-of-support date after cutover. |
| Rust artifact breaks pip-based users | Medium/medium | consumer inventory and explicit PyPI/native distribution ADR. |
| Native binary/container grows unexpectedly | Medium/low | measure stripped artifacts and image layers; do not assume size benefit. |
| Git/rg subprocess differences | Medium/medium | pinned minimum tool versions, argument adapter, fixtures, fallback tests. |
| Team cannot maintain Rust confidently | Medium/high | review ownership, coding standards, focused modules, stop gate after read-only milestone. |

---

## 24. Work breakdown and dependency order

| Work package | Size | Depends on | Parallelizable with |
|---|---:|---|---|
| ADRs and consumer inventory | M | none | Python P0 fixes |
| Contract manifest/harness | L | compatibility policy | Rust skeleton |
| Rust SDK/config/error skeleton | M | SDK spike | contract fixtures |
| Workspace capability/VFS | XL | filesystem ADR | parser spike |
| Parser/outline adapter | L | parser ADR | navigation tools |
| Read-only tools | L | workspace/VFS | HTTP auth |
| Mutation and patch tools | XL | VFS, gap plan | Git adapter |
| Git tools | L | workspace capability | patch work |
| Process runner | XL | concurrency model | packaging |
| HTTP auth/operations | L | SDK skeleton, auth contract | read tools |
| Differential/security corpus | XL, continuous | contract harness | every implementation phase |
| Native/container packaging | L | runnable server | docs and benchmarks |
| Canary/cutover | L | all release gates | Python deprecation prep |

Sizes are relative complexity indicators, not calendar commitments. Do not schedule mutation work before the workspace capability is accepted even if developers are available.

---

## 25. Documentation deliverables

Update or add:

- architecture overview for the Rust layers and concurrency model;
- configuration reference shared by Python and Rust during overlap;
- tool contract reference generated from the manifest;
- native binary and container installation guides;
- Python-to-Rust migration guide for existing users;
- security model and threat boundaries;
- supported Python syntax version policy;
- platform support and known filesystem differences;
- release/rollback runbook;
- Python deprecation timeline;
- benchmark methodology and results.

Documentation must distinguish current reality, beta capability, and planned work. Do not change the main README to call Rust production-ready until the cutover gates pass.

---

## 26. Final Rust cutover definition of done

- [ ] All P0 and P1 items in `fix-gaps.md` are closed for the Rust production path.
- [ ] The contract manifest covers all 14 tools, configuration, errors, transports, and permissions.
- [ ] Rust passes every required unit, integration, security, contract, and differential test.
- [ ] Tool schemas are compatible or an intentional versioned difference is documented.
- [ ] Workspace capability review finds no path that bypasses owning-root validation.
- [ ] Patch paths, Git repository discovery, trash, and process working directories cannot cross an allowed root.
- [ ] File, search, Git, HTTP, and subprocess outputs are bounded during production, not merely before serialization.
- [ ] Atomic-write and multi-file patch guarantees are documented and verified on Linux, macOS, and Windows.
- [ ] Python syntax validation and symbol outlines pass the supported grammar corpus.
- [ ] stdio and authenticated streamable HTTP complete real client handshakes from built artifacts.
- [ ] Test timeout/cancellation leaves no child processes behind.
- [ ] Native binaries and the non-root container pass smoke tests on supported targets.
- [ ] Dependency audit, license policy, SBOM, checksums, and release provenance are available.
- [ ] Startup, RSS, latency, and artifact benchmarks have been compared with the Python baseline and accepted.
- [ ] Canary and soak periods complete without an open P0/P1 issue.
- [ ] The rollback procedure has been exercised using immutable artifacts.
- [ ] Existing user configuration has a documented migration path.
- [ ] Rust is the default in client examples, release artifacts, Docker, and production documentation.
- [ ] Python has a published support/deprecation window and remains available until that window ends.

Rust should become the default only when it is measurably safer or more operable and demonstrably compatible—not merely when every Python source file has a Rust counterpart.

---

## 27. Ecosystem references

Technology choices should be revalidated when implementation begins. As of the planning date:

- the [official MCP Rust SDK](https://github.com/modelcontextprotocol/rust-sdk) is Tokio-based and documents both stdio and streamable HTTP server transports;
- Ruff exposes a [Rust Python parser and AST](https://github.com/astral-sh/ruff/tree/main/crates/ruff_python_parser), suitable for evaluation behind a project-owned adapter;
- [`cap-std`](https://docs.rs/cap-std/latest/cap_std/fs/struct.Dir.html) provides capability-oriented directory APIs worth evaluating for rooted I/O;
- the [`ignore`](https://docs.rs/ignore/latest/ignore/) crate supports bounded, non-symlink-following traversal with configurable ignore behavior;
- temporary-file persistence has platform-specific security and atomicity caveats, documented by crates such as [`tempfile`](https://docs.rs/tempfile/latest/tempfile/struct.NamedTempFile.html), so VFS guarantees must be tested rather than assumed.
