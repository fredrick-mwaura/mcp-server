"""Allow running the server with `python -m mcp_fs`.

The console-script entry point (`mcp-filesystem-server`, declared in
pyproject.toml) points at the same `main()` function in server.py.
"""

import sys

from mcp_fs.server import main

if __name__ == "__main__":
    sys.exit(main())
