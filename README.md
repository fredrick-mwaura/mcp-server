# 🖥️ Terminal Server - Your First MCP Server (Course Example)

> **A beginner-friendly Model Context Protocol (MCP) server that lets an AI
> assistant run real terminal commands on your computer** - macOS, Linux, or
> Windows. Built for students learning MCP.

---

## 📚 What is this?

This project is a **complete, working MCP server** written in a single, heavily
commented Python file (`terminal_server.py`). It teaches you, by reading real
code, how the pieces of MCP fit together.

When you finish this guide you will have:

1. An MCP server running on your own machine.
2. That server **connected to Claude Desktop**.
3. The ability to ask Claude things like:
   - *"Run `python3 --version` and tell me what's installed."*
   - *"What files are in my Downloads folder?"*
   - *"Which OS am I on and is `git` installed?"*
   - *"Run my tests and show me the failures."*

…and Claude will **actually run those commands on your terminal** and answer
from the real output.

---

## 🧠 MCP in one minute (the mental model)

**Model Context Protocol (MCP)** is an open standard that lets AI applications
("MCP **clients**", e.g. Claude Desktop) talk to programs that give the AI
superpowers ("MCP **servers**").

Think of it as **USB-C for AI** 🔌:

| Concept | Analogy | In this project |
|---|---|---|
| **MCP Client** | The laptop that wants to use devices | Claude Desktop |
| **MCP Server** | The device you plug in | `terminal_server.py` |
| **Tool** | A button on the device | `run_command`, `list_directory`, `get_system_info` |
| **Transport** | The cable | **stdio** (a pipe between two programs) |

### How the conversation works

1. Claude Desktop starts your server as a child process.
2. Both sides speak **JSON-RPC** messages over **stdin/stdout**.
3. Claude Desktop asks `tools/list` → your server replies with the 3 tools and
   their descriptions.
4. When Claude wants to act, it sends `tools/call` with a tool name + arguments.
5. Your server runs the code, returns the result, Claude reads it and continues.

> 📌 **stdio transport** means: your server prints JSON to **stdout** and reads
> JSON from **stdin**. That is why you must **never** `print()` normal text in
> the server file - it would corrupt the protocol! (That is why the code uses
> `logging`, which writes to **stderr**.)

---

## 📂 Project structure

```
mcp-server/
├── terminal_server.py    # 👈 THE server - read this top to bottom
├── requirements.txt      # 👈 lists the one Python dependency (the MCP SDK)
├── .gitignore            # keeps .venv/ and __pycache__ out of git
└── README.md             # 👈 you are here
```

**Everything interesting lives in `terminal_server.py`.** Open it now and read
the comments. Every section of the file is labelled so you can map it back to
the MCP concepts above.

### A guided tour of `terminal_server.py`

| Lines (approx.) | What it teaches |
|---|---|
| Top docstring | What MCP is, what transport we use, cross-platform notes |
| Imports | `subprocess` (runs commands), `platform`, `os`, the `MCPServer` class from the official SDK |
| Logging setup | Why logs go to **stderr** and never **stdout** |
| `mcp = MCPServer(...)` | Creating the server + giving Claude usage `instructions` |
| `run_command()` | **Tool 1** - the star. Runs any shell command with a timeout. |
| `list_directory()` | **Tool 2** - cross-platform file listing returning clean data |
| `get_system_info()` | **Tool 3** - reports OS, CPU, Python, which executables exist |
| `if __name__ == "__main__":` | Entry point - calls `mcp.run(transport="stdio")` |

> 💡 **The tools are defined as normal Python functions** with:
> - a **type-hinted signature** (MCP uses it to auto-generate the JSON schema
>   the client sends to Claude),
> - a **docstring** (becomes the tool description Claude reads to know when to
>   use the tool),
> - and an `@mcp.tool()` decorator that registers them.

---

## ✅ Prerequisites

