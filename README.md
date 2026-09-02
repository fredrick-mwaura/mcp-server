# 🖥️ Terminal Server - Your First MCP Server (Course Example)

> **A beginner-friendly Model Context Protocol (MCP) server that lets an AI
> assistant run real terminal commands on your computer** - macOS, Linux, or
> Windows. Built for students learning MCP. Connects to **opencode** as the MCP
> client.

---

## 📚 What is this?

This project is a **complete, working MCP server** written in a single, heavily
commented Python file (`terminal_server.py`). It teaches you, by reading real
code, how the pieces of MCP fit together.

When you finish this guide you will have:

1. An MCP server running on your own machine.
2. That server **connected to opencode**.
3. The ability to ask opencode things like:
   - *"Run `python3 --version` and tell me what's installed."*
   - *"What files are in my Downloads folder?"*
   - *"Which OS am I on and is `git` installed?"*
   - *"Run my tests and show me the failures."*

…and opencode will **actually run those commands on your terminal** and answer
from the real output.

---

## 🧠 MCP in one minute (the mental model)

**Model Context Protocol (MCP)** is an open standard that lets AI applications
("MCP **clients**", e.g. opencode) talk to programs that give the AI
superpowers ("MCP **servers**").

Think of it as **USB-C for AI** 🔌:

| Concept | Analogy | In this project |
|---|---|---|
| **MCP Client** | The laptop that wants to use devices | opencode |
| **MCP Server** | The device you plug in | `terminal_server.py` |
| **Tool** | A button on the device | `run_command`, `list_directory`, `get_system_info` |
| **Transport** | The cable | **stdio** (a pipe between two programs) |

### How the conversation works

1. opencode starts your server as a child process when it launches (it reads
   the server list from `opencode.json`).
2. Both sides speak **JSON-RPC** messages over **stdin/stdout**.
3. opencode asks `tools/list` → your server replies with the 3 tools and their
   descriptions.
4. When the agent wants to act, it sends `tools/call` with a tool name +
   arguments.
5. Your server runs the code, returns the result, the agent reads it and
   continues.

> 📌 **stdio transport** means: your server prints JSON to **stdout** and reads
> JSON from **stdin**. That is why you must **never** `print()` normal text in
> the server file - it would corrupt the protocol! (That is why the code uses
> `logging`, which writes to **stderr**.)

---

## 📂 Project structure

This repository is your whole MCP course:

```
mcp-server/
├── terminal_server.py       # 👈 THIS LESSON - the server - read it top to bottom
├── requirements.txt         # 👈 lists the one Python dependency (the MCP SDK)
├── opencode.json            # 👈 registers the servers with opencode
├── README.md                # 👈 you are here
│
├── PRODUCTION_PLAN.md       # next course unit: a production-grade file server
├── production-filesystem/   #   ...and its Phase 1 implementation
│
├── .venv/                   # virtual env (created in Step 2, not in git)
└── .gitignore               # keeps .venv/ and __pycache__ out of git
```

**Everything interesting for this lesson lives in `terminal_server.py`.** Open
it now and read the comments. Every section of the file is labelled so you can
map it back to the MCP concepts above.

### A guided tour of `terminal_server.py`

| Lines (approx.) | What it teaches |
|---|---|
| Top docstring | What MCP is, what transport we use, cross-platform notes |
| Imports | `subprocess` (runs commands), `platform`, `os`, the `MCPServer` class from the official SDK |
| Logging setup | Why logs go to **stderr** and never **stdout** |
| `mcp = MCPServer(...)` | Creating the server + giving the agent usage `instructions` |
| `run_command()` | **Tool 1** - the star. Runs any shell command with a timeout. |
| `list_directory()` | **Tool 2** - cross-platform file listing returning clean data |
| `get_system_info()` | **Tool 3** - reports OS, CPU, Python, which executables exist |
| `if __name__ == "__main__":` | Entry point - calls `mcp.run(transport="stdio")` |

> 💡 **The tools are defined as normal Python functions** with:
> - a **type-hinted signature** (MCP uses it to auto-generate the JSON schema
>   the client sends to the agent),
> - a **docstring** (becomes the tool description the agent reads to know when
>   to use the tool),
> - and an `@mcp.tool()` decorator that registers them.

---

## ✅ Prerequisites

- **Python 3.10 or newer** (the MCP SDK requires it; 3.12 is ideal).
  Check yours:
  ```bash
  python3 --version
  ```
  > On Windows the command may be `python --version` or `py --version`.
