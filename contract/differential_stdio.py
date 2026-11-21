#!/usr/bin/env python3
"""Initial black-box Python/Rust MCP differential probe.

This intentionally compares protocol-visible behavior rather than importing
either implementation. Search context fields are removed before comparison
because returning the requested context is a documented correction to the
Python implementation, not a compatibility regression.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class Server:
    def __init__(self, command: str, root: Path) -> None:
        environment = os.environ.copy()
        environment.update(
            MCP_FS_ROOTS=str(root),
            MCP_FS_MODE="read-only",
            MCP_FS_TRANSPORT="stdio",
            MCP_FS_LOG_FORMAT="text",
        )
        command_parts = shlex.split(command)
        executable = Path(command_parts[0])
        if not executable.is_absolute() and executable.parent != Path("."):
            command_parts[0] = str((Path.cwd() / executable).resolve())
        self.process = subprocess.Popen(
            command_parts,
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "differential-probe", "version": "1.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError(f"server exited while handling {method}")
        decoded = json.loads(response)
        if "error" in decoded:
            raise RuntimeError(f"JSON-RPC error from {method}: {decoded['error']}")
        return decoded["result"]

    def notify(self, method: str, params: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
            + "\n"
        )
        self.process.stdin.flush()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        self.process.kill()
        self.process.wait(timeout=5)


def normalize(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool == "grep_search":
        for group in payload.get("matches", []):
            for hit in group.get("hits", []):
                hit.pop("context_before", None)
                hit.pop("context_after", None)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-command", required=True)
    parser.add_argument("--rust-command", required=True)
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mcp-differential-") as directory:
        root = Path(directory)
        (root / "sample.py").write_text(
            '"""module"""\n\nclass Demo:\n    """doc"""\n'
            "    def method(self, value):\n        return value\n\n"
            "def top(value=1):\n    return value\n",
            encoding="utf-8",
        )
        (root / "note.txt").write_text("alpha\nbeta alpha\n", encoding="utf-8")
        python = Server(arguments.python_command, root)
        rust = Server(arguments.rust_command, root)
        try:
            python_tools = python.request("tools/list", {})["tools"]
            rust_tools = rust.request("tools/list", {})["tools"]
            python_contract = {
                tool["name"]: tool["inputSchema"] for tool in python_tools
            }
            rust_contract = {tool["name"]: tool["inputSchema"] for tool in rust_tools}
            if python_contract != rust_contract:
                raise AssertionError("tool names or input schemas differ")

            cases = [
                ("list_directory", {}),
                ("read_file", {"path": "sample.py"}),
                ("find_files", {"pattern": "*.py"}),
                ("grep_search", {"query": "alpha", "context_lines": 1}),
                ("symbol_outline", {"path": "sample.py"}),
            ]
            for tool, tool_arguments in cases:
                python_result = normalize(tool, python.call(tool, tool_arguments))
                rust_result = normalize(tool, rust.call(tool, tool_arguments))
                if python_result != rust_result:
                    raise AssertionError(
                        f"{tool} differs\nPython: {python_result}\nRust: {rust_result}"
                    )

            outside = str(root.parent)
            python_error = python.call("list_directory", {"path": outside})
            rust_error = rust.call("list_directory", {"path": outside})
            if python_error.get("error_code") != rust_error.get("error_code"):
                raise AssertionError(
                    f"security error differs: {python_error} != {rust_error}"
                )
        finally:
            python.close()
            rust.close()

    print("Python/Rust stdio differential probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
