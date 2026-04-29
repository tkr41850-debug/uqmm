"""CloudImageBuilder — Debian + Ubuntu, unified cloud-init NoCloud path.

See docs/design/config.md § CloudImageBuilder and docs/research/cloud-image.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pycdlib
import yaml

from uqmm.builders.base import InstallArtifacts
from uqmm.config import VMConfig
from uqmm.resolve import resolve_image


def render_user_data(cfg: VMConfig) -> str:
    """Render the cloud-init #cloud-config document for `cfg`.

    Disables password auth, skips package upgrade (slow under TCG), creates
    the configured user with the supplied SSH keys + passwordless sudo,
    enables qemu-guest-agent (preinstalled on Ubuntu, on Debian via runcmd —
    needed for clean QMP-driven shutdown later).
    """
    user_block: dict[str, Any] = {
        "name": cfg.user,
        "sudo": "ALL=(ALL) NOPASSWD:ALL",
        "shell": "/bin/bash",
        "ssh_authorized_keys": list(cfg.ssh_authorized_keys),
    }
    body: dict[str, Any] = {
        "hostname": cfg.effective_hostname(),
        "users": [user_block],
        "ssh_pwauth": False,
        "package_update": False,
        "package_upgrade": False,
        # `|| true`: package isn't preinstalled on Debian genericcloud;
        # don't fail the whole first-boot if apt can't reach a mirror.
        "runcmd": [
            ["sh", "-c", "systemctl enable --now qemu-guest-agent || true"],
        ],
    }
    return "#cloud-config\n" + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_meta_data(cfg: VMConfig) -> str:
    """Render the cloud-init NoCloud meta-data file.

    `instance-id` is derived from the VM name so cloud-init's per-instance
    state stays stable across reboots — change-of-instance triggers cloud-init
    to re-run first-boot logic, which we don't want.
    """
    body = {
        "instance-id": f"uqmm-{cfg.name}",
        "local-hostname": cfg.effective_hostname(),
    }
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def build_seed_iso(user_data: str, meta_data: str, out: Path) -> None:
    """Write a NoCloud cidata ISO containing user-data + meta-data to `out`.

    Volume identifier is lowercase `cidata` per the 2025 cloud-init
    deprecation of uppercase variants — see docs/design/toolchain.md
    gotcha #2.
    """
    # ISO9660 level 1 limits names to 8.3, so the lower layer uses 8.3
    # placeholders; cloud-init reads the Joliet (long-filename) layer, which
    # exposes the canonical `user-data` / `meta-data` names.
    iso = pycdlib.PyCdlib()
    iso.new(joliet=3, vol_ident="cidata")
    try:
        for name, iso_basename, content in (
            ("user-data", "USERDATA", user_data),
            ("meta-data", "METADATA", meta_data),
        ):
            data = content.encode("utf-8")
            iso.add_fp(
                _bytes_io(data),
                len(data),
                iso_path=f"/{iso_basename}.;1",
                joliet_path=f"/{name}",
            )
        iso.write(str(out))
    finally:
        iso.close()


def _bytes_io(data: bytes) -> Any:
    """Return a fresh BytesIO for `data` (pycdlib rewinds the stream itself)."""
    import io

    return io.BytesIO(data)


def prepare_disk(base: Path, out: Path, size_gb: int) -> None:
    """Create a qcow2 backed by `base`, then resize to `size_gb`.

    qcow2 backing-file references mean we don't copy the multi-hundred-MB
    cloud image — `out` is a thin overlay sized to `size_gb`. Cloud images
    auto-grow their root partition via cloud-initramfs-growroot on first
    boot, but only up to the qcow2 size, so the resize must happen *before*
    the first boot.
    """
    if not base.exists():
        raise FileNotFoundError(f"base image not found: {base}")
    create_cmd = [
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base),
        str(out),
    ]
    resize_cmd = ["qemu-img", "resize", str(out), f"{size_gb}G"]
    for cmd in (create_cmd, resize_cmd):
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {stderr.strip()}") from e


class CloudImageBuilder:
    """Builder for cloud-init-based images (Debian + Ubuntu)."""

    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts:
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before building (CLI allocates)")

        base = resolve_image(cfg)
        disk = vm_dir / "disk.qcow2"
        seed = vm_dir / "seed.iso"

        prepare_disk(base, disk, size_gb=cfg.disk_size_gb)
        build_seed_iso(render_user_data(cfg), render_meta_data(cfg), seed)

        args = _qemu_args(cfg, vm_dir, disk, seed)
        # Cloud image: no separate install phase. Same args drive the
        # cloud-init-on-first-boot run and every subsequent boot.
        return InstallArtifacts(
            qemu_install_args=args,
            qemu_runtime_args=args,
            seed_paths=[disk, seed],
        )

    def runtime_args(self, cfg: VMConfig, vm_dir: Path) -> list[str]:
        """Reconstruct runtime QEMU args from cfg + vm_dir without touching disk.

        Used by `uqmm start` — re-running build() would clobber the installed
        disk via prepare_disk(). The seed.iso and disk.qcow2 already exist on
        disk from the original create.
        """
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before runtime_args")
        return _qemu_args(cfg, vm_dir, vm_dir / "disk.qcow2", vm_dir / "seed.iso")


def _qemu_args(cfg: VMConfig, vm_dir: Path, disk: Path, seed: Path) -> list[str]:
    # -no-reboot: a guest reboot during create is almost always cloud-init
    # tripping over its own configuration. Letting QEMU exit on the reboot
    # turns that into a fast-fail (caller sees process exit; SSH-wait
    # surfaces a clear timeout) rather than a silent loop.
    assert cfg.ssh_port is not None
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35",
        "-cpu",
        "max",
        "-smp",
        str(cfg.vcpus),
        "-m",
        str(cfg.memory_mb),
        "-nographic",
        "-no-reboot",
        "-drive",
        f"file={disk},if=virtio",
        "-drive",
        f"file={seed},if=virtio,format=raw,readonly=on",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{cfg.ssh_port}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-qmp",
        f"unix:{vm_dir / 'qmp.sock'},server=on,wait=off",
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=off",
    ]