- **opencode** (installed) - see https://opencode.ai/docs/. This is the MCP
  client we connect to.
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
[INFO] Listening on stdio. Connect me to an MCP client such as opencode.
```

Press `Ctrl+C` to stop it.

> 💡 The server is now a working MCP server over stdio! Any MCP client can talk
> to it. opencode is the client we use in this course - and our next step.

---

## 🔌 Connecting to opencode

### Step 5 - Know where opencode reads its config

opencode is configured through an `opencode.json` (or `opencode.jsonc`) file.
opencode looks for it in the **project directory** (the folder you run opencode
from) and merges it with your global config at `~/.config/opencode/opencode.json`.

**A working `opencode.json` is already included in this repo** (we created it
so the course just works). Open it now and read the comments. The shape is:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "terminal-server": {
      "type": "local",
      "command": ["/Users/YOU/mcp-server/.venv/bin/python", "/Users/YOU/mcp-server/terminal_server.py"]
    }
  }
}
```

Key points for your own configs later:
- The `$schema` line gives your editor auto-completion and validation.
- `mcp` is an object keyed by server name (you get to pick the name).
- Every server needs `"type": "local"` (it launches a command) and
  `"command": [...]` - an **array** of strings, never a single string.

### Step 6 - Point the config at YOUR paths

The repo's `opencode.json` contains absolute paths that only work on the
teacher's machine. Replace them with **your** absolute paths from Step 1/2.

Get the path to your venv Python on **macOS/Linux**:

```bash
echo $VIRTUAL_ENV/bin/python        # prints e.g. /Users/YOU/mcp-server/.venv/bin/python
pwd                                  # your project folder
```

On **Windows (PowerShell)**:

```powershell
echo $env:VIRTUAL_ENV\Scripts\python.exe
pwd
```

<details>
<summary><b>🍎 macOS / 🐧 Linux opencode.json</b></summary>

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "terminal-server": {
      "type": "local",
      "command": ["/Users/YOU/mcp-server/.venv/bin/python", "/Users/YOU/mcp-server/terminal_server.py"]
    }
  }
}
```
</details>

<details>
<summary><b>🪟 Windows opencode.json</b></summary>

> ⚠️ In JSON, every backslash `\` must be doubled `\\`.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "terminal-server": {
      "type": "local",
      "command": ["C:\\Users\\YOU\\mcp-server\\.venv\\Scripts\\python.exe", "C:\\Users\\YOU\\mcp-server\\terminal_server.py"]
    }
  }
}
```
</details>

### Step 7 - Restart opencode

opencode reads its config **once, at startup** - it is not hot-reloaded.

1. **Quit opencode** completely.
2. Start it again from this project folder (`mcp-server`).
3. opencode now launches your server as a child process and connects over
   stdio. The tool is ready to use - just ask!

> 💡 Not seeing it? Run `/mcp` (or open the MCP tools list in your opencode UI)
> to confirm `terminal-server` is connected with its 3 tools: `run_command`,
> `list_directory`, `get_system_info`.

---

## 🎯 Try it!

Ask opencode anything that needs a terminal. Some great first prompts:

| Prompt | Which tool runs |
|---|---|
| "Run `python3 --version` and tell me what you see." | `run_command` |
| "List the files in my home directory." | `list_directory` |
| "What operating system am I on? Is git installed?" | `get_system_info` |
| "Run `ls -la` in this project folder and summarize what the files do." | `run_command` |
| "Check my disk space with `df -h`." | `run_command` |

opencode will ask for **permission before it runs a command** (good security
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
| Tools not available in opencode | Quit opencode completely and reopen it from the project folder - MCP servers load at startup, and the config is not hot-reloaded. |
| Config error when opencode starts | Check your `opencode.json` is valid JSON. On Windows remember **double backslashes** `\\`. Use `OPENCODE_DISABLE_PROJECT_CONFIG=1` to start while you fix it. |
| Tool call fails with "command not found: X" | Run `get_system_info` first - the tool reports which executables are on `PATH`. |
| Permission prompt never appears / command runs unexpectedly | opencode's permission rules decide this (see your `opencode.json` "permission" block). Defaults ask before running. |
| "Python not found" when opencode starts the server | Use the **absolute path** to your venv python (`which python` / `echo $VIRTUAL_ENV/bin/python`). |
| Still broken? | Run the exact `command` array from your config **manually in a terminal** - if it errors there, opencode will too. |

---

## ⚠️ SECURITY - please read this twice

This server is **intentionally unrestricted** so you can learn. That means:

- The agent can run **any command** with **your** user permissions - including
  `rm -rf`, reading your files, or sending data over the network.
- Only use this on **your own machine**, and only with MCP clients you trust.
- Never share a machine running this server with untrusted people, and never
  run it on a shared/cloud machine for a demo.
- The `instructions` we pass to the agent ("prefer read-only, ask before
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
- opencode MCP config docs: https://opencode.ai/docs/mcp-servers/

Happy building! 🚀
