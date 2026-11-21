# Production Hardening and Gap-Closure Plan

**Status:** Implemented on 2026-11-21; local validation complete, container/remote CI verification pending

**Scope:** `production-filesystem` MCP server, packaging, CI, examples, and supporting documentation

**Primary objective:** Move the project from a feature-complete prototype to a release candidate whose security boundaries, remote deployment, resource limits, and published claims are demonstrably correct.

### Implementation result

The plan has been implemented in both the hardened Python fallback and the
side-by-side Rust candidate. The Python implementation now has point-of-use
patch and Git containment, strict transactional text patches, fail-closed HTTP
authentication, a separate execution permission, bounded streaming and
subprocess capture, process-tree termination, corrected search context,
validated immutable configuration, safe container defaults, repaired package
metadata/entrypoint, release CI jobs, and synchronized examples/documentation.

Local verification completed with 139 Python tests at approximately 80% statement coverage,
Ruff and compile checks, an executable SWE-bench demo, wheel and sdist builds
installed into clean Python 3.12 environments, 29 Rust tests, strict Clippy,
formatting, a release build, and the Python/Rust stdio differential probe. The
Docker daemon is unavailable on the development host, so both authenticated
container handshakes remain delegated to the required GitHub Actions jobs.

---

## 1. Executive summary

The server already has the intended product surface: 14 MCP tools, stdio and streamable HTTP transports, typed errors, workspace scoping, atomic writes, Python syntax validation, Git/SWE-bench helpers, a bounded test runner, a Dockerfile, and a multi-platform test workflow. The current suite passes 122 tests on the local Python 3.12 environment.

The remaining work is not another feature phase. It is a production-hardening phase. Several gaps invalidate the strongest production claims:

1. `apply_patch` can construct paths outside an allowed root after the initial root check.
2. Git helpers can discover and operate from a repository root above the configured allowed root.
3. the Docker defaults expose a read-write, command-executing HTTP server on all interfaces without authentication;
4. the Docker entrypoint references a command that the package does not install;
5. bounded file responses and test output are not consistently bounded in memory while being produced;
6. the patch engine, search context handling, demo, CI, and documentation have correctness or coverage gaps.

The release should therefore be gated on the P0 and P1 workstreams below. Documentation cleanup alone must not be used to declare the project production-ready.

---

## 2. Goals and non-goals

### Goals

- Make the configured allowed roots a true boundary for every read, write, Git, patch, search, trash, and execution operation.
- Make unsafe remote configurations fail closed at startup.
- Make the published Docker image start successfully and run as a non-root user.
- Bound memory use as well as response size for file reads, searches, diffs, and subprocess output.
- Make unified-diff behavior predictable, validated, and tested for existing, new, and deleted files.
- Expand CI from a test-only workflow into a release-quality verification pipeline.
- Synchronize implementation, examples, package metadata, and documentation.
- Preserve the current MCP tool names and successful response shapes where practical.

### Non-goals

- Adding unrelated MCP tools or another transport.
- Turning the server into a general-purpose remote shell service.
- Supporting every Git patch feature in the first security-fix pull request.
- Replacing the official MCP SDK.
- Reworking the separate course-oriented `terminal_server.py` unless needed to clearly distinguish it from the production package.

---

## 3. Release gates and priority

| Priority | Workstream | Release impact | Completion gate |
|---|---|---|---|
| P0 | Patch path containment | Security blocker | No patch path can read, create, overwrite, delete, or create parents outside an allowed root. |
| P0 | Git/root containment | Security blocker | Git operations cannot reveal or mutate files outside the configured root. |
| P0 | HTTP authentication and safe defaults | Remote-deployment blocker | Non-loopback HTTP cannot start without an explicit authentication configuration. |
| P0 | Docker entrypoint | Packaging blocker | Built image starts and serves MCP using the installed console script. |
| P1 | Patch correctness and transactions | Data-integrity blocker | Context is validated; supported patch types are explicit; failed multi-file patches do not silently leave partial changes. |
| P1 | Memory and output bounds | Reliability blocker | Large files and noisy subprocesses remain memory-bounded. |
| P1 | Execution containment | Reliability/security blocker | Timeouts terminate process trees and output capture is capped. |
| P1 | CI release verification | Confidence blocker | CI builds, installs, tests, scans, and smoke-tests the package and image. |
| P2 | Search/input correctness | Quality | Context lines work on both backends and invalid bounds receive structured errors. |
| P2 | Demo/docs/metadata alignment | Quality | All examples and claims match the shipped behavior. |

