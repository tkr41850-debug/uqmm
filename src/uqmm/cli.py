"""uqmm command-line entry point.

This is the phase-1 skeleton: cyclopts wires up the full flag surface so
`--help` is meaningful, but commands that need a builder/QEMU stack
raise NotImplementedError. They get filled in by phases 2-4.

See docs/design/cli.md for the command contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from uqmm import state

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
) -> None:
    """Provision a VM and boot it until SSH-ready (Docker-style)."""
    del name, os, version, image, vcpus, memory_mb, disk_size_gb, ssh_port, user, key, hostname
    raise NotImplementedError("create: phase 2/3")


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
