"""uqmm command-line entry point.

See docs/design/cli.md for the command contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
from asyncio.subprocess import Process
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from uqmm import state
from uqmm.builders.cloudimg import CloudImageBuilder
from uqmm.config import VMConfig
from uqmm.qemu import process as qemu_process
from uqmm.ssh import wait_ready

# version_flags=() disables cyclopts' built-in --version (which would otherwise
# eat `create --version <ver>` and short-circuit before the subcommand runs).
app = App(name="uqmm", help="Headless QEMU machine manager.", version_flags=())


@app.command
def create(
    name: str,
    *,
    os: Annotated[str, Parameter(help="alpine | debian | ubuntu")],
    version: Annotated[str, Parameter(help='OS version, e.g. "3.21", "13", "24.04"')],
    image: Annotated[str | None, Parameter(help="Local path or URL; default: canonical")] = None,
    vcpus: int = 2,
    memory_mb: int = 2048,
    disk_size_gb: int = 20,
    ssh_port: Annotated[
        int | None, Parameter(help="Port to forward to guest:22; default: auto 22000-23000")
    ] = None,
    user: str = "uqmm",
    key: Annotated[
        list[Path] | None, Parameter(help="Public-key path(s); repeat for multiple")
    ] = None,
    hostname: str | None = None,
) -> int:
    """Provision a VM and boot it until SSH-ready (Docker-style)."""
    if os not in ("alpine", "debian", "ubuntu"):
        print(f"unsupported os: {os}", file=sys.stderr)
        return 2

    keys = _load_keys(key)
    if not keys:
        print(
            "no SSH key supplied — pass --key or generate ~/.ssh/id_ed25519.pub",
            file=sys.stderr,
        )
        return 2

    vm_dir = state.vm_dir(name)
    if vm_dir.exists():
        print(f"VM directory already exists: {vm_dir}", file=sys.stderr)
        return 1

    resolved_port = (
        ssh_port if ssh_port is not None else state.pick_ssh_port(state.read_occupied_ports())
    )

    cfg = VMConfig(
        name=name,
        os=os,  # pyright: ignore[reportArgumentType] — narrowed above
        version=version,
        image=image,
        vcpus=vcpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=resolved_port,
        user=user,
        ssh_authorized_keys=keys,
        hostname=hostname,
    )

    if os == "alpine":
        raise NotImplementedError("alpine create: phase 3")

    vm_dir.mkdir(parents=True)
    return asyncio.run(_create_cloudimg(cfg, vm_dir))


async def _create_cloudimg(cfg: VMConfig, vm_dir: Path) -> int:
    """Drive the cloud-image branch: build, launch, wait SSH, persist."""
    proc: Process | None = None
    try:
        artifacts = CloudImageBuilder().build(cfg, vm_dir)
        proc = await _launch_qemu(
            artifacts.qemu_install_args,
            pidfile=vm_dir / "qemu.pid",
            stderr_log=vm_dir / "install.log",
        )
        assert cfg.ssh_port is not None  # guaranteed by allocator
        await _wait_ssh_ready("127.0.0.1", cfg.ssh_port)
    except BaseException:
        # Reap QEMU so a "failed" state doesn't leave a live qemu-system-* and
        # a stale qemu.pid pointing at it. SIGTERM first; if the process is
        # already gone the kill_proc helper is a no-op.
        if proc is not None:
            await _kill_proc(proc)
        (vm_dir / "qemu.pid").unlink(missing_ok=True)
        cfg.state = "failed"
        cfg.save(vm_dir / "config.json")
        raise
    cfg.save(vm_dir / "config.json")
    print(f"{cfg.name} ready: ssh -p {cfg.ssh_port} {cfg.user}@127.0.0.1")
    return 0


async def _kill_proc(proc: Process) -> None:
    """SIGTERM and wait briefly; escalate to SIGKILL if still alive."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        async with asyncio.timeout(5.0):
            _ = await proc.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        _ = await proc.wait()


async def _launch_qemu(args: list[str], pidfile: Path, stderr_log: Path) -> Process:
    """Indirection so tests can patch this without touching qemu.process."""
    return await qemu_process.launch(args, pidfile=pidfile, stderr_log=stderr_log)


async def _wait_ssh_ready(host: str, port: int) -> None:
    """Indirection so tests can patch this without touching ssh module."""
    await wait_ready(host, port)


_DEFAULT_KEY_NAMES = ("id_ed25519.pub", "id_rsa.pub")


def _load_keys(key_paths: list[Path] | None) -> list[str]:
    """Read each --key file's contents (one or more keys per file).

    When `key_paths` is None or empty, fall back to ~/.ssh/id_ed25519.pub then
    ~/.ssh/id_rsa.pub (per cli.md § SSH key resolution). Returns [] only if no
    key was supplied or discoverable.
    """
    paths = list(key_paths) if key_paths else _discover_default_keys()
    out: list[str] = []
    for p in paths:
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
    return out


def _discover_default_keys() -> list[Path]:
    ssh_dir = Path.home() / ".ssh"
    for name in _DEFAULT_KEY_NAMES:
        candidate = ssh_dir / name
        if candidate.exists():
            return [candidate]
    return []


@app.command
def start(name: str, *, wait: bool = False) -> None:
    """Boot an existing VM. --wait blocks until SSH responds."""
    del name, wait
    raise NotImplementedError("start: phase 4")


@app.command
def stop(name: str, *, force: bool = False) -> None:
    """Graceful QMP system_powerdown; --force escalates to QMP quit."""
    del name, force
    raise NotImplementedError("stop: phase 4")


@app.command
def delete(name: str) -> None:
    """Stop if running, then remove the VM directory."""
    del name
    raise NotImplementedError("delete: phase 4")


@app.command
def status(name: str | None = None) -> None:
    """Per-VM state. Without <name>, shows all."""
    vms = list(state.iter_vm_dirs())
    if name is not None:
        # Single-VM probe lands in phase 4; until then say "unknown".
        raise NotImplementedError("status <name>: phase 4")
    if not vms:
        print("no VMs")
        return
    for d in vms:
        print(d.name)


@app.command(name="list")
def list_cmd() -> None:
    """Tabular listing of all VMs."""
    vms = list(state.iter_vm_dirs())
    if not vms:
        print("no VMs")
        return
    for d in vms:
        print(d.name)


@app.command
def ssh(name: str, *args: str) -> None:
    """Resolve port + exec system ssh with passthrough args."""
    # Phase 4: confirm cyclopts handles `uqmm ssh vm1 -- -L 8080:...`
    # correctly. *args collects positionals; a leading `--` should make
    # cyclopts stop flag-parsing, but verify against the real openssh flag
    # surface and switch to a passthrough-specific cyclopts config if not.
    del name, args
    raise NotImplementedError("ssh: phase 4")


@app.command
def log(name: str, *, follow: bool = False) -> None:
    """Print captured serial log; --follow tails."""
    del name, follow
    raise NotImplementedError("log: phase 4")


def main(argv: list[str] | None = None) -> int:
    """Entry point. argv defaults to sys.argv[1:] when called via console-script."""
    args = argv if argv is not None else sys.argv[1:]
    # cyclopts default behavior: parse-errors are printed and sys.exit(1) is
    # called; --help calls sys.exit(0). We catch SystemExit so tests can drive
    # main() without the process actually exiting.
    try:
        result = app(args)
    except SystemExit as e:
        code = e.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    if isinstance(result, int):
        return result
    return 0


# `shutil` is imported for use in phase 4 (`delete`); keep it referenced so
# the import survives ruff's unused-import check until then.
_ = shutil