P0 items should be fixed before any remote deployment or production claim. P1 items should be fixed before the first stable release. P2 items may be split into follow-up pull requests, but should be complete before documentation calls the server fully production-ready.

---

## 4. Cross-cutting implementation rules

The following invariants should guide every workstream:

1. **Resolve at the point of use.** A checked parent directory does not make a subsequently joined child path safe. Every derived path must pass the same canonical boundary check immediately before I/O.
2. **Fail closed.** Invalid paths, unsupported patch constructs, unsafe HTTP configurations, and invalid limits must return typed errors or prevent startup. Do not silently normalize a dangerous value into something permissive.
3. **Validate before mutation.** Parse and validate all files in a multi-file operation before committing the first change.
4. **Bound production, not just response serialization.** A 200-line response is not safe if the implementation first loads a 40 GB file or unlimited subprocess output into memory.
5. **Do not weaken stdio ergonomics.** Local stdio should remain zero-auth because access is already controlled by the parent process and OS account.
6. **Keep compatibility intentional.** If a response field or configuration name changes, document the migration and test the old behavior when compatibility is promised.
7. **Test every discovered regression.** Each gap below requires a failing regression test before or alongside its fix.

---

## 5. Workstream A — close patch boundary escapes (P0)

### Problem

`apply_patch` validates its base `path`, but parsed patch filenames are later joined using `canonical / rel_path`. Those derived paths are read and written without another call to `resolve_allowed_path`. Absolute paths, `..` components, crafted separators, and symlinked parents can therefore bypass the workspace boundary. New-file handling can also call `mkdir(parents=True)` outside the root before the VFS write.

### Implementation

1. Add a single helper for resolving patch target paths. It should:
   - reject NUL bytes;
   - reject absolute paths on POSIX and Windows, including drive-qualified and UNC paths;
   - strip only recognized Git prefixes (`a/` and `b/`) during parsing, not arbitrary leading components;
   - reject empty paths, `.` targets, and any lexical `..` component;
   - resolve the final candidate through `resolve_allowed_path(..., must_exist=False)`;
   - verify that the resolved candidate remains under the specific base directory passed to `apply_patch`, not merely under some other configured root;
   - resolve and validate the nearest existing parent so a symlinked parent cannot redirect a new file outside the workspace.
2. Perform this resolution before `editing.apply_patch` reads an existing target.
3. Pass resolved `Path` objects through the parsed patch plan. Do not reconstruct paths from untrusted relative strings in `server.py`.
4. Move parent-directory creation behind the validated-path helper and into the VFS layer.
5. Add a typed error such as `InvalidPatchPathError` with `error_code="invalid_patch_path"` rather than returning a generic internal error.
6. Ensure error messages do not echo sensitive outside-file contents.

### Required tests

- Existing-file patch containing `b/../../outside.txt` is rejected and the outside file is unchanged.
- New-file patch containing `b/../../outside.txt` is rejected and no outside directory is created.
- Absolute POSIX, Windows drive, and UNC patch paths are rejected on every platform.
- NUL-containing and empty patch paths are rejected.
- A parent symlink inside the workspace pointing outside is rejected.
- A symlink that resolves to a location inside the same allowed root remains usable if that is the intended canonical-path policy.
- A base directory inside allowed root A cannot direct a patch into allowed root B through `..`.
- Valid nested paths continue to work.

### Acceptance criteria

- All patch targets are canonical, validated objects before any read or write.
- A test can assert that no external file or directory changed for every malicious case.
- No `mkdir`, `read_text`, `open`, or VFS write receives a path derived only by `base / user_value`.

---

## 6. Workstream B — enforce Git boundary containment (P0)

### Problem

Git helpers call `git rev-parse --show-toplevel` and then operate on that result. If the configured allowed root is a subdirectory of a larger repository, the discovered repository root is outside the security boundary. `git_status`, `git_diff`, and `export_swebench_patch` can then reveal sibling changes. `revert_file` can place trash under that outside repository root.

### Recommended policy

Use the safest initial rule: **the discovered Git repository root must itself be inside an allowed root**. If an administrator wants whole-repository Git operations, they must configure the repository root as an allowed root.

