"""SSH readiness polling.

Drives `uqmm create` past the "QEMU is up, but cloud-init / setup-alpine
hasn't started sshd yet" gap. Pure stdlib — connects to the host-forwarded
port, reads the SSH banner, and keeps trying until it appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Coroutine
from typing import Any

_BANNER_PREFIX = b"SSH-"


async def wait_ready(
    host: str,
    port: int,
    # 1200s covers an Alpine FIRST boot under TCG with 1–2 vcpus: sshd's
    # initial RSA/ECDSA/ED25519 host-key generation is entropy-bound and
    # the emulator has no /dev/hwrng, so 4096-bit RSA keygen alone can
    # take 8+ min. Cloud-init guests reach SSH in 30–60s, and Alpine's
    # second boot also lands under a minute (keys already generated) — so
    # this is a generous first-boot ceiling, not the typical wait.
    timeout: float = 1200.0,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
) -> None:
    """Block until `host:port` answers with an SSH banner, or `timeout` elapses.

    Raises TimeoutError on deadline. Per-attempt connect timeout is small so a
    silently-dropping host doesn't block the loop for long.
    """
    deadline = time.monotonic() + timeout
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
    raise TimeoutError(f"SSH banner at {host}:{port} not seen within {timeout}s") from last_err


async def wait_ready_or_proc_exit(
    host: str,
    port: int,
    proc_coro: Coroutine[Any, Any, int],
    timeout: float = 1200.0,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
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
