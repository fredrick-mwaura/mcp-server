# Production MCP Server — SWE & SWE-Bench Force Multiplier

**Status:** Active Development (Phase 1 Complete, Moving to Phase 2)  
**Language:** Python 3.10+ (Tested on 3.12, MCP SDK v2)  
**Transports:** `stdio` (local power-user & agent harness) **and** `streamable-http` (remote sandboxes)  
**Mission:** Eliminate manual friction in daily software engineering and SWE-bench autonomous workflows while enforcing production-grade security, zero crashes, and the lowest possible LLM API token costs.

---

## 1. Why this project?

Standard file-system MCP servers are built as naive demos: they dump entire 4,000-line files into the prompt, freeze during directory scans, fail edits because of subtle indentation mismatches, lack git visibility, and have no test loop.

In real-world software engineering and **SWE-bench benchmarking**, this naive design causes:
1. **Context Bloat & Crushing API Bills**: Dumping whole files exhausts the token budget and multiplies input token costs on every single conversation turn.
2. **Brittle Code Modifications**: Re-generating entire files leads to syntax errors, hallucinated truncations, and lost imports.
3. **Missing Feedback Loops**: The model cannot autonomously run tests, inspect diffs, or export clean patch artifacts (`.patch`) for evaluation harnesses.

This server is architected as an **industrial-grade SWE development engine**:

| Production & SWE Concern | The Failure It Prevents | Token / Cost Advantage |
|---|---|---|
| **Path scoping & canonical gate** | Agent reads `/etc/shadow` or escapes repo root | Rejects out-of-bounds paths before disk I/O |
| **Line-sliced reads & caps** | `read_file` dumping 50,000 tokens into context | **90% token reduction**: read only lines 40–120 with numbers |
| **AST symbol outlines** | Model reading 5 full files just to find where a class lives | **95% token reduction**: 20 lines of function signatures |
| **Targeted grep search** | Dumping thousands of lines from file walks | Only returns matching lines + 1-2 lines of bounding context |
| **Surgical block editing** | Model rewriting 500 lines to change 3 lines | Completion tokens drop from 1,000+ tokens to ~50 tokens |
| **Pre-commit AST syntax check** | Indentation or syntax errors breaking the build | Rejects invalid code in 0 turns; prevents 5+ wasted retry turns |
| **Atomic writes (`os.replace`)** | Mid-stream crash or write failure corrupts file | Zero partial writes or broken files |
| **Native Git & SWE-bench tools** | Manual `git diff`, patch extraction, or broken resets | One-call `export_swebench_patch` and `revert_file` |
| **Smart test error extraction** | Pytest dumping 3,000 lines of passing logs | Filters directly to failing assertion and stack frame |

---

## 2. Product Requirements & Tool Surface

### 2.1 Target Personas
1. **The Daily Software Engineer**: Connecting via Claude Desktop, Cursor, or OpenCode over `stdio` to explore codebases, refactor methods, run unit tests, and review git diffs with zero manual copy-pasting.
2. **The SWE-Bench Autonomous Agent**: Headless agent running inside an evaluation harness that needs to locate the bug, reproduce the test failure, apply surgical diffs, verify fix, and export a clean submission patch.
3. **The Remote Team / Cloud Sandbox**: Containerized deployment over `streamable-http` with scoped bearer tokens for isolated evaluation environments.

### 2.2 Functional Surface

#### A. Navigation & Discovery (Token-Conscious)
| Tool | Mode | Description | Token Optimization |
|---|---|---|---|
| `list_directory(path, depth=1)` | R | Paginated, non-recursive by default. | Avoids dumping giant recursive trees. |
| `read_file(path, offset=1, limit=150, line_numbers=True)` | R | Reads a window of lines with line number prefix (e.g., `L42: ...`). | Caps default read to 150 lines; prevents context explosion. |
| `grep_search(query, regex=False, include_globs=[], max_results=30, context_lines=1)` | R | Ripgrep-grade search returning matches with tight context. | Emits short snippets, not entire files. |
| `symbol_outline(path)` | R | Extracts top-level classes, methods, and functions with line spans via `ast`. | Lets model see structural layout in ~25 lines. |
| `find_files(pattern, path=".")` | R | Fast glob matching for file paths by name/extension. | Returns clean relative path lists. |

#### B. Surgical Code Editing (Zero-Hallucination)
| Tool | Mode | Description | Safety & Cost Impact |
|---|---|---|---|
| `edit_block(path, target_content, replacement_content, dry_run=False)` | W | Replaces a unique target chunk with replacement. | Model only outputs the diff chunk; saves completion tokens. |
| `apply_patch(patch_str)` | W | Applies a standard unified diff patch (git-compatible). | Standard SWE-bench editing mechanism. |
| `write_file(path, content)` | W | Creates or overwrites a file using atomic swap (`.<name>.tmp` $\to$ `os.replace`). | Crash-safe; cannot corrupt existing files. |
| `delete_entry(path, permanent=False)` | W | Moves to `.trash` by default; permanent delete is explicit. | Prevents irreversible prompt injection accidents. |