This rule is easier to reason about than attempting to path-filter every Git status, rename, submodule, diff, and untracked-file edge case.

### Implementation

1. Change Git tool entry points to receive the applicable allowed root or a boundary-validation callback.
2. After `_get_repo_root`, canonicalize the returned path and verify it is within the same allowed root as the requested path.
3. Return a typed `git_repo_outside_root` error when the repository root is above the boundary.
4. Ensure user-supplied `git_diff(paths=...)` pathspecs are relative, cannot contain traversal, and resolve within the repository/allowed root.
5. Keep `.trash` anchored to the configured allowed root, never to an unchecked repository root.
6. Validate worktree and submodule behavior. Either support them explicitly or reject roots whose Git common directory/worktree arrangement crosses the boundary.
7. Consider invoking Git with `--no-optional-locks` for read-only commands and set predictable environment values such as `GIT_TERMINAL_PROMPT=0`.

### Required tests

- Allowed root equal to repository root: all Git tools continue to work.
- Allowed root is a repository subdirectory: status, diff, export, and revert return `git_repo_outside_root`.
- A pathspec containing `../` or an absolute path is rejected.
- An untracked file reverted inside the allowed root moves only to `<allowed-root>/.trash`.
- Worktree and submodule cases follow the documented policy.
- Git commands never prompt for credentials.

### Acceptance criteria

- No Git response includes a path or content outside the configured root.
- No Git write or trash operation targets a directory outside the configured root.

---

## 7. Workstream C — secure HTTP transport and configuration (P0)

### Problem

The current HTTP path is intentionally zero-auth, while the container defaults to `MCP_FS_HOST=0.0.0.0` and `MCP_FS_MODE=read-write`. Because `run_test` executes arbitrary programs, this is effectively unauthenticated remote code execution whenever port 8000 is reachable. Documentation simultaneously claims bearer-token protection in some places and zero-auth operation in others.

### Security model

- `stdio`: no application-level authentication.
- HTTP bound to loopback: zero-auth may be permitted, with an explicit warning.
- HTTP bound to a non-loopback address: bearer authentication is mandatory by default.
- An explicit development-only override may exist, but it must be conspicuous, disabled by default, and produce a high-severity startup warning.

### Configuration additions

Add validated settings similar to:

- `MCP_FS_AUTH_MODE=auto|bearer|none` (default `auto`);
- `MCP_FS_AUTH_TOKEN` or preferably `MCP_FS_AUTH_TOKEN_FILE`;
- `MCP_FS_ALLOW_UNSAFE_REMOTE=false`;
- optional allowed hosts/origins when browser-based clients are supported.

`auto` should resolve to `none` for stdio/loopback and `bearer` for non-loopback HTTP. Startup must fail if bearer mode lacks a usable secret. Tokens must never be logged or included in errors.

### Implementation

1. Add authentication middleware around the streamable HTTP application.
2. Compare bearer tokens using constant-time comparison.
3. Apply authentication to all MCP endpoints and session operations.
4. Validate `Host` and `Origin` according to the MCP SDK’s current HTTP security guidance.
5. Return consistent `401` and `403` responses without disclosing configuration details.
6. Default the container to read-only unless a deployer explicitly opts into read-write/execute capability.
7. Keep secrets out of image layers and examples; document runtime injection through environment secrets or mounted files.
8. Add startup logs that state transport, bind address, mode, and whether auth is enabled, but never the token.

### Required tests

- Loopback HTTP works without a token under `auth_mode=auto`.
- Non-loopback HTTP fails startup when no token is configured.
- Missing, malformed, and incorrect bearer tokens are rejected.
- A correct token permits MCP initialization and tool calls.
- Tokens do not appear in captured logs or structured errors.
- Explicit unsafe mode requires both the override and a clear warning.
- stdio behavior is unchanged.

### Acceptance criteria

- A default container cannot expose unauthenticated write or command-execution tools on the network.
- Documentation has one consistent, tested authentication story.

---

## 8. Workstream D — repair and verify packaging/container delivery (P0)

### Problem

The package installs `mcp-filesystem-server`, but the Dockerfile uses `ENTRYPOINT ["mcp-fs"]`. The image therefore cannot start as written. Package metadata also still describes a Phase 1-only server.

### Implementation

