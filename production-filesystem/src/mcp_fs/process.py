"""Bounded subprocess capture with process-tree timeout cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool


def _drain(stream, limit: int, chunks: list[bytes], state: list[bool]) -> None:
    retained = 0
    while True:
        chunk = stream.read(65_536)
        if not chunk:
            break
        remaining = max(0, limit - retained)
        if remaining:
            chunks.append(chunk[:remaining])
            retained += min(len(chunk), remaining)
        if len(chunk) > remaining:
            state[0] = True


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_bounded(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        list(args), cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None, start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    assert proc.stdout is not None and proc.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_truncated = [False]
    stderr_truncated = [False]
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, max_output_bytes, stdout_chunks, stdout_truncated), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, max_output_bytes, stderr_chunks, stderr_truncated), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(proc)
        proc.wait()
    for thread in threads:
        thread.join(timeout=2)
    return ProcessResult(
        proc.returncode,
        b"".join(stdout_chunks).decode("utf-8", errors="replace"),
        b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        stdout_truncated[0], stderr_truncated[0], timed_out,
    )