#### C. Pre-Commit AST Syntax Gate
Before any edit or write to a Python file is committed to disk, it passes through `validate_syntax(code, path)`:
- If a syntax or indentation error is detected, the write is aborted.
- Returns a structured error payload detailing the exact line and error message.
- **Why this is critical:** Prevents the agent from entering a costly 5-turn self-repair loop trying to guess why the file broke.

#### D. SWE-Bench & Git Integration
| Tool | Mode | Description |
|---|---|---|
| `git_status()` | R | Returns staged, unstaged, and untracked file summary. |
| `git_diff(cached=False, paths=[])` | R | Unified diff against `HEAD` or index, scoped to changed files. |
| `export_swebench_patch()` | R | Exports the exact `git diff HEAD` string required for SWE-bench submission. |
| `revert_file(path)` | W | Reverts uncommitted changes to a specific file (instant rollback). |

#### E. Bounded Verification & Test Loop
| Tool | Mode | Description |
|---|---|---|
| `run_test(command, timeout=90)` | W/Exec | Runs test command (e.g. `pytest path/to/test.py::test_case -k ...`). |
| **Smart Output Distillation** | N/A | Extracts failed assertions and stack traces; strips verbose passing logs to protect token budget. |

---

## 3. Architecture & Package Layout

```
                        ┌──────────────────────────────────────────────┐
                        │             MCP CLIENT / AGENT               │
                        │    (Claude Desktop / SWE-Bench / OpenCode)   │
                        └──────────────────────┬───────────────────────┘
                                               │ JSON-RPC (stdio / HTTP)
┌──────────────────────────────────────────────▼───────────────────────────────────────────────┐
│                                       TRANSPORT LAYER                                        │
│               stdio: default local             │    http: streamable-http + bearer           │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│                                        CORE ENGINE                                           │
│   Server assembly, Pydantic validation, mode gates (read-only vs read-write), telemetry      │
├───────────────────┬───────────────────┬──────────────────────┬───────────────────────────────┤
│   1. NAVIGATION   │    2. SURGICAL    │     3. SWE-BENCH     │        4. SECURITY &         │
│     & SEARCH      │      EDITING      │      & GIT CORE      │          LIMITS               │
├───────────────────┼───────────────────┼──────────────────────┼───────────────────────────────┤
│ • list_directory  │ • edit_block      │ • git_status         │ • canonical_path (single gate)│
│ • read_file       │ • apply_patch     │ • git_diff           │ • AST pre-commit syntax check │
│ • grep_search     │ • write_file      │ • export_patch       │ • payload caps & slice limits │
│ • symbol_outline  │ • delete_entry    │ • revert_file        │ • atomic file swap (vfs.py)   │
│ • find_files      │   (atomic swap)   │ • run_test (distill) │ • safe trash                  │
└───────────────────┴───────────────────┴──────────────────────┴───────────────────────────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │      WORKSPACE      │
                                    │    (Allowed Root)   │
                                    └─────────────────────┘
```

### 3.1 Proposed Package Layout

```
production-filesystem/
├── pyproject.toml
├── README.md
├── src/
│   └── mcp_fs/
│       ├── __init__.py
│       ├── __main__.py
│       ├── server.py              # MCPServer assembly & tool registration
│       ├── config.py              # Settings: roots, mode, limits, transport
│       ├── errors.py              # Structured, typed error hierarchy
│       ├── vfs.py                 # ONLY layer performing raw OS I/O
│       ├── telemetry.py           # JSON logging & duration metrics
│       ├── limits.py              # Token-budget caps (max lines, max bytes)
│       ├── security/
│       │   ├── __init__.py
│       │   ├── canonical_path.py  # Zone check, realpath, traversal defense
│       │   ├── syntax_guard.py    # AST pre-commit validation for edits
│       │   └── trash.py           # Safe deletion (.trash directory)
│       └── tools/
│           ├── __init__.py
│           ├── navigation.py      # list_directory, read_file (sliced), find_files
│           ├── search.py          # grep_search (ripgrep-fast regex engine)
│           ├── outline.py         # symbol_outline (AST structural map)
│           ├── editing.py         # edit_block, apply_patch, write_file
│           ├── git_ops.py         # git_status, git_diff, export_patch, revert
│           └── execution.py       # run_test with smart failure distillation
└── tests/
    ├── unit/                      # canonical_path, syntax_guard, limits, editing
    ├── integration/               # vfs operations, git tools, read slices
    └── security/                  # traversal, symlink escapes, syntax injection
```

---

## 4. Security & Guardrails

1. **Single Chokepoint (`canonical_path.py`)**:
   Every path requested by a tool runs through:
   `expanduser` $\to$ `abspath` $\to$ `normpath` $\to$ `os.path.realpath` $\to$ boundary check against configured `roots`.
   If a path tries to escape via `..`, symlinks, or sibling prefixes (`/data2` vs `/data`), it is rejected with a typed `WorkspaceNotAllowed` error before any OS handle is opened.
