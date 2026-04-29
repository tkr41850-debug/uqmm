"""On-disk state: XDG paths, SSH port allocation, and create locking.

See docs/design/cli.md § On-disk state for the directory layout.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import socket
from collections.abc import Generator, Iterator
from pathlib import Path

from uqmm.config import VMConfig


class CreateInProgressError(Exception):
    """Raised by acquire_create_lock when another process holds the lock."""


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


def validate_vm_name(name: str) -> None:
    """Raise ValueError if name is not a safe VM/hostname identifier."""
    import re

    if not name:
        raise ValueError("VM name must not be empty")
    if len(name) > 64:
        raise ValueError(f"VM name too long ({len(name)} > 64): {name!r}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(f"VM name {name!r} contains invalid characters (allowed: A-Za-z0-9._-)")
    if name[0] in (".", "-"):
        raise ValueError(f"VM name must not start with '.' or '-': {name!r}")


def is_port_bindable(port: int) -> bool:
    """Return True if port can be bound on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


@contextlib.contextmanager
def acquire_create_lock(vm_dir: Path) -> Generator[None]:
    """Hold an exclusive flock on `vm_dir/create.lock` for the duration.

    Raises CreateInProgressError immediately (non-blocking) if the lock is
    already held by another process. The lock is released when the context
    manager exits (including on exception or signal — flock state is
    per-process-fd and disappears when the fd is closed).
    """
    lockfile = vm_dir / "create.lock"
    lockfile.touch()
    fd = os.open(str(lockfile), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise CreateInProgressError(f"create already in progress for {vm_dir.name}") from None
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
