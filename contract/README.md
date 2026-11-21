# MCP filesystem compatibility contract

This directory contains the language-neutral contract shared by the Python
reference server and the Rust candidate. Security corrections documented in
`docs/implementation/fix-gaps.md` override buggy legacy behavior.

`server-manifest.json` records the stable tool, configuration, permission, and
error-code surface. The Rust integration tests start the built binary over
stdio and HTTP, assert the exact 14-tool list, execute a real tool call, and
verify bearer authentication. Security fixtures cover patch traversal, Git
repository authority expansion, symlink escapes, bounded output, and test
process-group cleanup.

`differential_stdio.py` starts both real binaries, requires exact tool names
and input schemas, compares the five read-only tools over a shared fixture, and
checks an outside-root security decision. It normalizes only the documented
search-context correction. The fixture corpus will grow through the remaining
beta gates.
