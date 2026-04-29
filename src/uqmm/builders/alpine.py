"""AlpineSeedBuilder — stock ISO + serial-pexpect path.

See docs/research/alpine-unattended.md for the answer-file schema and
docs/design/config.md § AlpineSeedBuilder for the build/runtime split.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from uqmm.builders.base import InstallArtifacts
from uqmm.config import VMConfig
from uqmm.resolve import resolve_image


def render_answers(cfg: VMConfig) -> str:
    """Render a setup-alpine answer file.

    Trust `setup-alpine -c` output over the wiki when in doubt — the wiki has
    documented spelling inconsistencies. Field set chosen to match a 3.21
    canonical skeleton.
    """
    user_keys = "\n".join(cfg.ssh_authorized_keys)
    hostname = cfg.effective_hostname()
    return f"""\
KEYMAPOPTS="us us"
HOSTNAMEOPTS="-n {hostname}"
DEVDOPTS=mdev
INTERFACESOPTS="auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"
DNSOPTS=""
TIMEZONEOPTS="-z UTC"
PROXYOPTS="none"
APKREPOSOPTS="-1"
USEROPTS="-a -u -g 'wheel,audio,video,netdev' {cfg.user}"
USERSSHKEY="{user_keys}"
SSHDOPTS="-c openssh"
NTPOPTS="-c chrony"
DISKOPTS="-m sys -s 0 /dev/vda"
LBUOPTS="none"
APKCACHEOPTS="none"
"""


def build_disk(disk: Path, size_gb: int) -> None:
    """Create an empty qcow2 disk for setup-alpine to install into.

    Writes to a .tmp sidecar and renames on success so a failed create
    leaves no partial disk behind (R3).
    """
    import os

    tmp = disk.with_suffix(disk.suffix + ".tmp")
    cmd = ["qemu-img", "create", "-f", "qcow2", str(tmp), f"{size_gb}G"]
    try:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise RuntimeError(f"qemu-img create failed: {stderr.strip()}") from e
        os.replace(tmp, disk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class AlpineSeedBuilder:
    """Builder for Alpine via stock ISO + serial pexpect.

    Unlike CloudImageBuilder, install and runtime args differ: install boots
    from the ISO with -no-reboot so QEMU exits when setup-alpine triggers a
    reboot; runtime drops the CD and -no-reboot.
    """

    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts:
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before building")

        iso = resolve_image(cfg)
        disk = vm_dir / "disk.qcow2"
        answers = vm_dir / "answers"

        build_disk(disk, size_gb=cfg.disk_size_gb)
        answers.write_text(render_answers(cfg))

        install = _qemu_install_args(cfg, vm_dir, disk, iso)
        runtime = _qemu_runtime_args(cfg, vm_dir, disk)
        return InstallArtifacts(
            qemu_install_args=install,
            qemu_runtime_args=runtime,
            seed_paths=[disk, answers],
        )

    def runtime_args(self, cfg: VMConfig, vm_dir: Path) -> list[str]:
        """Reconstruct runtime QEMU args without rebuilding the disk.

        Used by `uqmm start` — calling build() again would `qemu-img create`
        a fresh blank disk and lose the installed system.
        """
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before runtime_args")
        disk = vm_dir / "disk.qcow2"
        if not disk.exists():
            raise FileNotFoundError(f"missing runtime artifact: {disk}")
        return _qemu_runtime_args(cfg, vm_dir, disk)


def _common_args(cfg: VMConfig, vm_dir: Path, *, smp: int, mem_mb: int) -> list[str]:
    assert cfg.ssh_port is not None
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35",
        "-cpu",
        "max",
        "-smp",
        str(smp),
        "-m",
        str(mem_mb),
        "-nographic",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{cfg.ssh_port}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-qmp",
        f"unix:{vm_dir / 'qmp.sock'},server=on,wait=off",
    ]


def _qemu_install_args(cfg: VMConfig, vm_dir: Path, disk: Path, iso: Path) -> list[str]:
    install_smp = max(cfg.vcpus, 4)
    install_mem = max(cfg.memory_mb, 4096)
    bumps: list[str] = []
    if cfg.vcpus < 4:
        bumps.append(f"vcpus {cfg.vcpus} → {install_smp}")
    if cfg.memory_mb < 4096:
        bumps.append(f"memory {cfg.memory_mb}MB → {install_mem}MB")
    if bumps:
        print(
            f"note: alpine install raised {', '.join(bumps)};"
            " runtime will use your requested values",
            file=sys.stderr,
        )
    return [
        *_common_args(cfg, vm_dir, smp=install_smp, mem_mb=install_mem),
        "-cdrom",
        str(iso),
        "-boot",
        "d",
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-no-reboot",
        # wait=on so QEMU blocks until the pexpect driver connects — no boot
        # output is missed. reconnect-ms keeps the chardev alive if the host
        # side drops mid-install.
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=on,reconnect-ms=1000",
    ]


def _qemu_runtime_args(cfg: VMConfig, vm_dir: Path, disk: Path) -> list[str]:
    return [
        *_common_args(cfg, vm_dir, smp=cfg.vcpus, mem_mb=cfg.memory_mb),
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=off",
    ]