1. Change the Docker entrypoint to the installed console script, or intentionally add an `mcp-fs` alias and test both. Prefer one canonical public command.
2. Update package description, classifiers, license metadata, project URLs, and versioning policy.
3. Build both wheel and sdist from a clean checkout.
4. Install each artifact into a fresh environment and smoke-test:
   - importing `mcp_fs`;
   - `python -m mcp_fs` over stdio;
   - the console script over stdio;
   - HTTP startup with safe configuration.
5. Build the container, start it as the non-root user, mount a temporary workspace, and perform a real MCP initialize/list-tools call.
6. Add a health/readiness strategy that does not bypass MCP authentication. If a separate health endpoint is introduced, it should reveal only process readiness.
7. Pin base-image tags to an intentional patch or digest policy and document rebuild cadence.
8. Confirm the runtime image contains `git` and `rg` and does not contain build-only tooling.

### Required tests

- Container process remains running with valid configuration.
- It runs as UID 1000/non-root and can operate only in the mounted workspace.
- Read-only is the container default.
- An authenticated HTTP MCP request succeeds when non-loopback binding is enabled.
- Invalid configuration exits non-zero with a clear stderr message.

### Acceptance criteria

- Fresh wheel, sdist, and container artifacts all start using documented commands.
- The image smoke test runs in CI rather than depending on manual verification.

---

## 9. Workstream E — make patch application correct and recoverable (P1)

### Problem

The in-process patch implementation uses hunk line numbers but does not verify that context and removal lines match the current file. It has incomplete handling for `/dev/null`, deletion, rename, mode changes, and no-newline markers. Multi-file patches are written one file at a time, so a later error can leave a partial patch applied.

### Design decision

Choose and document one of these approaches before implementation:

1. **Strict internal subset:** support text modifications, text creation, and text deletion only; reject every unsupported construct with a typed error. This is portable and easiest to secure.
2. **Git-backed application:** for verified Git repositories, use `git apply --check` followed by `git apply`, with all paths and repository boundaries validated first. Retain the internal subset for non-Git directories if that use case is required.

The recommended near-term approach is a strict internal subset plus a Git-backed path for SWE-bench repositories. Do not silently approximate unsupported patches.

### Implementation

1. Parse the full patch into an immutable plan without performing I/O.
2. Correctly recognize:
   - existing-file changes;
   - `--- /dev/null` new files;
   - `+++ /dev/null` deletions;
   - `\ No newline at end of file`;
   - multiple hunks per file.
3. Explicitly reject or support renames, copies, binary patches, submodules, and file-mode changes.
4. Validate every context and removed line against the current file. Reject stale or offset-mismatched hunks.
5. Detect duplicate targets and conflicting file operations within one patch.
6. Run Python syntax validation on every resulting Python file before committing any file.
7. Stage all new contents in sibling temporary files first.
8. Commit only after every target, hunk, syntax check, and temporary write succeeds. Maintain backups/rollback information so a commit failure does not silently leave a mixed state.
9. Return an accurate result distinguishing `validated`, `applied`, `rejected`, and `rolled_back` outcomes.

### Required tests

- New, modified, and deleted text files.
- Multiple files and multiple hunks.
- Stale context and incorrect removal content.
- Invalid Python in the second file leaves the first file unchanged.
- Failure during the commit stage triggers rollback and cleanup.
- No-newline markers.
- Unsupported binary/rename/mode patches return explicit errors.
- Reapplying an already-applied patch fails safely.

### Acceptance criteria

- The result never reports `patch_applied` after a partial or rejected operation.
- Every supported patch is checked against current content before mutation.
- The documented supported subset matches the tests.

---

## 10. Workstream F — bound file reading and response production (P1)

### Problem

`read_file_slice` limits returned lines but calls `readlines()`, loading the entire file into memory. The size gate is intentionally bypassed for a targeted slice, which makes a small request against a huge file a potential memory-exhaustion event. Extremely long individual lines can also defeat line-count-based output limits.

### Implementation

1. Replace `readlines()` with a streaming iteration.
2. Collect only the requested line window. If `total_lines` remains part of the response, count the remainder without retaining it in memory; alternatively make total-line counting optional and document the compatibility change.
3. Add a maximum returned-content byte limit in addition to the line limit.
4. Add a per-line byte/character cap with a `line_truncated` indicator.
5. Validate `offset >= 1`, `limit >= 1`, and configured limits at startup.
6. Decide how binary detection and decoding errors should work past the initial 8 KB probe. Prefer explicit metadata over silently replacing arbitrary binary data.
7. Guard against files changing between `stat`, probe, and read. Responses should remain safe even if metadata becomes stale.
8. Consider an optional `include_total_lines` flag when exact counting would require scanning a very large file.

