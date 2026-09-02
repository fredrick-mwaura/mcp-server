"""Unit tests for execution tools and test failure distillation (Phase 5).

Tests:
  - distill_test_output on pytest passing & failing outputs.
  - distill_test_output on unittest passing & failing outputs.
  - Failure trace capping and truncation limits.
  - run_test execution, timeout handling, and missing command handling.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mcp_fs.errors import PathNotFoundError
from mcp_fs.tools.execution import distill_test_output, run_test


def test_distill_pytest_success() -> None:
    """Passing pytest output is distilled to a compact summary with 0 failures."""
    stdout = """
============================= test session starts ==============================
platform darwin -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: /project
collected 5 items

tests/test_a.py .....                                                    [100%]

============================== 5 passed in 0.25s ===============================
"""
    res = distill_test_output(stdout, "", 0, 0.25)
    assert res["status"] == "passed"
    assert res["counts"]["passed"] == 5
    assert res["counts"]["failed"] == 0
    assert res["failures"] == []
    assert res["distilled_output"] is None
    assert "5 passed in 0.25s" in res["summary"]


def test_distill_pytest_failure() -> None:
    """Failing pytest output extracts the exact failure section and assertion."""
    stdout = """
============================= test session starts ==============================
collected 3 items

tests/test_math.py .F.                                                   [100%]

=================================== FAILURES ===================================
__________________________________ test_add ___________________________________

    def test_add():
>       assert add(1, 2) == 4
E       AssertionError: assert 3 == 4

tests/test_math.py:10: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 4
========================= 1 failed, 2 passed in 0.15s ==========================
"""
    res = distill_test_output(stdout, "", 1, 0.15)
    assert res["status"] == "failed"
    assert res["counts"]["failed"] == 1
    assert res["counts"]["passed"] == 2
    assert "AssertionError: assert 3 == 4" in res["distilled_output"]
    assert "test_add" in res["distilled_output"]
    # Verify boilerplate platform header was stripped
    assert "test session starts" not in res["distilled_output"]


def test_distill_unittest_success() -> None:
    """Passing unittest output is recognized and summarized."""
    output = "Ran 12 tests in 0.045s\n\nOK\n"
    res = distill_test_output(output, "", 0, 0.045)
    assert res["status"] == "passed"
    assert res["counts"]["passed"] == 12
    assert res["counts"]["failed"] == 0


def test_distill_unittest_failure() -> None:
    """Failing unittest output extracts the traceback."""
    output = """
======================================================================
FAIL: test_broken (test_pkg.TestApp)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_pkg.py", line 15, in test_broken
    self.assertEqual(a, b)
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 4 tests in 0.012s

FAILED (failures=1)
"""
    res = distill_test_output(output, "", 1, 0.012)
    assert res["status"] == "failed"
    assert res["counts"]["failed"] == 1
    assert "AssertionError: 1 != 2" in res["distilled_output"]


def test_distill_failure_line_capping() -> None:
    """Massive failure outputs are capped at max_failure_lines to preserve context budget."""
    long_failures = "\n".join(f"E       AssertionError: detail line {i}" for i in range(200))
    stdout = f"=== FAILURES ===\n{long_failures}\n=== 10 failed in 1.0s ==="

    res = distill_test_output(stdout, "", 1, 1.0, max_failure_lines=20)
    assert res["truncated"] is True
    assert "[... Failure trace truncated at 20 lines" in res["distilled_output"]


def test_run_test_success(tmp_path: Path) -> None:
    """run_test executes passing command and returns distilled output."""
    res = run_test(tmp_path, f"{sys.executable} -c \"print('Ran 1 test in 0.01s\\n\\nOK')\"")
    assert res["status"] == "passed"
    assert res["exit_code"] == 0


def test_run_test_timeout_handling(tmp_path: Path) -> None:
    """run_test cleanly catches timeout and returns structured timeout status."""
    res = run_test(
        tmp_path,
        f"{sys.executable} -c \"import time; time.sleep(5)\"",
        timeout=1.0,
    )
    assert res["status"] == "timeout"
    assert res["timed_out"] is True
    assert res["timeout_seconds"] == 1.0


def test_run_test_missing_executable(tmp_path: Path) -> None:
    """run_test on a non-existent command returns structured error."""
    res = run_test(tmp_path, "nonexistent_executable_12345 --flag")
    assert res["status"] == "error"
    assert "not found" in res["error"]


def test_run_test_nonexistent_directory(tmp_path: Path) -> None:
    """run_test on a missing directory raises PathNotFoundError."""
    with pytest.raises(PathNotFoundError):
        run_test(tmp_path / "ghost", f"{sys.executable} --version")
