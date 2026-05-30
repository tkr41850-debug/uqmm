"""SSH readiness polling.

Drives `uqmm create` past the "QEMU is up, but cloud-init / setup-alpine
hasn't started sshd yet" gap. Pure stdlib — connects to the host-forwarded
port, reads the SSH banner, and keeps trying until it appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

_BANNER_PREFIX = b"SSH-"


async def wait_ready(
    host: str,
    port: int,
    timeout: float | None = 1200.0,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
) -> None:
    """Block until `host:port` answers with an SSH banner, or `timeout` elapses.

    When *timeout* is ``None`` the loop runs indefinitely (no deadline).
    Raises TimeoutError on deadline. Per-attempt connect timeout is small so a
    silently-dropping host doesn't block the loop for long.
    """
    deadline = float("inf") if timeout is None else time.monotonic() + timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=2.0
            )
        except (ConnectionRefusedError, OSError, TimeoutError) as e:
            last_err = e
            await asyncio.sleep(1.0)
            continue
        try:
            banner = await asyncio.wait_for(reader.read(64), timeout=2.0)
        except TimeoutError as e:
            last_err = e
            banner = b""
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if banner.startswith(_BANNER_PREFIX):
            return
        await asyncio.sleep(1.0)
    timeout_label = "" if timeout is None else f" within {timeout}s"
    raise TimeoutError(f"SSH banner at {host}:{port} not seen{timeout_label}") from last_err


async def wait_ready_or_proc_exit(
    host: str,
    port: int,
    proc_coro: Coroutine[Any, Any, int],
    timeout: float | None = 1200.0,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
) -> None:
    """Race SSH readiness against process exit.

    Raises RuntimeError if the process exits before SSH becomes ready.
    """
    ssh_task: asyncio.Task[None] = asyncio.create_task(wait_ready(host, port, timeout=timeout))
    proc_task: asyncio.Task[int] = asyncio.create_task(proc_coro)
    done, pending = await asyncio.wait(
        {ssh_task, proc_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    if proc_task in done and ssh_task not in done:
        code = proc_task.result()
        raise RuntimeError(f"qemu exited with code {code} before SSH became ready; see install.log")
    if ssh_task in done:
        ssh_task.result()


async def wait_ready_or_pid_stop(
    host: str,
    port: int,
    pidfile: Path,
    timeout: float | None = None,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
) -> None:
    """Race SSH readiness against a pidfile-watched process death.

    Polls the PID in *pidfile* every second.  Returns when SSH is ready,
    or raises RuntimeError if the process dies first.
    """
    ssh_task: asyncio.Task[None] = asyncio.create_task(wait_ready(host, port, timeout=timeout))
    watch_task: asyncio.Task[None] = asyncio.create_task(_watch_pid(pidfile))
    done, pending = await asyncio.wait(
        {ssh_task, watch_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    if watch_task in done and ssh_task not in done:
        raise RuntimeError("QEMU process died before SSH became ready; check install.log")
    if ssh_task in done:
        ssh_task.result()


async def _watch_pid(pidfile: Path) -> None:
    """Return when the process identified by *pidfile* is no longer alive."""
    while True:
        if not await asyncio.to_thread(pidfile.exists):
            return
        try:
            pid = int(await asyncio.to_thread(pidfile.read_text))
        except (ValueError, OSError):
            return
        try:
            await asyncio.to_thread(os.kill, pid, 0)
            await asyncio.sleep(1.0)
        except ProcessLookupError:
            return
        except PermissionError:
            await asyncio.sleep(1.0)