### Required tests

- A multi-gigabyte sparse or generated file can yield a small slice under a constrained memory test.
- A single extremely long line is truncated by bytes, not only by line count.
- Invalid offset/limit values return structured validation errors.
- Offset past EOF remains well-defined.
- File mutation during reading does not crash the server or produce unbounded output.
- UTF-8 boundaries are handled correctly when byte-capping output.

### Acceptance criteria

- Memory use is proportional to the configured response cap, not source-file size.
- The response clearly identifies truncation by lines, bytes, or individual-line length.

---

## 11. Workstream G — harden test execution (P1)

### Problem

`run_test` captures unlimited stdout and stderr in memory. On timeout, `subprocess.run` terminates the direct process but may leave descendant processes running. Read-write mode also currently doubles as permission to execute arbitrary programs, which deserves an explicit policy—especially over HTTP.

### Implementation

1. Separate execution permission from filesystem write permission, for example with `MCP_FS_ALLOW_EXEC=false` by default for HTTP/container deployments.
2. Stream stdout and stderr into bounded buffers. Preserve the beginning, failure-relevant sections when feasible, and a capped tail.
3. Start a process group/session and terminate the full process tree on timeout:
   - POSIX: new session/process group, `SIGTERM`, grace period, then `SIGKILL`;
   - Windows: supported process-group/job-object strategy.
4. Cap command length, argument count, output bytes, and timeout through validated settings.
5. Use an explicit environment policy. At minimum disable interactive prompts and document inherited variables; preferably allow a controlled environment overlay.
6. Keep `shell=False` and test quoting behavior across platforms.
7. Return distinct statuses for test failure, timeout, output truncation, executable-not-found, and termination failure.
8. Ensure telemetry does not log secrets embedded in command arguments.

### Required tests

- A child process spawned by the test command is gone after timeout.
- Unlimited-output commands are capped without exhausting memory.
- Truncation metadata is present and accurate.
- Execution disabled mode rejects `run_test` even when file writes are allowed.
- Windows and POSIX argument parsing cases pass in CI.
- Sensitive command arguments are redacted or omitted from logs.

### Acceptance criteria

- Timeout means the process tree is actually terminated.
- Captured output cannot exceed configured memory bounds.
- Remote execution requires an explicit, authenticated opt-in.

---

## 12. Workstream H — fix search, discovery, and input validation (P2)

### Search context

The ripgrep command requests context lines, but the JSON parser currently ignores ripgrep `context` events. The Python fallback also ignores `context_lines`.

Implement one shared response model for both backends:

- each hit includes the matched line and bounded before/after context;
- overlapping contexts are de-duplicated;
- match count counts matches, not context lines;
- the global cap is enforced across files;
- context text is included in output-byte budgeting.

### Search/discovery boundary safety

- Skip or revalidate file symlinks in the Python search fallback so an in-root link cannot read an outside target.
- Use `lstat` rather than target-following `stat` where discovery should report symlink metadata.
- Keep directory traversal non-following unless a link has been canonicalized and remains inside the root.
- Handle invalid regular expressions as typed client errors.

### General validation

Create shared validation for:

- positive offsets, limits, result caps, depths, context counts, ports, timeouts, and byte caps;
- non-empty search patterns where required;
- sensible upper bounds independent of caller input;
- finite numeric values (`NaN` and infinities must be rejected);
- settings relationships, such as default limits not exceeding hard limits.

Avoid silently turning negative values into surprising slices or caps. Return a stable `invalid_argument` error with the field and permitted range.

### Required tests

- Equivalent context output from ripgrep and Python backends.
- Context merging near adjacent matches.
- Symlinked files pointing outside are not searched or stat-followed.
- Invalid regex and all invalid numeric bounds return structured errors.
- `max_results=1` cannot return multiple hits because of per-file behavior.

---

## 13. Workstream I — strengthen configuration and telemetry (P2)

### Configuration

