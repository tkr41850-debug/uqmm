"""Status discovery: pidfile → QMP → SSH banner cascade.

See docs/design/cli.md § Status discovery.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from pathlib import Path
from typing import Literal

from uqmm.config import VMConfig
from uqmm.qemu import qmp

Status = Literal[
    "not-created", "stopped", "starting", "running", "unreachable", "failed", "invalid-config"
]


async def probe(vm_dir: Path) -> Status:
    """Determine the runtime state of the VM rooted at `vm_dir`."""
    cfg_path = vm_dir / "config.json"
    if not cfg_path.exists():
        return "not-created"

    try:
        cfg = VMConfig.load(cfg_path)
    except (ValueError, OSError):
        return "invalid-config"

    if cfg.state == "failed":
        return "failed"

    if cfg.state == "creating":
        return "failed"

    pidfile = vm_dir / "qemu.pid"
    if not pidfile.exists():
        return "stopped"

    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        # Retry once after 50ms — a mid-write partial read resolves quickly.
        await asyncio.sleep(0.05)
        try:
            pid = int(pidfile.read_text().strip())
        except (ValueError, OSError):
            pidfile.unlink(missing_ok=True)
            return "stopped"

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # PID dead — clean up stale pidfile so subsequent probes are fast.
        pidfile.unlink(missing_ok=True)
        return "stopped"
    except PermissionError:
        # PID alive but owned by someone else; treat as alive.
        pass

    qmp_sock = vm_dir / "qmp.sock"
    try:
        client = await qmp.connect(qmp_sock, timeout=1.0)
    except (TimeoutError, OSError):
        return "starting"

    try:
        if cfg.ssh_port is None:
            return "unreachable"
        if _ssh_banner_ok("127.0.0.1", cfg.ssh_port):
            return "running"
        return "unreachable"
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


def _ssh_banner_ok(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Open a TCP connection, read up to 16 bytes, return True if SSH banner."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            banner = s.recv(16)
            return banner.startswith(b"SSH-")
    except (OSError, TimeoutError):
        return False
