"""AlpineSeedBuilder — stock ISO + serial-pexpect path.

See docs/research/alpine-unattended.md for the answer-file schema and
docs/design/config.md § AlpineSeedBuilder for the build/runtime split.
"""

from __future__ import annotations

import subprocess
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
    """Create an empty qcow2 disk for setup-alpine to install into."""
    cmd = ["qemu-img", "create", "-f", "qcow2", str(disk), f"{size_gb}G"]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise RuntimeError(f"qemu-img create failed: {stderr.strip()}") from e


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


def _common_args(cfg: VMConfig, vm_dir: Path) -> list[str]:
    assert cfg.ssh_port is not None
    # Alpine install needs more than the default 2 vcpus / 2 GiB to finish
    # apk-add openssh + reboot in a reasonable time under TCG; bump the
    # minimum at *args generation* time so the user's stored config still
    # reflects what they asked for.
    smp = max(cfg.vcpus, 4)
    mem = max(cfg.memory_mb, 4096)
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35",
        "-cpu",
        "max",
        "-smp",
        str(smp),
        "-m",
        str(mem),
        "-nographic",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{cfg.ssh_port}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-qmp",
        f"unix:{vm_dir / 'qmp.sock'},server=on,wait=off",
    ]


def _qemu_install_args(cfg: VMConfig, vm_dir: Path, disk: Path, iso: Path) -> list[str]:
    return [
        *_common_args(cfg, vm_dir),
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
        *_common_args(cfg, vm_dir),
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=off",
    ]
