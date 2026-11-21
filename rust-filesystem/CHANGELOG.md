# Changelog

## 0.1.0 — migration candidate

- Added all 14 filesystem, editing, patch, Git, and test-execution MCP tools.
- Added stdio and authenticated streamable HTTP transports.
- Added fail-closed remote HTTP configuration and a separate execution gate.
- Added rooted path capabilities, bounded output, atomic writes, recoverable
  trash, strict patch validation, and Git repository containment.
- Added unit, integration, security, stdio contract, HTTP auth, and real Git
  tests.

This release is a side-by-side candidate. It is not yet the production default;
see the implementation checkpoint in `docs/implementation/python-to-rust.md`.
