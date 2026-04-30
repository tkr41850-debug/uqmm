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

    Disables password auth, skips package upgrade (slow under TCG), enables
    qemu-guest-agent (preinstalled on Ubuntu, on Debian via runcmd — needed
    for clean QMP-driven shutdown later).

    For `cfg.user == "root"`: opts out of cloud-init's default `disable_root`
    (which prepends a `command="echo 'Please login as ...'"` to root's
    authorized_keys) and writes the keys to root directly. Distro default
    sshd ships `PermitRootLogin prohibit-password` so pubkey-only root SSH
    works without sshd_config edits. No sudo/shell entries — root has them
    implicitly. Supplying `users:` without `default` also skips creation of
    the distro default user (`debian`/`ubuntu`).

    For other users: creates the named account with passwordless sudo + bash
    shell, leaves `disable_root` at the cloud-init default (true).
    """
    body: dict[str, Any] = {
        "hostname": cfg.effective_hostname(),
        "ssh_pwauth": False,
        "package_update": False,
        "package_upgrade": False,
        # `|| true`: package isn't preinstalled on Debian genericcloud;
        # don't fail the whole first-boot if apt can't reach a mirror.
        "runcmd": [
            ["sh", "-c", "systemctl enable --now qemu-guest-agent || true"],
        ],
    }
    if cfg.user == "root":
        body["disable_root"] = False
        body["users"] = [
            {
                "name": "root",
                "ssh_authorized_keys": list(cfg.ssh_authorized_keys),
            }
        ]
    else:
        body["users"] = [
            {
                "name": cfg.user,
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "ssh_authorized_keys": list(cfg.ssh_authorized_keys),
            }
        ]
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

    Writes to a .tmp sidecar and renames on success so a failed resize
    leaves no partial overlay behind (R3).
    """
    import os

    if not base.exists():
        raise FileNotFoundError(f"base image not found: {base}")
    tmp = out.with_suffix(out.suffix + ".tmp")
    create_cmd = [
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base),
        str(tmp),
    ]
    resize_cmd = ["qemu-img", "resize", str(tmp), f"{size_gb}G"]
    try:
        for cmd in (create_cmd, resize_cmd):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
                raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {stderr.strip()}") from e
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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

        return InstallArtifacts(
            qemu_install_args=_qemu_install_args(cfg, vm_dir, disk, seed),
            qemu_runtime_args=_qemu_runtime_args(cfg, vm_dir, disk, seed),
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
        disk = vm_dir / "disk.qcow2"
        seed = vm_dir / "seed.iso"
        if not disk.exists():
            raise FileNotFoundError(f"missing runtime artifact: {disk}")
        if not seed.exists():
            raise FileNotFoundError(f"missing runtime artifact: {seed}")
        return _qemu_runtime_args(cfg, vm_dir, disk, seed)


def _qemu_install_args(cfg: VMConfig, vm_dir: Path, disk: Path, seed: Path) -> list[str]:
    # -no-reboot: a guest reboot during cloud-init first boot almost always
    # indicates a config error; QEMU exit makes it a fast-fail.
    assert cfg.ssh_port is not None
    return [
        *_qemu_base_args(cfg, vm_dir, disk, seed),
        "-no-reboot",
    ]


def _qemu_runtime_args(cfg: VMConfig, vm_dir: Path, disk: Path, seed: Path) -> list[str]:
    # No -no-reboot at runtime: `sudo reboot` should reboot the guest, not stop it.
    assert cfg.ssh_port is not None
    return _qemu_base_args(cfg, vm_dir, disk, seed)


def _qemu_base_args(cfg: VMConfig, vm_dir: Path, disk: Path, seed: Path) -> list[str]:
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
