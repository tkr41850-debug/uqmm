"""Pexpect over a QEMU bidirectional unix socket.

QEMU is started with `-serial unix:<path>,server=on,wait=on`; this module
connects a stdlib socket to that path (retrying until the socket file
appears), wraps it in `pexpect.socket_pexpect.SocketSpawn`, and tees all
serial output to an install.log file.

The Alpine drive script runs synchronously in a thread (pexpect is sync);
the *outer* coordination layer awaits it via `loop.run_in_executor`.
"""

# pexpect ships no type stubs; surface noise from unknown types is suppressed
# at the module level rather than ignored at every call site.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportReturnType=false, reportUnknownArgumentType=false, reportIncompatibleMethodOverride=false, reportAttributeAccessIssue=false

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

from pexpect.socket_pexpect import SocketSpawn


class _LoggingSocketSpawn(SocketSpawn):
    """SocketSpawn that honors logfile_read — pexpect's stock SocketSpawn
    overrides read_nonblocking to call socket.recv directly, skipping the
    _log call that the base spawn uses to populate logfile_read. We re-add
    it here so install.log captures everything QEMU sent us.
    """

    def read_nonblocking(self, size: int = 1, timeout: float = -1) -> bytes:
        s = super().read_nonblocking(size, timeout)
        self._log(s, "read")
        return s


async def open_serial(
    sock_path: Path,
    log_path: Path,
    timeout: float = 30.0,  # noqa: ASYNC109 — explicit deadline matches caller ergonomics
) -> SocketSpawn:
    """Connect to QEMU's serial unix socket; tee output to `log_path`.

    QEMU creates the socket asynchronously after launch; retry until it's
    listening or the deadline expires.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(sock_path))
            break
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            last_err = e
            s.close()
            await asyncio.sleep(0.1)
    else:
        raise TimeoutError(f"serial socket {sock_path} not ready within {timeout}s") from last_err

    # logfile_read captures everything QEMU sends us (which is what the user
    # cares about); logfile_send would just echo our typed commands back.
    # buffering=0 ensures bytes hit disk as the install runs — important for
    # `uqmm log --follow` to be useful before the install completes.
    log = log_path.open("ab", buffering=0)
    spawn = _LoggingSocketSpawn(s, timeout=120, encoding=None)
    spawn.logfile_read = log
    return spawn
