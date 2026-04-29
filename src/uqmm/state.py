"""On-disk state: XDG paths and SSH port allocation.

See docs/design/cli.md § On-disk state for the directory layout.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path

from uqmm.config import VMConfig


def data_root() -> Path:
    """`$XDG_DATA_HOME/uqmm`, defaulting to `~/.local/share/uqmm`."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "uqmm"


def cache_root() -> Path:
    """`$XDG_CACHE_HOME/uqmm`, defaulting to `~/.cache/uqmm`."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "uqmm"


def vm_dir(name: str) -> Path:
    return data_root() / "vms" / name


def image_cache_dir() -> Path:
    return cache_root() / "images"


def iter_vm_dirs() -> Iterator[Path]:
    """Yield every directory under `data_root()/vms/`."""
    vms = data_root() / "vms"
    if not vms.exists():
        return
    for entry in vms.iterdir():
        if entry.is_dir():
            yield entry


def read_occupied_ports() -> set[int]:
    """Collect `ssh_port` from every existing VM's config.json.

    Raises ValueError if any config.json is corrupt — a corrupt config
    makes the port look free, which can cause silent double-assignment.
    """
    ports: set[int] = set()
    for d in iter_vm_dirs():
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            continue
        try:
            cfg = VMConfig.load(cfg_path)
        except (ValueError, OSError) as e:
            raise ValueError(f"corrupt config for VM {d.name!r}: {cfg_path}") from e
        if cfg.ssh_port is not None:
            ports.add(cfg.ssh_port)
    return ports


def pick_ssh_port(occupied: set[int], lo: int = 22000, hi: int = 23000) -> int:
    """Find the first free port in `[lo, hi]` not in `occupied`.

    Tries to `bind()` each candidate to detect ports held by external
    processes, then immediately releases the test socket. Raises
    RuntimeError if no port is available.

    There is an inherent TOCTOU window between this check and QEMU's
    later `hostfwd` bind — another process can claim the port in the
    gap. SLiRP doesn't support fd-passing so this is unavoidable. Worst
    case: QEMU launch fails with `bind: Address already in use` and the
    user retries.
    """
    for port in range(lo, hi + 1):
        if port in occupied:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free SSH port in {lo}-{hi}")
