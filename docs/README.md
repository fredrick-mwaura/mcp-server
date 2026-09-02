# Architecture & Engineering Documentation

Welcome to the technical architecture and algorithm documentation for the **Production MCP File System & SWE-Bench Super-Server**.

This server is engineered specifically for daily high-productivity software engineering and autonomous benchmark harnesses (such as [SWE-bench](https://www.swebench.com/)). It replaces naive, demo-grade MCP file servers with a hardened, token-frugal, and crash-resilient engine.

---

## Documentation Index

| Document | Focus & Highlights |
|---|---|
| **[1. System Architecture](architecture.md)** | Core component hierarchy, closure-based dependency injection, single chokepoint security model, and transport isolation. |
| **[2. Token Economics & Cost Optimization](token_economics.md)** | In-depth breakdown of prompt and completion token savings (~90–98%), AST outline navigation, surgical editing, and failure prevention. |
| **[3. Security Invariants & VFS Algorithms](security_and_vfs.md)** | Canonical path resolution algorithm, adversarial threat defense, atomic write swap (`os.replace`), and why scoped `<root>/.trash/` is used instead of `send2trash`. |
| **[4. Git & SWE-Bench Lifecycle](git_and_swebench.md)** | SWE-bench submission patch export pipeline (`git diff HEAD`), porcelain status parsing, diff line-capping, and rollback guarantees. |
| **[5. Test Execution & Failure Distillation](test_runner.md)** | Bounded test execution (`run_test`), failure trace extraction, and ~90% token reduction on test results. |

---

## Architectural Principles at a Glance

```
                         ┌──────────────────────────────────────────────┐
                         │              MCP CLIENT / AGENT              │
                         │    (Claude Desktop / Cursor / SWE-Bench)     │
                         └──────────────────────┬───────────────────────┘
                                                │ JSON-RPC (stdio / HTTP)
┌───────────────────────────────────────────────▼──────────────────────────────────────────────┐
│                                       TRANSPORT LAYER                                        │
│               stdio: default local             │    http: streamable-http + bearer           │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│                                        CORE ENGINE                                           │
│   Settings validation (Pydantic), Mode gates (read-only vs read-write), Telemetry (stderr)   │
├───────────────────┬───────────────────┬──────────────────────┬───────────────────────────────┤
│   1. NAVIGATION   │    2. SURGICAL    │     3. SWE-BENCH     │        4. SECURITY &         │
│     & SEARCH      │      EDITING      │      & GIT CORE      │          LIMITS               │
├───────────────────┼───────────────────┼──────────────────────┼───────────────────────────────┤
│ • list_directory  │ • edit_block      │ • git_status         │ • canonical_path (single gate)│
│ • read_file (L42:)│ • apply_patch     │ • git_diff           │ • AST pre-commit syntax gate  │
│ • grep_search     │ • write_file      │ • export_swebench    │ • payload & line caps         │
│ • symbol_outline  │ • delete_entry    │ • revert_file        │ • atomic file swap (vfs.py)   │
│ • find_files      │   (atomic swap)   │                      │ • scoped .trash/ buffer       │
└───────────────────┴───────────────────┴──────────────────────┴───────────────────────────────┘
                                                │
                                     ┌──────────▼──────────┐
                                     │      WORKSPACE      │
                                     │    (Allowed Root)   │
                                     └─────────────────────┘
```

1. **Zero-Trust File Boundaries**: No code ever performs disk I/O without resolving canonical paths through `resolve_allowed_path()`.
2. **Context Budget Conservation**: Never dump entire files; return bounded, numbered slices and AST outlines.
3. **Pre-Commit Syntax Gates**: Python changes are validated in memory with `ast.parse()` before disk modification.
4. **Crash-Safe Mutations**: All writes use sibling temp files and `os.replace` atomic swaps.
5. **Portability Everywhere**: Zero desktop platform dependencies; runs natively in headless Docker containers, CI runners, macOS, Linux, and Windows.
