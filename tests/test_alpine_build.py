from pathlib import Path
from unittest.mock import patch

import pytest

from uqmm.builders.alpine import AlpineSeedBuilder
from uqmm.config import VMConfig


def test_build_creates_disk_and_answers(tmp_path: Path) -> None:
    iso = tmp_path / "alpine.iso"
    iso.write_bytes(b"fake-iso")
    vm_dir = tmp_path / "vms" / "al321"
    vm_dir.mkdir(parents=True)
    cfg = VMConfig(
        name="al321",
        os="alpine",
        version="3.21",
        ssh_port=22500,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
    )
    with (
        patch("uqmm.builders.alpine.resolve_image", return_value=iso),
        patch("uqmm.builders.alpine.build_disk") as mock_disk,
    ):
        artifacts = AlpineSeedBuilder().build(cfg, vm_dir)

    mock_disk.assert_called_once_with(vm_dir / "disk.qcow2", size_gb=20)
    assert (vm_dir / "answers").exists()
    assert "ssh-ed25519 AAA" in (vm_dir / "answers").read_text()
    assert artifacts.seed_paths == [vm_dir / "disk.qcow2", vm_dir / "answers"]


def test_install_args_have_cdrom_and_no_reboot(tmp_path: Path) -> None:
    iso = tmp_path / "alpine.iso"
    iso.write_bytes(b"")
    vm_dir = tmp_path / "vms" / "al321"
    vm_dir.mkdir(parents=True)
    cfg = VMConfig(
        name="al321",
        os="alpine",
        version="3.21",
        ssh_port=22500,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
    )
    with (
        patch("uqmm.builders.alpine.resolve_image", return_value=iso),
        patch("uqmm.builders.alpine.build_disk"),
    ):
        artifacts = AlpineSeedBuilder().build(cfg, vm_dir)

    install = artifacts.qemu_install_args
    runtime = artifacts.qemu_runtime_args
    assert "-cdrom" in install
    assert str(iso) in install
    assert "-no-reboot" in install
    # serial wait=on so the driver always connects before boot output starts.
    serial_arg = install[install.index("-serial") + 1]
    assert "wait=on" in serial_arg
    assert "reconnect-ms=1000" in serial_arg

    # Runtime sheds CD + no-reboot; serial still attached but wait=off.
    assert "-cdrom" not in runtime
    assert "-no-reboot" not in runtime
    runtime_serial = runtime[runtime.index("-serial") + 1]
    assert "wait=off" in runtime_serial


def test_alpine_bumps_resources_below_threshold(tmp_path: Path) -> None:
    iso = tmp_path / "alpine.iso"
    iso.write_bytes(b"")
    vm_dir = tmp_path / "vms" / "al321"
    vm_dir.mkdir(parents=True)
    cfg = VMConfig(
        name="al321",
        os="alpine",
        version="3.21",
        ssh_port=22500,
        vcpus=2,
        memory_mb=2048,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
    )
    with (
        patch("uqmm.builders.alpine.resolve_image", return_value=iso),
        patch("uqmm.builders.alpine.build_disk"),
    ):
        artifacts = AlpineSeedBuilder().build(cfg, vm_dir)

    install = artifacts.qemu_install_args
    runtime = artifacts.qemu_runtime_args
    # Install bumps to ≥4 / ≥4096 to keep TCG install time tractable.
    assert install[install.index("-smp") + 1] == "4"
    assert install[install.index("-m") + 1] == "4096"
    # Runtime honors the user's actual request — bump is install-only.
    assert runtime[runtime.index("-smp") + 1] == "2"
    assert runtime[runtime.index("-m") + 1] == "2048"


def test_alpine_keeps_higher_resources(tmp_path: Path) -> None:
    iso = tmp_path / "alpine.iso"
    iso.write_bytes(b"")
    vm_dir = tmp_path / "vms" / "al321"
    vm_dir.mkdir(parents=True)
    cfg = VMConfig(
        name="al321",
        os="alpine",
        version="3.21",
        ssh_port=22500,
        vcpus=8,
        memory_mb=8192,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
    )
    with (
        patch("uqmm.builders.alpine.resolve_image", return_value=iso),
        patch("uqmm.builders.alpine.build_disk"),
    ):
        artifacts = AlpineSeedBuilder().build(cfg, vm_dir)

    install = artifacts.qemu_install_args
    assert install[install.index("-smp") + 1] == "8"
    assert install[install.index("-m") + 1] == "8192"


def test_build_requires_ssh_port(tmp_path: Path) -> None:
    cfg = VMConfig(name="al321", os="alpine", version="3.21")
    vm_dir = tmp_path / "vms" / "al321"
    vm_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="ssh_port"):
        AlpineSeedBuilder().build(cfg, vm_dir)
