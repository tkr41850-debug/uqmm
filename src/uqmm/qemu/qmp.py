"""Thin async wrapper over qemu.qmp.QMPClient.

QEMU only opens its QMP unix socket once it's started accepting clients,
so callers see a brief window where `connect` raises FileNotFoundError /
ConnectionRefusedError. `connect` here retries with backoff until the
socket is up or the deadline expires.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from qemu.qmp import QMPClient


async def connect(sock: Path, timeout: float = 30.0) -> QMPClient:  # noqa: ASYNC109
    """Open a QMP client, retrying until the socket is listening."""
    client = QMPClient(name="uqmm")
    deadline = time.monotonic() + timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            await client.connect(str(sock))
            return client
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            last_err = e
            await asyncio.sleep(0.1)
    raise TimeoutError(f"QMP socket {sock} not ready within {timeout}s") from last_err


async def system_powerdown(client: QMPClient) -> None:
    """Send the ACPI power-button event to the guest. Graceful."""
    _ = await client.execute("system_powerdown")


async def quit(client: QMPClient) -> None:
    """Force QEMU to exit immediately. Ungraceful. (Name matches the QMP verb.)"""
    _ = await client.execute("quit")


async def wait_shutdown(client: QMPClient, timeout: float) -> bool:  # noqa: ASYNC109
    """Block until a SHUTDOWN event arrives or `timeout` elapses.

    Returns True on SHUTDOWN, False on timeout.
    """
    try:
        async with asyncio.timeout(timeout):
            with client.listener() as events:
                async for ev in events:
                    if ev.get("event") == "SHUTDOWN":
                        return True
    except TimeoutError:
        return False
    return False
