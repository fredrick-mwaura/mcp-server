"""execution.py - Bounded test runner and smart failure distillation (Phase 5).

This module provides token-frugal test verification for software engineering
and autonomous SWE-bench evaluation:

  run_test:             Executes test suites (pytest, unittest, etc.) in a vetted
                        working directory with strict timeout boundaries.
  distill_test_output:  Extracts failing assertions, tracebacks, and summary metrics
                        while discarding thousands of lines of passing logs and
                        chatter, achieving ~90% token savings.

Guarded by the server's read-write mode gate (executing test code modifies state
and runs host processes).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from mcp_fs.errors import PathNotFoundError


def distill_test_output(
    stdout: str,
    stderr: str,
    returncode: int,
    duration_seconds: float,
    max_failure_lines: int = 120,
) -> dict[str, object]:
    """Distill raw test output down to essential failure traces and counts.

    Strips away boilerplate platform headers, passing test chatter (e.g. 500 dots),
    and repetitive warnings, preserving only the failing test names, exact
    assertion failures, and stack frames.

    Args:
        stdout:            Standard output from test process.
        stderr:            Standard error from test process.
        returncode:        Process exit code (0 for success, non-zero for failure).
        duration_seconds:  Elapsed execution time.
        max_failure_lines: Maximum lines of failure details returned.

    Returns:
        A dictionary with status, counts, summary line, and distilled failure traces.
    """
    combined = stdout + ("\n" + stderr if stderr.strip() else "")

    # Parse counts and summary
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    errors_count = 0
    summary_line = ""

    # Check for pytest summary line: e.g. "== 1 failed, 106 passed in 3.48s =="
    pytest_summary_match = re.search(r"=+ (.* in [\d\.]+s.*) =+", combined)
    if pytest_summary_match:
        summary_line = pytest_summary_match.group(1).strip()
        passed_m = re.search(r"(\d+)\s+passed", summary_line)
        failed_m = re.search(r"(\d+)\s+failed", summary_line)
        skipped_m = re.search(r"(\d+)\s+skipped", summary_line)
        errors_m = re.search(r"(\d+)\s+error", summary_line)
        if passed_m:
            passed_count = int(passed_m.group(1))
        if failed_m:
            failed_count = int(failed_m.group(1))
        if skipped_m:
            skipped_count = int(skipped_m.group(1))
        if errors_m:
            errors_count = int(errors_m.group(1))

    # Check for unittest summary: e.g. "FAILED (failures=1, errors=2)" or "OK"
    elif "Ran " in combined:
        ran_m = re.search(r"Ran (\d+) tests? in ([\d\.]+s)", combined)
        if ran_m:
            total_ran = int(ran_m.group(1))
            summary_line = f"Ran {total_ran} tests in {ran_m.group(2)}"
            fail_m = re.search(r"failures=(\d+)", combined)
            err_m = re.search(r"errors=(\d+)", combined)
            skip_m = re.search(r"skipped=(\d+)", combined)
            if fail_m:
                failed_count = int(fail_m.group(1))
            if err_m:
                errors_count = int(err_m.group(1))
            if skip_m:
                skipped_count = int(skip_m.group(1))
            passed_count = max(0, total_ran - failed_count - errors_count - skipped_count)

    # General status determination
    is_success = returncode == 0 and failed_count == 0 and errors_count == 0
    status = "passed" if is_success else "failed"

    # If all tests passed, return token-efficient compact confirmation
    if is_success:
        return {
            "status": "passed",
            "exit_code": 0,
            "duration_seconds": round(duration_seconds, 2),
            "summary": summary_line or "All tests passed successfully.",
            "counts": {
                "passed": passed_count,
                "failed": 0,
                "skipped": skipped_count,
                "errors": 0,
            },
            "failures": [],
            "distilled_output": None,
        }

    # Extract pytest FAILURES section if present
    distilled_lines: list[str] = []
    if "=== FAILURES ===" in combined:
        parts = re.split(r"=+\s+FAILURES\s+=+", combined, maxsplit=1)
        if len(parts) > 1:
            failures_section = parts[1]
            # Strip off the footer summary info
            footer_split = re.split(r"=+\s+(?:short test summary info|warnings summary|\d+ failed)\s+=+", failures_section, maxsplit=1)
            failure_body = footer_split[0].strip()

            # Filter out internal pytest noise, keeping assertion errors and tracebacks
            for line in failure_body.splitlines():
                if len(distilled_lines) >= max_failure_lines:
                    break
                distilled_lines.append(line)

    # Fallback extraction: extract error/traceback lines
    if not distilled_lines:
        lines = combined.splitlines()
        capturing = False
        for line in lines:
            if len(distilled_lines) >= max_failure_lines:
                break
            # Start capturing on tracebacks or failure notices
            if any(k in line for k in ("FAIL:", "ERROR:", "Traceback (most recent call last):", "FAILED ")):
                capturing = True
            if capturing:
                distilled_lines.append(line)
                if line.startswith("AssertionError") or line.startswith("Error:"):
                    # include line and continue
                    pass

    # If still empty (generic non-zero exit command), return the last lines
    if not distilled_lines:
        distilled_lines = [l for l in combined.splitlines() if l.strip()][-max_failure_lines:]

    # Truncate notice if capped
    truncated = len(distilled_lines) >= max_failure_lines
    if truncated:
        distilled_lines.append(
            f"\n[... Failure trace truncated at {max_failure_lines} lines to preserve tokens. Run with targeted test path to isolate. ...]"
        )

    return {
        "status": status,
        "exit_code": returncode,
        "duration_seconds": round(duration_seconds, 2),
        "summary": summary_line or f"Tests failed with exit code {returncode}.",
        "counts": {
            "passed": passed_count,
            "failed": failed_count or (1 if returncode != 0 else 0),
            "skipped": skipped_count,
            "errors": errors_count,
        },
        "truncated": truncated,
        "distilled_output": "\n".join(distilled_lines),
    }


def run_test(
    canonical_path: Path,
    command: str,
    *,
    timeout: float = 90.0,
    distill: bool = True,
) -> dict[str, object]:
    """Execute a test command in the approved working directory.

    Runs test suites with a strict timeout boundary and returns token-distilled
    results isolating failure assertions and summary metrics.

    Args:
        canonical_path: An approved, canonical directory where the test will run.
        command:        The shell command string (e.g. 'pytest tests/test_app.py -k test_foo').
        timeout:        Maximum seconds before terminating process (default 90s, capped at 300s).
        distill:        If True, strips boilerplate and passing chatter (default True).

    Returns:
        A dictionary with execution status, counts, duration, and distilled failures.
    """
    if not os.path.isdir(canonical_path):
        raise PathNotFoundError(str(canonical_path))

    # Clamp timeout between 1s and 300s
    clamped_timeout = max(1.0, min(float(timeout), 300.0))

    cmd_args = shlex.split(command)
    if not cmd_args:
        return {
            "status": "error",
            "command": command,
            "error": "Command string cannot be empty.",
        }

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd_args,
            cwd=str(canonical_path),
            capture_output=True,
            text=True,
            timeout=clamped_timeout,
        )
        duration = time.perf_counter() - started
        stdout = proc.stdout
        stderr = proc.stderr
        returncode = proc.returncode

    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - started
        return {
            "status": "timeout",
            "command": command,
            "timed_out": True,
            "timeout_seconds": clamped_timeout,
            "duration_seconds": round(duration, 2),
            "summary": f"Test execution timed out after {clamped_timeout}s.",
            "detail": (
                f"Process was terminated after exceeding the {clamped_timeout}s timeout limit. "
                "Check for infinite loops, deadlocks, or hanging network calls."
            ),
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "command": command,
            "error": f"Executable '{cmd_args[0]}' not found on system PATH.",
        }

    if distill:
        res = distill_test_output(stdout, stderr, returncode, duration)
        res["command"] = command
        return res
    else:
        return {
            "status": "passed" if returncode == 0 else "failed",
            "command": command,
            "exit_code": returncode,
            "duration_seconds": round(duration, 2),
            "stdout": stdout,
            "stderr": stderr,
        }
