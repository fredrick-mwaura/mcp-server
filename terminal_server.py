#!/usr/bin/env python3
"""
===============================================================================
 terminal_server.py - Your First Model Context Protocol (MCP) Server
===============================================================================

 WHAT THIS FILE IS
 ------------------------------------------------------------------------------
 This is a complete, minimal MCP server. It exposes "tools" that an AI
 assistant (like Claude Desktop, Claude Code, or any MCP client) can call.

 The tools in this server let the AI assistant safely (well, as safely as we
 make it!) execute terminal commands on YOUR computer, and do a few small
 file-system / system tasks.

 THE BEAUTY OF MCP
 ------------------------------------------------------------------------------
 Before MCP, an AI assistant could only "talk". With MCP, the AI can *act*:
  - you can ask it to list your files,
  - check which OS you're on,
  - run a build or a test,
  - read the output, and keep working based on the result.

 This file is intentionally written as a SINGLE file with a LOT of comments.
 Read it top to bottom. Every block of code explains *why* it exists.

 THE TRANSPORT
 ------------------------------------------------------------------------------
 MCP servers can talk to clients over different "transports":
   1. stdio  - the server is started as a child process by the client, and the
               two sides exchange JSON-RPC messages over standard input (stdin)
               and standard output (stdout).  <<< THIS IS WHAT WE USE.
   2. Streamable HTTP - the server runs as its own HTTP web service.

 We use stdio because it is the simplest to set up with Claude Desktop.
 Because we write to stdout for protocol messages, we must NOT use print()
 anywhere in this file for normal output - it would corrupt the protocol!
 We always use logging (which writes to stderr) for our own debugging notes.

 CROSS-PLATFORM
 ------------------------------------------------------------------------------
 The commands we run through our `run_command` tool will work on:
   - macOS  (subprocess shell: /bin/sh, which is bash running in sh-mode)
   - Linux  (subprocess shell: /bin/sh)
   - Windows(subprocess shell: cmd.exe)
 Python's subprocess module hides most platform differences from us.
 We just have to be careful about a few things (timeouts, decoding, paths).

===============================================================================
"""

# -----------------------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------------------
import os          # operating-system features: env vars, current dir, paths
import platform    # tells us which OS / Python we are running on
import shutil      # finds executable paths like git, python, node
import subprocess  # the star of the show: runs terminal commands from Python
import logging     # prints diagnostic messages to STDERR (never to stdout!)
import sys         # gives us sys.stderr, where all our logs must go
from typing import Any  # type hint helper used in docstrings / annotations

# `MCPServer` is the high-level API from the official MCP Python SDK (v2+).
# It lets us declare tools by just decorating plain Python functions, and it
# handles all of the low-level JSON-RPC protocol work for us.
# (In MCP SDK v1 the same class was named `FastMCP`; the API we use here is
# identical, so tutorials that mention FastMCP still apply almost verbatim.)
from mcp.server.mcpserver import MCPServer

# -----------------------------------------------------------------------------
# LOGGING SETUP  (why does this matter?)
# -----------------------------------------------------------------------------
# MCP over stdio uses STDOUT for protocol JSON messages. If our code wrote a
# random print() to stdout, the client would choke on garbage that is not
# valid JSON-RPC. So we route ALL human-readable logging to STDERR instead.
# `force=True` makes this work even though we run in a script.
logging.basicConfig(
    level=logging.INFO,            # show INFO and above (INFO, WARNING, ERROR)
    format="[%(asctime)s] %(levelname)s: %(message)s",
    stream=sys.stderr,             # <-- always send logs to stderr, not stdout
    force=True,                    # override any pre-existing logging config
)

# -----------------------------------------------------------------------------
# THE APP NAME
# -----------------------------------------------------------------------------
# Every MCP server has a name. The client uses it in its UI (e.g. Claude
# Desktop lists "Terminal Server (local)" next to the tools it provides).
APP_NAME = "Terminal Server (local)"

# -----------------------------------------------------------------------------
# CREATE THE MCP SERVER OBJECT
# -----------------------------------------------------------------------------
# `MCPServer` takes care of ALL the low-level MCP protocol work for us.
# We just register tools/functions on it, then call `mcp.run()` at the bottom.
mcp = MCPServer(
    APP_NAME,
    # `instructions` is optional, but nice: the SDK sends it to the client so
    # the AI model gets a short "how to use me" cheat sheet on connection.
    instructions=(
        "You can run terminal commands on the user's computer. "
        "Prefer read-only commands. Always use timeouts. "
        "Never run destructive commands without asking the user first."
    ),
)


