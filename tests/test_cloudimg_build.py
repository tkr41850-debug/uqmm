from pathlib import Path
from unittest.mock import patch

from uqmm.builders.cloudimg import CloudImageBuilder
from uqmm.config import VMConfig


def test_build_invokes_resolve_prepare_seed(tmp_path: Path) -> None:
    cfg = VMConfig(
        name="deb13",
        os="debian",
        version="13",
        ssh_port=22500,
        ssh_authorized_keys=["ssh-ed25519 AAA test@host"],
    )
    base_image = tmp_path / "cache" / "debian-13.qcow2"
    base_image.parent.mkdir(parents=True)
    base_image.write_bytes(b"fake-base")

    vm_dir = tmp_path / "vms" / "deb13"
    vm_dir.mkdir(parents=True)

    with (
        patch("uqmm.builders.cloudimg.resolve_image", return_value=base_image),
        patch("uqmm.builders.cloudimg.prepare_disk") as mock_prepare,
        patch("uqmm.builders.cloudimg.build_seed_iso") as mock_seed,
    ):
        artifacts = CloudImageBuilder().build(cfg, vm_dir)

    mock_prepare.assert_called_once_with(base_image, vm_dir / "disk.qcow2", size_gb=20)
    mock_seed.assert_called_once()
    seed_args = mock_seed.call_args
    user_data, meta_data, seed_path = (
        seed_args.args[0],
        seed_args.args[1],
        seed_args.args[2],
    )
    assert "#cloud-config" in user_data
    assert "instance-id" in meta_data
    assert seed_path == vm_dir / "seed.iso"

    # Cloud-image: install args == runtime args (no separate install boot).
    assert artifacts.qemu_install_args == artifacts.qemu_runtime_args
    assert artifacts.seed_paths == [vm_dir / "disk.qcow2", vm_dir / "seed.iso"]


def test_build_qemu_args_contain_required_pieces(tmp_path: Path) -> None:
    cfg = VMConfig(
        name="vm1",
        os="debian",
        version="13",
        ssh_port=22500,
        memory_mb=2048,
        vcpus=2,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
    )
    vm_dir = tmp_path / "vms" / "vm1"
    vm_dir.mkdir(parents=True)
    base_image = tmp_path / "base.qcow2"
    base_image.write_bytes(b"")

    with (
        patch("uqmm.builders.cloudimg.resolve_image", return_value=base_image),
        patch("uqmm.builders.cloudimg.prepare_disk"),
        patch("uqmm.builders.cloudimg.build_seed_iso"),
    ):
        artifacts = CloudImageBuilder().build(cfg, vm_dir)

    args = " ".join(artifacts.qemu_install_args)
    assert "qemu-system-x86_64" in artifacts.qemu_install_args[0]
    assert "-nographic" in artifacts.qemu_install_args
    # -no-reboot: a guest-triggered reboot during create indicates failure;
    # exit the QEMU process so the caller sees it instead of looping.
    assert "-no-reboot" in artifacts.qemu_install_args
    assert "-cpu" in artifacts.qemu_install_args
    # virtio disks: cloud image base + cidata seed
    assert f"file={vm_dir / 'disk.qcow2'},if=virtio" in args
    assert f"file={vm_dir / 'seed.iso'},if=virtio,format=raw,readonly=on" in args
    # SLiRP usermode net + hostfwd
    assert "user,id=net0,hostfwd=tcp:127.0.0.1:22500-:22" in args
    assert "virtio-net-pci,netdev=net0" in args
    # control sockets
    assert f"unix:{vm_dir / 'qmp.sock'},server=on,wait=off" in args
    assert f"unix:{vm_dir / 'serial.sock'},server=on,wait=off" in args
    # resources
    assert "2048" in artigs_str(artifacts.qemu_install_args, "-m")
    assert "2" in artigs_str(artifacts.qemu_install_args, "-smp")


def artigs_str(args: list[str], flag: str) -> str:
    """Return the arg immediately following `flag`, or empty string."""
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return ""


def test_build_requires_ssh_port(tmp_path: Path) -> None:
    cfg = VMConfig(name="vm1", os="debian", version="13")  # no ssh_port
    vm_dir = tmp_path / "vms" / "vm1"
    vm_dir.mkdir(parents=True)
    import pytest

    with pytest.raises(ValueError, match="ssh_port"):
        CloudImageBuilder().build(cfg, vm_dir)