1. Move numeric range validation into Pydantic fields or explicit validators.
2. Validate and canonicalize roots once at startup, preserving the mapping from a requested path to its owning root.
3. Make settings immutable after construction if supported cleanly by the Pydantic version.
4. Reject duplicate or overlapping roots when they create ambiguous ownership, or define deterministic longest-root matching.
5. Remove unused module-level configuration such as `_TRANSPORT` if it has no purpose.
6. Add a sanitized configuration summary for diagnostics.

### Telemetry

1. Define which fields may contain source code, paths, commands, secrets, or user data.
2. Never log file content, patches, bearer tokens, or raw subprocess output by default.
3. Redact authorization headers and configurable sensitive command arguments.
4. Add consistent fields: tool, outcome, error code, duration, bytes returned, truncation, and request correlation ID when available.
5. Ensure all stdio diagnostics remain on stderr.
6. Add tests that capture logs and assert sensitive values are absent.

---

## 14. Workstream J — release-quality tests and CI (P1)

### Current gap

The existing workflow runs pytest across three operating systems and three Python versions, which is valuable, but it does not verify formatting, types, coverage, built artifacts, the Docker image, or security tooling. Coverage is not currently configured, so the plan’s coverage claims are unproven.

### Test organization

Add explicit suites/directories for:

- `tests/security/`: traversal, symlinks, patch paths, Git boundary, HTTP auth, secret leakage;
- `tests/contract/`: tool names, schemas, response compatibility, error codes;
- `tests/packaging/`: wheel/sdist and console-script smoke tests;
- `tests/container/`: image startup and MCP handshake;
- existing unit and integration tests.

### CI jobs

1. **Static quality:** formatting check, linting, and type checking.
2. **Unit/integration matrix:** existing OS/Python matrix, with dependency definitions sourced from a development extra rather than duplicated ad hoc in workflow YAML.
3. **Security suite:** adversarial tests with branch coverage focused on canonical path, patch, Git, HTTP auth, and execution gates.
4. **Coverage:** publish a report and enforce an agreed threshold. Use a high branch-coverage threshold for security modules rather than claiming an undefined “100% coverage.”
5. **Build:** build wheel and sdist, inspect metadata, install each into clean environments, and run smoke tests.
6. **Container:** build image, run it non-root, perform authenticated MCP handshake, and stop it cleanly.
7. **Dependency/security checks:** dependency audit and a lightweight static security scan, with findings triaged rather than blindly ignored.
8. **Demo:** run the SWE-bench demonstration and fail on inaccurate assertions.

### Development dependencies

Define a complete `dev`/`test` extra containing the tools and transport test dependencies actually required by CI, such as pytest, async/test HTTP dependencies, coverage tooling, build tooling, linter, and type checker. Keep runtime dependencies minimal, but do not rely accidentally on transitive packages without declaring direct usage.

### Acceptance criteria

- A pull request cannot merge when security regression, artifact installation, or container startup fails.
- Coverage numbers are reproducible locally and in CI.
- CI configuration and `pyproject.toml` do not maintain conflicting dependency lists.

---

## 15. Workstream K — repair examples, docs, and product claims (P2)

### Example fixes

Update `examples/swebench_harness_demo.py` to:

- read class methods from the actual `methods` field rather than `children`;
- assert `run_test["status"] == "passed"` and use `counts["passed"]` rather than a nonexistent top-level `passed` field;
- fail if expected methods are missing;
- fail if the exported patch is empty or includes unexpected files;
- remove “100% invariants met” language unless every asserted invariant is actually checked;
- use the supported public MCP invocation path where possible instead of private SDK internals such as `_tool_manager`.

### Documentation alignment

1. Replace the stale top-level plan status with a truthful hardening status.
2. Mark phases 1–6 as feature implementation complete, while keeping production hardening open.
3. Update the root README and package README from the Phase 1/one-tool description to the current 14-tool surface.
4. Reconcile all bearer-token versus zero-auth statements.
5. Document the exact patch subset, Git-root rule, symlink policy, execution permission, and resource limits.
6. Update `.env.example` with all current settings and safe defaults.
7. Correct the package description and remove comments claiming future work that already exists.
8. Clearly label `terminal_server.py` as an intentionally unsafe course example and `production-filesystem` as the hardened product.
9. Replace unsupported token-reduction percentages with benchmark methodology or qualify them as illustrative estimates.
10. Add a changelog/release checklist and a migration section for any new authentication or execution settings.

### Acceptance criteria

