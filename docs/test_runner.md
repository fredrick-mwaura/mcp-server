# Test Execution & Failure Distillation (Phase 5)

## 1. The Problem: The Log Dump Trap

In autonomous software engineering and benchmark evaluation loops (like SWE-bench), **running tests is the single most token-expensive operation**:

* **Verbosity**: A medium test suite runs 200–500 tests. When all pass or when 1 test fails, `pytest` or `unittest` outputs:
  * 30+ lines of platform information, plugins, and root directories.
  * Hundreds of lines of passing dots or test names.
  * Repetitive deprecation and third-party warnings.
  * Raw tracebacks containing internal framework internals (`site-packages/_pytest/...`).
* **The Cost**: A single test run frequently produces 2,000 to 5,000 tokens of terminal output. If an autonomous agent runs tests after each edit across 10 turns, **it wastes 30,000 to 50,000 tokens just reading test chatter**, rapidly consuming budget and exhausting the context window.

---

## 2. The Smart Distillation Algorithm (`distill_test_output`)

Instead of returning raw process stdout/stderr to the model, `run_test` applies an intelligent distillation filter:

```
[Raw Test Process (pytest / unittest / shell)]
                     │
                     ▼ (stdout + stderr)
┌─────────────────────────────────────────────────────────────┐
│ 1. Header & Chatter Stripping                               │
│    - Drops platform info, session configuration, & dots     │
├─────────────────────────────────────────────────────────────┤
│ 2. Metrics & Status Parsing                                 │
│    - Extracts: passed, failed, skipped, errors counts       │
│    - Extracts: execution duration in seconds                │
├─────────────────────────────────────────────────────────────┤
│ 3. Success Fast-Path (0 Tokens Wasted)                      │
│    - If exit_code == 0 and 0 failures:                      │
│      Returns compact 2-line confirmation                    │
├─────────────────────────────────────────────────────────────┤
│ 4. Failure Isolation (Surgical Diagnosis)                   │
│    - Extracts ONLY the === FAILURES === block               │
│    - Isolates exact failing test name & assertion error     │
│    - Strips internal test runner frames                     │
│    - Caps output at max_failure_lines (default: 120)        │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
[Compact, Token-Distilled JSON Payload (~150 tokens)]
```

---

## 3. Output Comparison

### 3.1 When Tests Pass

#### Raw Pytest Output (~1,200 tokens):
```
============================= test session starts ==============================
platform darwin -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/frd/Projects/mcp-server/production-filesystem
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2
collected 120 items

tests/integration/test_git_tools.py ..........                           [  8%]
tests/integration/test_server.py ........................                [ 28%]
tests/integration/test_write_tools.py ...................                [ 44%]
tests/unit/test_canonical_path.py ..........                             [ 52%]
tests/unit/test_execution.py ..........                                  [ 60%]
tests/unit/test_git_ops.py ........                                      [ 67%]
tests/unit/test_read_file.py ..........                                  [ 75%]
tests/unit/test_symbol_outline.py .........                              [ 83%]
tests/unit/test_syntax_guard.py .......                                  [ 89%]
tests/unit/test_write_ops.py ..........                                  [100%]

============================= 120 passed in 4.81s ==============================
```

#### Distilled Output (~30 tokens — 97.5% reduction):
```json
{
  "status": "passed",
  "exit_code": 0,
  "duration_seconds": 4.81,
  "summary": "120 passed in 4.81s",
  "counts": {
    "passed": 120,
    "failed": 0,
    "skipped": 0,
    "errors": 0
  },
  "failures": [],
  "distilled_output": null
}
```

---

### 3.2 When Tests Fail

#### Distilled Output (~150 tokens):
```json
{
  "status": "failed",
  "exit_code": 1,
  "duration_seconds": 0.42,
  "summary": "1 failed, 119 passed in 0.42s",
  "counts": {
    "passed": 119,
    "failed": 1,
    "skipped": 0,
    "errors": 0
  },
  "distilled_output": "__________________________________ test_solve __________________________________\n    def test_solve():\n>       assert solve(10) == 42\nE       AssertionError: assert 100 == 42\n\ntests/test_solver.py:14: AssertionError"
}
```

The agent receives the exact test name, failing line, and assertion failure immediately without any noise.

---

## 4. Operational Invariants

1. **Strict Timeout Bounds**:
   * Default timeout is 90 seconds (configurable up to 300 seconds).
   * If a test hangs (e.g. infinite loop, deadlock, or waiting for network socket), the subprocess is cleanly killed and a structured `"status": "timeout"` payload is returned.
2. **Directory & Workspace Scoping**:
   * Tests only execute within verified directories approved by `resolve_allowed_path()`.
3. **Read-Write Mode Gate**:
   * Running test commands executes arbitrary host processes; therefore `run_test` is strictly blocked when the server runs in `MCP_FS_MODE=read-only`.
