"""SSH readiness polling.

Drives `uqmm create` past the "QEMU is up, but cloud-init / setup-alpine
hasn't started sshd yet" gap. Pure stdlib — connects to the host-forwarded
port, reads the SSH banner, and keeps trying until it appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

_BANNER_PREFIX = b"SSH-"


async def wait_ready(
    host: str,
    port: int,
    timeout: float = 300.0,  # noqa: ASYNC109 — explicit timeout matches caller ergonomics
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