2. **Atomic Writes**:
   Writes always stream to a sibling temporary file (`.<filename>.mcpfs-tmp-<uuid>`), sync to disk, and replace atomically via `os.replace`.
3. **AST Syntax Validation**:
   Any Python write/edit must successfully parse via `ast.parse()` before replacing the destination file. If it fails, the disk remains untouched and the syntax error details are returned to the agent.
4. **Least-Privilege Trash**:
   Deletions move files into `.trash/` inside the allowed root, allowing immediate recovery if an agent deletes the wrong file.

---

## 5. Token & API Cost Optimization Matrix

To make this server the **cheapest to operate** across thousands of benchmark runs or daily programming turns:

| Operation | Standard MCP Waste | Our Optimized Strategy | Token Cost Reduction |
|---|---|---|---|
| **Reading Code** | Returns entire 2,000-line file (~8,000 tokens) | Line-numbered slice (`offset=10, limit=100`) (~400 tokens) | **~95% cheaper** |
| **Locating Definitions** | Model reads multiple entire modules to find a class | `symbol_outline` returns outline of classes & functions (~60 tokens) | **~98% cheaper** |
| **Grep / Search** | Returns full file dumps or unconstrained walks | Match lines + 1-line bounding context, capped at 30 hits | **~90% cheaper** |
| **Code Edits** | Model emits 500 lines of unchanged code to edit 5 lines | `edit_block` emits only `target` and `replacement` (~50 tokens) | **~90% cheaper completion** |
| **Failed Edits** | Broken indentation requires 3–5 roundtrips of debugging | `syntax_guard` immediately returns exact line of syntax error | **Eliminates 3–5 wasted turns** |
| **Test Output** | Dumps 2,000 lines of pytest output (passing tests, warnings) | Distills output to failure assertion and traceback summary (~150 tokens) | **~90% cheaper** |

---

## 6. Phased Roadmap

### Phase 1: Core Foundation & Path Security (COMPLETED ✅)
- [x] Package layout (`pyproject.toml`, console script).
- [x] Pydantic configuration & environment parsing.
- [x] Zero-trust `canonical_path` security gate (tested against traversals, symlinks, sibling prefixes).
- [x] Base `list_directory` tool.
- [x] JSON structured logging & 16 unit/integration tests passing.

### Phase 2: Token-Preserving Code Inspection & Search (COMPLETED ✅)
- [x] `read_file`: Line slicing (`offset`, `limit`), line-number prefixes (`L42:`), binary detection, and byte caps.
- [x] `symbol_outline`: AST-based extraction of functions, classes, arguments, and docstrings for Python files.
- [x] `find_files`: Name/glob filter within allowed roots.
- [x] `grep_search`: High-speed regex/string search with bounded context lines and match caps.
- [x] Unit & integration tests for all Phase 2 tools.

### Phase 3: Surgical Editing & Pre-Commit Syntax Gate (COMPLETED ✅)
- [x] `edit_block`: Precise string replacement with uniqueness validation and dry-run mode.
- [x] `syntax_guard`: AST validation on Python edits prior to disk commit.
- [x] `write_file`: Atomic temporary-file-and-swap mechanism.
- [x] `apply_patch`: Unified diff patch engine.
- [x] `delete_entry`: Safe deletion via `.trash/` buffer.

### Phase 4: SWE-Bench & Git Lifecycle (COMPLETED ✅)
- [x] `git_status`: Structured summary of modified, added, deleted, and untracked files.
- [x] `git_diff`: Clean unified diff generation against `HEAD` or index.
- [x] `export_swebench_patch`: Generate standardized `.patch` string for benchmark submission.
- [x] `revert_file`: Roll back changes to a specific file.

### Phase 5: Verification & Smart Test Runner (COMPLETED ✅)
- [x] `run_test`: Subprocess test runner with timeout and exit code handling.
- [x] Failure distillation: Parser extracting failed test names, assertion failures, and tracebacks while discarding passing chatter.

### Phase 6: Remote Transport, Packaging & CI/CD (COMPLETED ✅)
- [x] `streamable-http` transport: Frictionless zero-auth HTTP runner with host/port configuration.
- [x] Multi-stage non-root Dockerfile for evaluation sandboxes.
- [x] GitHub Actions CI matrix covering macOS, Linux, and Windows across Python 3.10–3.12.


---

## 7. Definition of Done for SWE & SWE-Bench Production

1. **Security**: Zero possibility of path traversal or symlink escape out of configured roots; 100% test coverage on adversarial security tests.
2. **Token Efficiency**: Never dumps uncontrolled raw data; all tools default to token-preserving slices, summaries, and match limits.
3. **Syntax Reliability**: Every Python code edit is AST-verified before touching disk; no corrupted files.
4. **SWE-Bench Readiness**: Able to clone a repo, inspect an issue via `grep_search`/`symbol_outline`, modify files via `edit_block`, verify via `run_test`, and export a clean patch via `export_swebench_patch`.
5. **Portability**: Verified on macOS, Linux, and Windows; installed via standard `pip install -e .`.