- Every documented command is exercised by CI or a checked smoke-test script.
- No page claims bearer auth, complete patch compatibility, coverage, or production readiness unless the relevant acceptance test exists.

---

## 16. Recommended delivery sequence

Keep changes reviewable and avoid mixing security fixes with broad cleanup.

### Pull request 1 — boundary containment

- Workstream A: patch path resolution.
- Workstream B: Git repository/pathspec containment.
- Regression tests under `tests/security`.
- Minimal documentation warning that remote/patch use is not yet production-ready.

**Gate:** No known filesystem or Git operation crosses an allowed root.

### Pull request 2 — remote and container safety

- Workstream C: HTTP auth and fail-closed configuration.
- Workstream D: console-script/Docker repair.
- Container defaults switched to read-only.
- Authenticated HTTP integration and container smoke tests.

**Gate:** A fresh image starts securely and cannot expose unauthenticated execution.

### Pull request 3 — mutation correctness

- Workstream E: strict patch semantics, preflight, and rollback.
- New/delete/multi-file/stale-context coverage.

**Gate:** Supported patches are content-validated and do not partially apply on validation failure.

### Pull request 4 — resource containment

- Workstream F: streaming file reads and byte caps.
- Workstream G: bounded subprocess capture and process-tree termination.
- Relevant configuration fields and telemetry.

**Gate:** File size and process output cannot cause unbounded server memory growth.

### Pull request 5 — search and validation correctness

- Workstream H and remaining Workstream I items.
- Cross-backend search contract tests.

**Gate:** Both search backends return the same bounded response model and all public numeric inputs are validated.

### Pull request 6 — release pipeline and truthfulness

- Workstream J: expanded CI and artifact verification.
- Workstream K: examples, plan, READMEs, environment reference, and metadata.
- Version bump and release candidate notes.

**Gate:** CI proves the claims made by the release documentation.

---

## 17. Verification matrix

| Capability | Unit | Integration | Security/adversarial | Packaging/container |
|---|---:|---:|---:|---:|
| Canonical paths and symlinks | Yes | Yes | Required | — |
| Patch parsing/application | Yes | Yes | Required | — |
| Git status/diff/export/revert | Yes | Yes | Required | Git available in image |
| HTTP authentication | Yes | Yes | Required | Authenticated handshake |
| File slicing/byte limits | Yes | Yes | Large-file memory test | — |
| Test runner/process cleanup | Yes | Yes | Output/child-process stress | OS matrix |
| Search backends | Yes | Yes | Symlink/regex/input cases | `rg` present and absent |
| stdio transport | — | Yes | Protocol stdout discipline | Wheel/sdist smoke |
| Docker runtime | — | MCP handshake | Non-root/read-only/auth | Required |
| Documentation examples | — | Executable examples | — | Commands smoke-tested |

---

## 18. Final definition of done

The hardening phase is complete only when all of the following are true:

- [x] Every direct and derived filesystem path is checked against its owning allowed root immediately before I/O.
- [x] Malicious patch paths and Git roots/pathspecs cannot cross workspace boundaries.
- [x] Patch application validates content and has documented, tested behavior for creation, modification, deletion, unsupported constructs, and multi-file failure.
- [x] Non-loopback HTTP requires authentication unless an explicit unsafe development override is enabled.
- [x] Remote command execution requires a separate, authenticated opt-in.
- [ ] The default container is non-root, read-only, starts successfully, and passes a real MCP handshake.
- [x] File reads, search output, diffs, and subprocess output are bounded in both response size and memory production.
- [x] Test timeouts terminate descendant processes on supported platforms.
- [x] Security regression tests cover traversal, symlinks, patch paths, Git boundaries, auth, and secret handling.
- [ ] CI runs static checks, the supported OS/Python test matrix, coverage, artifact installation, and container smoke tests.
- [x] The demo asserts the real response schema and fails on regressions.
- [x] READMEs, plan status, environment examples, package metadata, and architecture docs match the shipped implementation.
- [ ] A release candidate is installed and exercised from its built wheel/sdist and container, not only from an editable checkout.
- [ ] The worktree is clean and release artifacts are reproducible from the tagged commit.

The unchecked items are release-verification gates, not missing implementation.
Do not describe the release candidate as container-verified until the remote CI
handshakes pass, and do not declare the Rust cutover complete until the separate
transition gates in `python-to-rust.md` are satisfied.