# -----------------------------------------------------------------------------
# SECURITY NOTES (READ THIS!)
# -----------------------------------------------------------------------------
# WARNING: Tools on this server execute with the SAME permissions as the user
# who launched the server. That makes this server VERY powerful and therefore
# potentially dangerous. Use it only on your own machine, and only with MCP
# clients you trust (e.g. your own Claude Desktop).
#
# A real production terminal server would add an ALLOWLIST of permitted
# commands. For this course we keep it open, and instead teach you the risk.

# Safe default timeout (seconds) for any single command we run.
DEFAULT_TIMEOUT_SECONDS = 30
# Absolute upper limit - protects you from a command that hangs forever.
MAX_TIMEOUT_SECONDS = 300


# ==============================================================================
# TOOL 1 OF 3  ->  run_command
# ==============================================================================
@mcp.tool()
def run_command(
    command: str,
    working_directory: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a shell command on the host operating system and return its output.

    The command is executed by your default system shell:
      - macOS/Linux : /bin/sh  (macOS: bash in sh-mode; many Linuxes: dash)
      - Windows     : cmd.exe

    Handy for anything you would type in a real terminal:
    checking versions (git --version), listing files (ls / dir),
    running tests or builds, checking network (ping), etc.

    Args:
        command: The exact command line to run, e.g. "ls -la".
        working_directory: Optional folder to run the command in. Defaults to
            the server's current directory.
        timeout_seconds: How many seconds to wait before giving up. Defaults
            to 30. The absolute maximum allowed is 300 (5 minutes).

    Returns:
        A dictionary with exit code, captured stdout, captured stderr, and
        (when relevant) the directory the command actually ran in.
    """
    # --- validate the timeout ------------------------------------------------
    # A student must understand: user input must be validated BEFORE we touch
    # subprocess, otherwise a bad value could hang the server forever.
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        return {
            "error": f"timeout_seconds must be between 1 and "
                     f"{MAX_TIMEOUT_SECONDS}. You passed {timeout_seconds}."
        }

    # --- decide which directory to run in -----------------------------------
    # Default to the folder the server process was launched from. This is the
    # same directory you see when you type `pwd` in a fresh terminal.
    if working_directory is None or working_directory.strip() == "":
        working_directory = os.getcwd()

    # Expand things like "~" and resolve relative paths like "../project".
    working_directory = os.path.abspath(os.path.expanduser(working_directory))

    # If the folder does not exist, fail fast with a clear, friendly message.
    if not os.path.isdir(working_directory):
        return {
            "error": f"working_directory does not exist: {working_directory}"
        }

    # --- log what we are about to do (goes to STDERR, never stdout) ---------
    logging.info(f"Running command: {command!r} in {working_directory}")

    # --- actually run the command --------------------------------------------
    try:
        # subprocess.run() blocks until the command finishes. We hand it:
        #   - a string command + shell=True  -> the OS's default shell parses it
        #   - capture_output=True            -> grab stdout AND stderr
        #   - encoding="utf-8"               -> decode output the same on all OSes
        #   - errors="replace"               -> never crash on weird characters
        #   - cwd=...                        -> change into that directory first
        #   - timeout=...                    -> stop waiting after N seconds
        result = subprocess.run(
            command,
            shell=True,             # let the shell handle pipes, &&, |, etc.
            capture_output=True,    # return output instead of printing it
            encoding="utf-8",       # decode output as UTF-8 on every operating system
            errors="replace",       # replace undecodable bytes with a marker
            cwd=working_directory,  # run inside the requested folder
            timeout=timeout_seconds,
        )

        # Build a clean dictionary result. The MCP SDK serializes dicts to
        # JSON text, so the AI assistant receives structured data it can read.
        return {
            "exit_code": result.returncode,  # 0 == success on every OS
            "stdout": result.stdout,          # what the command printed out
            "stderr": result.stderr,          # what the command complained about
            "working_directory": working_directory,
        }

    except subprocess.TimeoutExpired:
        # subprocess raises this if the command runs longer than the timeout.
        # We catch it so the server stays alive and can answer the next request.
        logging.warning(f"Command timed out after {timeout_seconds}s: {command!r}")
        return {
            "error": (
                f"The command did not finish within {timeout_seconds} seconds "
                "and was killed. Try a longer timeout, or a more specific command."
            )
        }

    except Exception as exc:  # noqa: BLE001 - catch anything else gracefully
        # A truly unexpected failure (bad encoding, OS quirk, ...). Report it
        # clearly instead of crashing the whole server.
        logging.exception(f"Unexpected error running {command!r}")
        return {"error": f"Failed to run command: {exc}"}


# ==============================================================================
# TOOL 2 OF 3  ->  list_directory
# ==============================================================================
@mcp.tool()
def list_directory(path: str = ".") -> dict[str, Any]:
    """List the files and folders inside a directory.

    Prefer this over `ls`/`dir` for simple listing because it returns clean
    structured data (names, sizes, file vs. folder) that is easy for you to
    reason about, and it works identically on macOS, Linux and Windows.

    Args:
        path: The directory to inspect. Defaults to the current directory.
              Supports relative paths ("..", "src") and "~" for your home.

    Returns:
        A dictionary with the absolute path inspected and a list of entries.
    """
    # Resolve the user-supplied path the same way we did for working_directory.
    target = os.path.abspath(os.path.expanduser(path))

    if not os.path.isdir(target):
        return {"error": f"Not a directory (or does not exist): {target}"}

    entries = []  # we will fill this with one small dict per item
    # os.scandir() is more efficient than os.listdir() because it does not
    # materialize the whole list up front, and gives us stats for free.
    with os.scandir(target) as it:
        for entry in it:
            try:
                is_dir = entry.is_dir()          # True if it is a folder
                # os.scandir avoids extra syscalls: we pass follow_symlinks=False
                # so a symlink is reported as a link, not resolved silently.
                size = entry.stat(follow_symlinks=False).st_size if not is_dir else None
            except OSError:
                # A permission error on one entry should not kill the whole call.
                is_dir, size = False, None
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if is_dir else "file",
                    # Show human-friendly sizes for files, None for directories.
                    "size_bytes": size,
                }
            )

    # Sort: directories first, then files - each group alphabetically.
    entries.sort(key=lambda e: (e["type"] != "directory", e["name"].lower()))

    return {
        "absolute_path": target,
        "entry_count": len(entries),
        "entries": entries,
    }


# ==============================================================================
# TOOL 3 OF 3  ->  get_system_info
# ==============================================================================
@mcp.tool()
def get_system_info() -> dict[str, Any]:
    """Get information about the computer the server is running on.

    Useful when you are not sure of the environment (macOS vs Linux vs
    Windows), which Python version is present, whether git exists, etc.
    Run this before assuming which shell commands will work.

    Returns:
        A dictionary describing the operating system, machine and tooling.
    """
    info: dict[str, Any] = {}

    # --- basic operating system details --------------------------------------
    # platform.system()  -> 'Darwin' (macOS), 'Linux', 'Windows', ...
    # platform.release() -> kernel version, e.g. '23.5.0' or '6.1.0'
    info["os_name"] = platform.system()
    info["os_release"] = platform.release()
    info["machine"] = platform.machine()  # e.g. 'arm64' (Apple Silicon) / 'x86_64'
    info["architecture"] = platform.architecture()[0]  # '64bit' / '32bit'

    # macOS extra detail, e.g. 'macOS 14.5'. Wrapped in a try/except because
    # platform.mac_ver() can fail on non-mac systems.
    if platform.system() == "Darwin":
        mac_ver = platform.mac_ver()[0]
        info["os_pretty_name"] = f"macOS {mac_ver}" if mac_ver else "macOS"
    else:
        info["os_pretty_name"] = platform.system()

    # --- runtime details ------------------------------------------------------
    info["python_version"] = platform.python_version()  # the Python we run under
    info["current_directory"] = os.getcwd()             # where commands run by default
    info["cpu_count"] = os.cpu_count()                  # logical CPU cores
    info["hostname"] = platform.node()                  # computer name on network

    # --- check a few common executables ---------------------------------------
    # shutil.which() searches the PATH just like a shell would, returning the
    # full path to the executable (or None if it cannot be found). Telling the
    # AI which tools exist prevents it from trying commands that will fail.
    executables = {}
    for name in ("git", "python3", "node", "npm", "docker", "curl", "pip"):
        found = shutil.which(name)
        executables[name] = found if found else "NOT INSTALLED"
    info["available_executables"] = executables

    return info


# ==============================================================================
# MAIN ENTRY POINT  (where the program actually starts)
# ==============================================================================
if __name__ == "__main__":
    # A friendly note printed to STDERR so you (the student) see the server
    # start. Claude Desktop, on the other hand, starts this file invisibly.
    logging.info(f"Starting MCP server: {APP_NAME}")
    logging.info(
        "Listening on stdio. Connect me to an MCP client such as Claude Desktop."
    )

    # mcp.run(transport="stdio") starts the server using the STDIO transport.
    # The server now sits waiting on stdin for JSON-RPC requests. It will
    # answer 'tools/list', 'tools/call' etc. until the client closes stdin.
    #
    # (To run over HTTP instead you would pass mcp.run(transport="streamable-http"))
    mcp.run(transport="stdio")
