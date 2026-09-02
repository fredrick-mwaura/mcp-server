"""mcp_fs - a production-grade MCP file system server.

This package is intentionally *modular*: unlike Lesson 1's single-file
`terminal_server.py`, each production concern lives in its own module so it can
be reasoned about and tested in isolation:

    config/     ->  what the server is allowed to do (env-driven settings)
    security/   ->  the single chokepoint that gates EVERY path (the product)
    vfs.py      ->  the ONLY module allowed to touch the real filesystem
    tools/      ->  thin MCP tool handlers (validate + delegate, never touch FS)
        navigation.py  ->  list_directory, read_file (line-sliced), find_files
        search.py      ->  grep_search (ripgrep-accelerated code search)
        outline.py     ->  symbol_outline (AST structural map)
    errors.py   ->  typed errors the AI model can actually understand
    telemetry.py->  JSON logging (stderr, never stdout - same rule as Lesson 1)
    server.py   ->  assembles an MCPServer from the pieces

The rule that keeps this safe: *no module except vfs.py imports os/fs calls,
and no path reaches vfs.py without first passing through security.*
"""

__version__ = "0.3.0"
