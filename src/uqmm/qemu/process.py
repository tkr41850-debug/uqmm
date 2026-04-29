"""Async QEMU subprocess launcher.

Spawns qemu-system-* with stdout discarded, stderr piped + drained to
a log file (avoiding the pipe-deadlock gotcha — see toolchain.md #5),
and writes the PID to a pidfile so the lifecycle layer can find the
process across CLI invocations.
"""

from __future__ import annotations

import asyncio
from asyncio.subprocess import DEVNULL, PIPE, Process
from pathlib import Path

# Background drain tasks live for the lifetime of their QEMU process. Holding
# strong refs in this set prevents the GC from collecting them mid-run (ruff
# RUF006); they self-remove on completion.
_DRAIN_TASKS: set[asyncio.Task[None]] = set()


async def launch(args: list[str], pidfile: Path, stderr_log: Path) -> Process:
    """Spawn `args` as a subprocess; drain stderr to `stderr_log`; write PID."""
    proc = await asyncio.create_subprocess_exec(*args, stdout=DEVNULL, stderr=PIPE)
    pidfile.write_text(f"{proc.pid}\n")  # noqa: ASYNC240 — tiny synchronous write
    drain = asyncio.create_task(_drain_stderr(proc, stderr_log))
    _DRAIN_TASKS.add(drain)
    drain.add_done_callback(_DRAIN_TASKS.discard)
    return proc


async def _drain_stderr(proc: Process, stderr_log: Path) -> None:
    if proc.stderr is None:
        return
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stderr_log.open("ab") as f:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            _ = f.write(line)
            f.flush()
