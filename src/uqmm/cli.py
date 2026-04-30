"""uqmm command-line entry point.

See docs/design/cli.md for the command contract.
"""

# pexpect ships no type stubs; the _Spawn protocol in alpine_drive captures
# the surface we use, but at the call site basedpyright sees pexpect.SocketSpawn
# as Unknown. Suppress at module level rather than per-call.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportArgumentType=false

from __future__ import annotations

import asyncio
import contextlib
import os as _os
import shutil
import sys
from asyncio.subprocess import Process
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from rich.console import Console
from rich.table import Table

from uqmm import state
from uqmm.alpine_drive import drive_install
from uqmm.builders.alpine import AlpineSeedBuilder
from uqmm.builders.base import InstallArtifacts
from uqmm.builders.cloudimg import CloudImageBuilder
from uqmm.config import VMConfig
from uqmm.discover import probe
from uqmm.qemu import process as qemu_process
from uqmm.qemu import qmp
from uqmm.qemu.serial import open_serial
from uqmm.serve import serve_answers_once
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

    try:
        state.validate_vm_name(name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    keys, key_err = _load_keys(key)
    if key_err:
        print(key_err, file=sys.stderr)
        return 2
    if not keys:
        print(
            "no SSH key supplied — pass --key or generate ~/.ssh/id_ed25519.pub",
            file=sys.stderr,
        )
        return 2

    try:
        VMConfig(
            name=name,
            os=os,  # pyright: ignore[reportArgumentType]
            version=version,
            vcpus=vcpus,
            memory_mb=memory_mb,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if ssh_port is not None:
        if ssh_port < 1024 or ssh_port > 65535:
            print(f"--ssh-port {ssh_port} must be in 1024-65535", file=sys.stderr)
            return 2
        if not state.is_port_bindable(ssh_port):
            print(f"port {ssh_port} is unavailable (in use or privileged)", file=sys.stderr)
            return 2

    vm_dir = state.vm_dir(name)

    try:
        resolved_port = (
            ssh_port if ssh_port is not None else state.pick_ssh_port(state.read_occupied_ports())
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    if vm_dir.exists():
        return _handle_existing_vm_dir(
            vm_dir,
            name,
            os,
            version,
            image,
            vcpus,
            memory_mb,
            disk_size_gb,
            resolved_port,
            user,
            keys,
            hostname,
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
        state="creating",
    )

    vm_dir.mkdir(parents=True)
    with state.acquire_create_lock(vm_dir):
        cfg.save(vm_dir / "config.json")
        try:
            if os == "alpine":
                rc = asyncio.run(_create_alpine(cfg, vm_dir))
            else:
                rc = asyncio.run(_create_cloudimg(cfg, vm_dir))
        except Exception:
            # Ensure config.json is marked failed even for uncaught exceptions.
            cfg_path = vm_dir / "config.json"
            if cfg_path.exists():
                try:
                    saved_cfg = VMConfig.load(cfg_path)
                    if saved_cfg.state == "creating":
                        cfg.state = "failed"
                        cfg.save(cfg_path)
                except Exception:
                    pass
            raise
    return rc


def _handle_existing_vm_dir(
    vm_dir: Path,
    name: str,
    os: str,
    version: str,
    image: str | None,
    vcpus: int,
    memory_mb: int,
    disk_size_gb: int,
    ssh_port: int,
    user: str,
    keys: list[str],
    hostname: str | None,
) -> int:
    """Handle create when vm_dir already exists (R1/R5/R14 state machine)."""
    try:
        saved = VMConfig.load(vm_dir / "config.json")
    except (ValueError, OSError):
        print(f"VM directory exists but config.json is corrupt: {vm_dir}", file=sys.stderr)
        return 1

    new_cfg = VMConfig(
        name=name,
        os=os,  # pyright: ignore[reportArgumentType]
        version=version,
        image=image,
        vcpus=vcpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=ssh_port,
        user=user,
        ssh_authorized_keys=keys,
        hostname=hostname,
        state="creating",
    )

    if saved.state == "created":
        if _configs_match(saved, new_cfg):
            print(
                f"{name} already created; run `uqmm start --wait {name}` to boot", file=sys.stdout
            )
            return 0
        _print_config_diff(saved, new_cfg)
        print(f"use `uqmm delete {name} && uqmm create {name} ...` to reconfigure", file=sys.stderr)
        return 1

    # failed or creating — try to resume (lock disambiguates concurrent vs stale)
    try:
        with state.acquire_create_lock(vm_dir):
            disk_fields = ("os", "version", "image", "disk_size_gb")
            for f in disk_fields:
                if getattr(saved, f) != getattr(new_cfg, f):
                    _print_config_diff(saved, new_cfg)
                    print(
                        f"disk-affecting fields differ; use `uqmm delete {name}` first",
                        file=sys.stderr,
                    )
                    return 1
            # Regenerate seed from current args; reuse disk if present
            new_cfg.state = "creating"
            new_cfg.save(vm_dir / "config.json")
            if os == "alpine":
                return asyncio.run(_resume_alpine(new_cfg, vm_dir, saved))
            return asyncio.run(_create_cloudimg(new_cfg, vm_dir))
    except state.CreateInProgressError:
        print(f"create already in progress for {name}", file=sys.stderr)
        return 1


def _configs_match(a: VMConfig, b: VMConfig) -> bool:
    fields = (
        "os",
        "version",
        "image",
        "vcpus",
        "memory_mb",
        "disk_size_gb",
        "ssh_port",
        "user",
        "hostname",
    )
    for f in fields:
        if getattr(a, f) != getattr(b, f):
            return False
    return sorted(a.ssh_authorized_keys) == sorted(b.ssh_authorized_keys)


def _print_config_diff(saved: VMConfig, new: VMConfig) -> None:
    fields = (
        "os",
        "version",
        "image",
        "vcpus",
        "memory_mb",
        "disk_size_gb",
        "ssh_port",
        "user",
        "hostname",
        "ssh_authorized_keys",
    )
    for f in fields:
        av, bv = getattr(saved, f), getattr(new, f)
        if av != bv:
            print(f"  {f}: {av!r} → {bv!r}", file=sys.stderr)


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
        await _wait_ssh_or_exit(proc, "127.0.0.1", cfg.ssh_port)
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
    cfg.state = "created"
    cfg.save(vm_dir / "config.json")
    print(f"{cfg.name} ready: ssh -p {cfg.ssh_port} {cfg.user}@127.0.0.1")
    return 0


async def _run_alpine_install(cfg: VMConfig, vm_dir: Path, artifacts: InstallArtifacts) -> None:
    """Drive the pexpect install phase. Touches state.installed on clean exit."""
    answers_text = (vm_dir / "answers").read_text()
    answers = serve_answers_once(answers_text)
    proc: Process | None = None
    try:
        proc = await _launch_qemu(
            artifacts.qemu_install_args,
            pidfile=vm_dir / "qemu.pid",
            stderr_log=vm_dir / "install.log",
        )
        spawn = await open_serial(vm_dir / "serial.sock", vm_dir / "install.log")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            drive_install,
            spawn,
            f"http://10.0.2.2:{answers.port}/answers",
        )
        # Installer typed `reboot`; -no-reboot makes QEMU exit on guest reboot.
        _ = await proc.wait()
        proc = None  # already exited — don't reap below
        (vm_dir / "qemu.pid").unlink(missing_ok=True)
        (vm_dir / "state.installed").touch()
    except BaseException:
        if proc is not None:
            await _kill_proc(proc)
        (vm_dir / "qemu.pid").unlink(missing_ok=True)
        raise
    finally:
        # Stop the answers server whether install succeeded or not.
        answers.stop()


async def _run_alpine_runtime(cfg: VMConfig, vm_dir: Path) -> None:
    """Launch runtime QEMU (no CD, no -no-reboot) and wait for SSH. Raises on failure."""
    proc: Process | None = None
    try:
        runtime_args = AlpineSeedBuilder().runtime_args(cfg, vm_dir)
        proc = await _launch_qemu(
            runtime_args,
            pidfile=vm_dir / "qemu.pid",
            stderr_log=vm_dir / "install.log",
        )
        assert cfg.ssh_port is not None
        await _wait_ssh_or_exit(proc, "127.0.0.1", cfg.ssh_port)
    except BaseException:
        if proc is not None:
            await _kill_proc(proc)
        (vm_dir / "qemu.pid").unlink(missing_ok=True)
        raise


def _seed_fields_differ(saved: VMConfig, new: VMConfig) -> bool:
    """True when setup-alpine-consumed fields changed (user, hostname, or keys)."""
    if saved.user != new.user or saved.hostname != new.hostname:
        return True
    return sorted(saved.ssh_authorized_keys) != sorted(new.ssh_authorized_keys)


async def _create_alpine(cfg: VMConfig, vm_dir: Path) -> int:
    """Drive the alpine branch: ISO install over serial, then runtime relaunch."""
    try:
        artifacts = AlpineSeedBuilder().build(cfg, vm_dir)
        await _run_alpine_install(cfg, vm_dir, artifacts)
        await _run_alpine_runtime(cfg, vm_dir)
    except BaseException:
        cfg.state = "failed"
        cfg.save(vm_dir / "config.json")
        raise
    cfg.state = "created"
    cfg.save(vm_dir / "config.json")
    (vm_dir / "state.seeded").unlink(missing_ok=True)
    (vm_dir / "state.installed").unlink(missing_ok=True)
    print(f"{cfg.name} ready: ssh -p {cfg.ssh_port} {cfg.user}@127.0.0.1")
    return 0


async def _resume_alpine(cfg: VMConfig, vm_dir: Path, saved: VMConfig) -> int:
    """Resume Alpine create from a failed/creating state using checkpoint markers."""
    installed_marker = vm_dir / "state.installed"
    seeded_marker = vm_dir / "state.seeded"
    has_installed = installed_marker.exists()
    has_seeded = seeded_marker.exists()

    if has_installed and not (vm_dir / "disk.qcow2").exists():
        print(
            f"state.installed is set but disk.qcow2 is missing; "
            f"use `uqmm delete {cfg.name} && uqmm create {cfg.name} ...` to start fresh",
            file=sys.stderr,
        )
        cfg.state = "failed"
        cfg.save(vm_dir / "config.json")
        return 1

    if has_installed and _seed_fields_differ(saved, cfg):
        _print_config_diff(saved, cfg)
        print(
            "setup-alpine has already run; SSH keys/user/hostname are baked into the disk.\n"
            f"To apply new credentials, use `uqmm delete {cfg.name} && uqmm create {cfg.name} ...`",
            file=sys.stderr,
        )
        return 1

    try:
        if not has_installed:
            if has_seeded:
                artifacts = AlpineSeedBuilder().rebuild_seed(cfg, vm_dir)
            else:
                artifacts = AlpineSeedBuilder().build(cfg, vm_dir)
            await _run_alpine_install(cfg, vm_dir, artifacts)
        await _run_alpine_runtime(cfg, vm_dir)
    except BaseException:
        cfg.state = "failed"
        cfg.save(vm_dir / "config.json")
        raise
    cfg.state = "created"
    cfg.save(vm_dir / "config.json")
    (vm_dir / "state.seeded").unlink(missing_ok=True)
    (vm_dir / "state.installed").unlink(missing_ok=True)
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


async def _wait_ssh_or_exit(proc: Process, host: str, port: int) -> None:
    """Race SSH readiness against process exit. Indirection for test patching."""
    ssh_task: asyncio.Task[None] = asyncio.create_task(_wait_ssh_ready(host, port))
    proc_task: asyncio.Task[int] = asyncio.create_task(proc.wait())
    done, pending = await asyncio.wait({ssh_task, proc_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    if proc_task in done and ssh_task not in done:
        code = proc_task.result()
        raise RuntimeError(f"qemu exited with code {code} before SSH became ready; see install.log")
    if ssh_task in done:
        ssh_task.result()


_DEFAULT_KEY_NAMES = ("id_ed25519.pub", "id_rsa.pub")


def _load_keys(key_paths: list[Path] | None) -> tuple[list[str], str]:
    """Read each --key file's contents (one or more keys per file).

    When `key_paths` is None or empty, fall back to ~/.ssh/id_ed25519.pub then
    ~/.ssh/id_rsa.pub. Returns (keys, error_message). error_message is empty on success.
    """
    paths = list(key_paths) if key_paths else _discover_default_keys()
    out: list[str] = []
    bad: list[str] = []
    empty: list[str] = []
    for p in paths:
        try:
            text = p.read_text()
        except OSError:
            bad.append(str(p))
            continue
        lines = [
            ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")
        ]
        if not lines:
            empty.append(str(p))
            continue
        out.extend(lines)
    if bad:
        return [], f"key file(s) not found: {', '.join(bad)}"
    if empty:
        return [], f"key file(s) are empty: {', '.join(empty)}"
    return out, ""


def _discover_default_keys() -> list[Path]:
    ssh_dir = Path.home() / ".ssh"
    for name in _DEFAULT_KEY_NAMES:
        candidate = ssh_dir / name
        if candidate.exists():
            return [candidate]
    return []


@app.command
def start(name: str, *, wait: bool = False) -> int:
    """Boot an existing VM. --wait blocks until SSH responds."""
    vm_dir = state.vm_dir(name)
    if not (vm_dir / "config.json").exists():
        print(f"no such VM: {name}", file=sys.stderr)
        return 1
    cfg = VMConfig.load(vm_dir / "config.json")
    if cfg.state == "failed":
        print(f"{name} is in failed state; delete and create again", file=sys.stderr)
        return 1
    return asyncio.run(_start(cfg, vm_dir, wait_ssh=wait))


async def _start(cfg: VMConfig, vm_dir: Path, *, wait_ssh: bool) -> int:
    status = await probe(vm_dir)
    if status in ("starting", "running"):
        print(f"{cfg.name} is already {status}", file=sys.stderr)
        return 1
    if status == "unreachable":
        print(
            f"{cfg.name} is unreachable (process alive, SSH not responding); "
            f"try: uqmm stop {cfg.name} --force && uqmm start {cfg.name}",
            file=sys.stderr,
        )
        return 3

    # IMPORTANT: do NOT call builder.build() here — that would rerun
    # prepare_disk / build_disk and clobber the installed qcow2. Use
    # runtime_args() which reconstructs args from existing on-disk artifacts.
    builder = AlpineSeedBuilder() if cfg.os == "alpine" else CloudImageBuilder()
    try:
        runtime_args = builder.runtime_args(cfg, vm_dir)
    except FileNotFoundError as e:
        print(f"missing artifact: {e}", file=sys.stderr)
        return 1

    proc = await _launch_qemu(
        runtime_args,
        pidfile=vm_dir / "qemu.pid",
        stderr_log=vm_dir / "install.log",
    )
    if wait_ssh:
        try:
            assert cfg.ssh_port is not None
            await _wait_ssh_or_exit(proc, "127.0.0.1", cfg.ssh_port)
        except BaseException:
            await _kill_proc(proc)
            (vm_dir / "qemu.pid").unlink(missing_ok=True)
            raise
    print(f"{cfg.name} started" + (" (ssh ready)" if wait_ssh else ""))
    return 0


@app.command
def stop(name: str, *, force: bool = False) -> int:
    """Graceful QMP system_powerdown; --force escalates to QMP quit."""
    vm_dir = state.vm_dir(name)
    if not (vm_dir / "config.json").exists():
        print(f"no such VM: {name}", file=sys.stderr)
        return 1
    return asyncio.run(_stop(vm_dir, force=force))


async def _stop(vm_dir: Path, *, force: bool) -> int:
    status = await probe(vm_dir)
    if status in ("not-created", "stopped", "failed"):
        # Idempotent — already stopped is a success per Docker semantics.
        return 0

    qmp_sock = vm_dir / "qmp.sock"
    try:
        client = await qmp.connect(qmp_sock, timeout=5.0)
    except (TimeoutError, OSError):
        # QMP unreachable — fall back to SIGTERM/SIGKILL via pidfile.
        return await _stop_via_pidfile(vm_dir)

    try:
        if force:
            await qmp.quit(client)
        else:
            await qmp.system_powerdown(client)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()

    # Wait for the QEMU process to actually exit (pid disappears).
    deadline = asyncio.get_event_loop().time() + (5.0 if force else 30.0)
    while asyncio.get_event_loop().time() < deadline:
        if (await probe(vm_dir)) == "stopped":
            (vm_dir / "qemu.pid").unlink(missing_ok=True)
            return 0
        await asyncio.sleep(0.5)
    # Graceful timed out — escalate.
    if not force:
        return await _stop(vm_dir, force=True)
    # Force timed out (very rare) — fall back to OS-level kill.
    return await _stop_via_pidfile(vm_dir)


async def _stop_via_pidfile(vm_dir: Path) -> int:
    pidfile = vm_dir / "qemu.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)
        return 0
    import signal

    with contextlib.suppress(ProcessLookupError):
        _os.kill(pid, signal.SIGKILL)
    pidfile.unlink(missing_ok=True)
    return 0


@app.command
def delete(name: str) -> int:
    """Stop if running, then remove the VM directory."""
    vm_dir = state.vm_dir(name)
    if not vm_dir.exists():
        print(f"no such VM: {name}", file=sys.stderr)
        return 1
    # Stop is idempotent — safe to call even if already stopped. _stop
    # always returns 0 (escalating to SIGKILL as last resort), so we
    # don't need to check it.
    _ = asyncio.run(_stop(vm_dir, force=False))
    shutil.rmtree(vm_dir)
    print(f"{name} deleted")
    return 0


@app.command
def status(name: str | None = None) -> int:
    """Per-VM state. Without <name>, shows all."""
    if name is not None:
        vm_dir = state.vm_dir(name)
        result = asyncio.run(probe(vm_dir))
        print(result)
        return 0
    vms = list(state.iter_vm_dirs())
    if not vms:
        print("no VMs")
        return 0
    for d in vms:
        result = asyncio.run(probe(d))
        print(f"{d.name}\t{result}")
    return 0


@app.command(name="list")
def list_cmd() -> int:
    """Tabular listing of all VMs."""
    vms = list(state.iter_vm_dirs())
    if not vms:
        print("no VMs")
        return 0
    table = Table()
    table.add_column("name")
    table.add_column("os/version")
    table.add_column("status")
    table.add_column("ssh-port")
    for d in vms:
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            table.add_row(d.name, "?", "?", "?")
            continue
        try:
            cfg = VMConfig.load(cfg_path)
        except (ValueError, OSError):
            table.add_row(d.name, "?", "invalid-config", "?")
            continue
        result = asyncio.run(probe(d))
        port = str(cfg.ssh_port) if cfg.ssh_port is not None else "-"
        table.add_row(d.name, f"{cfg.os}/{cfg.version}", result, port)
    Console().print(table)
    return 0


@app.command
def ssh(
    name: str,
    *args: Annotated[str, Parameter(allow_leading_hyphen=True)],
) -> int:
    """Resolve port + exec system ssh with passthrough args."""
    vm_dir = state.vm_dir(name)
    if not (vm_dir / "config.json").exists():
        print(f"no such VM: {name}", file=sys.stderr)
        return 1
    cfg = VMConfig.load(vm_dir / "config.json")
    if cfg.ssh_port is None:
        print(f"{name} has no SSH port allocated", file=sys.stderr)
        return 1
    status = asyncio.run(probe(vm_dir))
    if status not in ("running", "unreachable"):
        # `unreachable` still gets the exec — user might be debugging.
        print(f"{name} is {status}; start it first", file=sys.stderr)
        return 1
    argv = [
        "ssh",
        "-p",
        str(cfg.ssh_port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{cfg.user}@127.0.0.1",
        *args,
    ]
    # os.execvp replaces this process with ssh — the caller's TTY is
    # connected directly, signals/resize/Ctrl-C all behave like running ssh
    # by hand. Returns 1 only if exec itself fails (e.g. ssh binary missing).
    try:
        _os.execvp("ssh", argv)
    except FileNotFoundError:
        print("ssh binary not found in PATH", file=sys.stderr)
        return 1
    return 0  # unreachable on success — execvp doesn't return


@app.command
def log(name: str, *, follow: bool = False) -> int:
    """Print captured serial log; --follow tails."""
    vm_dir = state.vm_dir(name)
    if not (vm_dir / "config.json").exists():
        print(f"no such VM: {name}", file=sys.stderr)
        return 1
    log_path = vm_dir / "install.log"
    if not log_path.exists():
        # Empty/no-log: not an error, just nothing to show.
        return 0
    with log_path.open("rb") as fh:
        if follow:
            return _follow_log(fh)
        sys.stdout.buffer.write(fh.read())
        return 0


def _follow_log(fh: object) -> int:
    """tail -f style: print appended bytes; exit on Ctrl-C."""
    import time

    try:
        while True:
            chunk = fh.read()  # pyright: ignore[reportAttributeAccessIssue]
            if chunk:
                _ = sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


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
