# MCP Client Configurations

This directory contains ready-to-use configuration files for popular MCP-compatible developer tools and agent harnesses.

---

## 1. Claude Desktop (`claude_desktop_config.json`)

Location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

### Stdio Transport (Default)
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "/Users/frd/Projects/mcp-server/.venv/bin/mcp-filesystem-server",
      "env": {
        "MCP_FS_ROOTS": "/Users/frd/Projects/my-project",
        "MCP_FS_MODE": "read-write",
        "MCP_FS_TRANSPORT": "stdio"
      }
    }
  }
}
```

### Streamable HTTP Transport (Phase 6)
If running the server in HTTP mode (`MCP_FS_TRANSPORT=streamable-http`):
```json
{
  "mcpServers": {
    "filesystem-http": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

---

## 2. Cursor IDE (`.cursor/mcp.json`)

Location in your workspace root: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "production-filesystem": {
      "command": "/Users/frd/Projects/mcp-server/.venv/bin/mcp-filesystem-server",
      "env": {
        "MCP_FS_ROOTS": "${workspaceFolder}",
        "MCP_FS_MODE": "read-write"
      }
    }
  }
}
```

---

## 3. Opencode (`opencode.json`)

Location in your workspace root: `opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "filesystem": {
      "type": "local",
      "command": [
        "/Users/frd/Projects/mcp-server/.venv/bin/mcp-filesystem-server"
      ],
      "environment": {
        "MCP_FS_ROOTS": "/Users/frd/Projects/mcp-server",
        "MCP_FS_MODE": "read-write",
        "MCP_FS_TRANSPORT": "stdio"
      }
    }
  }
}
```
