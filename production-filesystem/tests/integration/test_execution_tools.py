"""Integration tests for run_test via the real MCPServer API.

Validates:
  - run_test works via server.call_tool in read-write mode.
  - Mode gate: run_test is strictly blocked in read-only mode (code execution requires write permission).
  - Security gate: path traversal outside allowed roots is blocked.
  - Distillation: failure trace is extracted and returned properly formatted.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def _call(server, tool: str, arguments: dict) -> dict:
    """Run one tools/call against the server and return its parsed dictionary."""
    async def _run():
        result = await server.call_tool(tool, arguments)
        if not result.content:
            return {"error_code": "no_content", "error": str(result)}
        text = result.content[0].text
        if text is None:
            return {}
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"text": text}
    return asyncio.run(_run())


def _rw_server(root: Path):
    settings = Settings(roots=[str(root)], mode="read-write", transport="stdio")
    return build_server(settings)


def _ro_server(root: Path):
    settings = Settings(roots=[str(root)], mode="read-only", transport="stdio")
    return build_server(settings)


def test_run_test_blocked_in_readonly_mode(tmp_path: Path) -> None:
    """run_test must be blocked when server is in read-only mode."""
    server = _ro_server(tmp_path)
    res = _call(server, "run_test", {
        "command": f"{sys.executable} --version",
        "path": str(tmp_path),
    })
    assert res["error_code"] == "read_only_mode"


def test_run_test_blocks_path_traversal(tmp_path: Path) -> None:
    """run_test must reject directories outside the allowed workspace."""
    server = _rw_server(tmp_path)
    res = _call(server, "run_test", {
        "command": f"{sys.executable} --version",
        "path": str(tmp_path.parent),
    })
    assert res["error_code"] == "path_not_allowed"


def test_run_test_success_in_readwrite_mode(tmp_path: Path) -> None:
    """run_test executes successfully in read-write mode."""
    server = _rw_server(tmp_path)
    res = _call(server, "run_test", {
        "command": f"{sys.executable} -c \"print('Ran 2 tests in 0.01s\\n\\nOK')\"",
        "path": str(tmp_path),
    })
    assert res["status"] == "passed"
    assert res["exit_code"] == 0
    assert res["counts"]["passed"] == 2


def test_run_test_failure_distillation_in_readwrite_mode(tmp_path: Path) -> None:
    """run_test isolates failures and assertions when a test fails."""
    # Write a failing test script
    test_file = tmp_path / "test_sample.py"
    test_file.write_text("""
import unittest

class SampleTest(unittest.TestCase):
    def test_failure(self):
        self.assertEqual(1 + 1, 3)

if __name__ == '__main__':
    unittest.main()
""")

    server = _rw_server(tmp_path)
    res = _call(server, "run_test", {
        "command": f"{sys.executable} test_sample.py",
        "path": str(tmp_path),
    })

    assert res["status"] == "failed"
    assert res["exit_code"] != 0
    assert res["counts"]["failed"] >= 1
    assert "AssertionError" in res["distilled_output"]