- **Python 3.10 or newer** (the MCP SDK requires it; 3.12 is ideal).
  Check yours:
  ```bash
  python3 --version
  ```
  > On Windows the command may be `python --version` or `py --version`.
- **Claude Desktop** (installed + logged in) - see https://claude.ai/download.
- For the optional Inspector testing step: **Node.js** (`node --version`) to get
  `npx`. (You can skip the Inspector and still complete the course.)

---

## 🚀 Setup (everyone does this)

### Step 1 - Open a terminal in this folder

```bash
cd mcp-server          # the folder containing terminal_server.py
pwd                    # remember this absolute path - you'll need it!
```

### Step 2 - Create a virtual environment

A virtual environment ("venv") keeps this project's dependencies isolated from
the rest of your system. Best practice from day one!

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Your prompt should now show `(.venv)` at the start.

### Step 3 - Install the MCP SDK

```bash
pip install -r requirements.txt
```

This installs the official [`mcp`](https://pypi.org/project/mcp/) Python SDK -
the one and only dependency. Verify it worked:

```bash
python -c "import mcp; print('MCP SDK OK')"
```

### Step 4 - Confirm the server can start (sanity check)

The server *waits* for input when run, so just launching it and pressing Ctrl+C
is a great first test:

```bash
python terminal_server.py
```

You should see the two log lines on **stderr** (they won't appear in some
launchers, that's fine):

```
[INFO] Starting MCP server: Terminal Server (local)
[INFO] Listening on stdio. Connect me to an MCP client such as Claude Desktop.
```

Press `Ctrl+C` to stop it.

> 💡 The server is now a working MCP server over stdio! Any MCP client can talk
> to it. Claude Desktop is just the most famous one - and our next step.

---

## 🔌 Connecting to Claude Desktop

### Step 5 - Find Claude Desktop's config file

Claude Desktop reads a JSON file that lists all your MCP servers:

| OS | Config file location |
|---|---|
| **macOS** | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` (usually `C:\Users\YOURNAME\AppData\Roaming\Claude\...`) |
| **Linux** | `~/.config/Claude/claude_desktop_config.json` |

The file may not exist yet - that's normal, create it (including the `Claude`
folder if needed).

### Step 6 - Add your server to the config

Now we tell Claude Desktop to launch our server. **You need the absolute path to
your Python** (inside the venv) and **the absolute path to
`terminal_server.py`**.

Get both paths on **macOS/Linux**:

```bash
echo $VIRTUAL_ENV/bin/python        # prints e.g. /Users/YOU/mcp-server/.venv/bin/python
pwd                                  # your project folder
```

On **Windows (PowerShell)**:

```powershell
echo $env:VIRTUAL_ENV\Scripts\python.exe
pwd
```

Then paste the matching block below into the JSON config file.

<details>
<summary><b>🍎 macOS / 🐧 Linux config</b></summary>

```json
{
  "mcpServers": {
    "terminal-server": {
      "command": "/Users/YOU/mcp-server/.venv/bin/python",
      "args": ["/Users/YOU/mcp-server/terminal_server.py"]
    }
  }
}
```

Replace `/Users/YOU/mcp-server/` with **your** paths from Step 6.
</details>

<details>
<summary><b>🪟 Windows config</b></summary>

> ⚠️ In JSON, every backslash `\` must be doubled `\\`.

```json
{
  "mcpServers": {
    "terminal-server": {
      "command": "C:\\Users\\YOU\\mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOU\\mcp-server\\terminal_server.py"]
    }
  }
}
```

Replace `C:\\Users\\YOU\\mcp-server\\` with **your** paths from Step 6.
</details>

### Step 7 - Restart Claude Desktop

1. **Fully quit** Claude Desktop (Cmd+Q on Mac / close from the system tray on
   Windows).
2. Reopen it. Claude Desktop starts every MCP server listed in the config when
   it launches.
3. Look for the 🔌 **plug icon** (a tools/power icon) in the bottom-left of the
   input box. Click it - you should see **`terminal-server`** with 3 tools:
   `run_command`, `list_directory`, `get_system_info`.

> 💡 If you don't see the plug icon, click the little **grid/wrench icon**
> instead - the MCP servers live under the tools menu in some versions.

---

## 🎯 Try it!

Ask Claude anything that needs a terminal. Some great first prompts:

| Prompt | Which tool runs |
|---|---|
| "Run `python3 --version` and tell me what you see." | `run_command` |
| "List the files in my home directory." | `list_directory` |
| "What operating system am I on? Is git installed?" | `get_system_info` |
| "Run `ls -la` in this project folder and summarize what the files do." | `run_command` |
| "Check my disk space with `df -h`." | `run_command` |

Claude will ask for **permission before it runs a command** (good security
habit). Watch it *actually execute on your machine* and answer from the real
output. 🎉

---

## 🔬 Optional: Test with the MCP Inspector

[`@modelcontextprotocol/inspector`](https://github.com/modelcontextprotocol/inspector)
is an official GUI for debugging any MCP server - super useful during your
course. It shows live logs, lets you call tools with custom arguments, and see
raw JSON-RPC.

```bash
npx @modelcontextprotocol/inspector
```

Then in the opened page:

1. **Transport Type**: `STDIO`
2. **Command**: the path to your venv Python (same as Step 6)
3. **Args**: `["/path/to/terminal_server.py"]`
4. Click **Connect**, then try each tool and inspect the raw requests/responses.

---

## ❓ Troubleshooting

| Symptom | Likely fix |
|---|---|
| No plug icon / no tools in Claude Desktop | Quit Claude completely (Cmd+Q / tray) and reopen. MCP servers load at startup. |
| Config error shown in Claude Desktop | Check your JSON is valid. On Windows remember **double backslashes** `\\`. |
| Tool call fails with "command not found: X" | Run `get_system_info` first - the tool reports which executables are on `PATH`. |
| Permission prompt never appears / command runs unexpectedly | Claude Desktop always asks; if not, review your Claude privacy settings. |
| "Python not found" when Claude starts the server | Use the **absolute path** to your venv python (`which python` / `echo $VIRTUAL_ENV/bin/python`). |
| Still broken? | Run the exact `command` + `args` from your config **manually in a terminal** - if it errors there, Claude Desktop will too. |

---

## ⚠️ SECURITY - please read this twice

This server is **intentionally unrestricted** so you can learn. That means:

- Claude can run **any command** with **your** user permissions - including
  `rm -rf`, reading your files, or sending data over the network.
- Only use this on **your own machine**, and only with MCP clients you trust.
- Never share a machine running this server with untrusted people, and never
  run it on a shared/cloud machine for a demo.
- The `instructions` we pass to Claude ("prefer read-only, ask before
  destructive commands") is a **hint, not a security boundary**.

In a real product you would add a **command allowlist**. Great homework
exercise (below)! 🤓

---

## 🎓 Exercises for students

1. **Add an allowlist.** Give `run_command` a `blocklist` (e.g. refuse commands
   containing `rm -rf` or `format`) and return a friendly error instead.
2. **Add a tool.** Write `read_file(path)` that returns a file's contents using
   Python's built-in `open()`. Notice you don't need to touch any protocol
   code - just a function, a docstring and a decorator!
3. **Change the timeout.** Play with `MAX_TIMEOUT_SECONDS` and watch what
   happens with `sleep 100`.
4. **Switch transport.** Change `mcp.run(transport="stdio")` to
   `transport="streamable-http"` and read the MCP docs on HTTP servers. Compare
   the two.
5. **Wire protocol snooping.** In a terminal run
   `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python terminal_server.py`
   and look at the raw JSON that comes back. That's MCP, undressed. 🩲

---

## 📖 Further learning

- Official MCP docs: https://modelcontextprotocol.io
- MCP Python SDK on PyPI: https://pypi.org/project/mcp/
- MCP Python SDK source & examples: https://github.com/modelcontextprotocol/python-sdk

Happy building! 🚀
# mcp-server
