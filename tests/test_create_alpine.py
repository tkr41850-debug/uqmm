from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm.builders.base import InstallArtifacts
from uqmm.cli import main


def _key(tmp: Path) -> Path:
    p = tmp / "id.pub"
    p.write_text("ssh-ed25519 AAA test@host\n")
    return p


def _patches(art: InstallArtifacts):
    fake_proc_install = MagicMock(pid=4242)
    fake_proc_install.wait = AsyncMock(return_value=0)
    fake_proc_runtime = MagicMock(pid=4243)
    fake_proc_runtime.wait = AsyncMock(return_value=0)

    return (
        patch(
            "uqmm.cli.AlpineSeedBuilder",
            return_value=MagicMock(build=MagicMock(return_value=art)),
        ),
        patch(
            "uqmm.cli._launch_qemu",
            new=AsyncMock(side_effect=[fake_proc_install, fake_proc_runtime]),
        ),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch(
            "uqmm.cli.serve_answers_once",
            return_value=MagicMock(port=9999, stop=MagicMock()),
        ),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(return_value=None)),
    )


def test_create_alpine_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    art = InstallArtifacts(
        qemu_install_args=["qemu-system-x86_64", "-cdrom", "iso", "-no-reboot"],
        qemu_runtime_args=["qemu-system-x86_64"],
        seed_paths=[vm_dir_target / "disk.qcow2", vm_dir_target / "answers"],
    )

    p_builder, p_launch, p_serial, p_drive, p_serve, p_ssh = _patches(art)

    # answers file must exist before _create_alpine reads it; the real builder
    # would write it, but we've mocked the builder away.
    def write_answers(*_args: object, **_kw: object) -> InstallArtifacts:
        vm_dir_target.mkdir(parents=True, exist_ok=True)
        (vm_dir_target / "answers").write_text("KEYMAPOPTS=us\n")
        return art

    builder_mock = MagicMock()
    builder_mock.build = MagicMock(side_effect=write_answers)
    p_builder = patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock)

    with p_builder, p_launch, p_serial, p_drive, p_serve, p_ssh:
        rc = main(
            [
                "create",
                "al321",
                "--os",
                "alpine",
                "--version",
                "3.21",
                "--key",
                str(_key(tmp_path)),
            ]
        )

    assert rc == 0
    cfg_path = vm_dir_target / "config.json"
    assert cfg_path.exists()
    text = cfg_path.read_text()
    assert '"created"' in text
    assert '"alpine"' in text


def test_create_alpine_marks_failed_on_drive_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pexpect import TIMEOUT

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"

    def write_answers(*_args: object, **_kw: object) -> InstallArtifacts:
        vm_dir_target.mkdir(parents=True, exist_ok=True)
        (vm_dir_target / "answers").write_text("KEYMAPOPTS=us\n")
        return InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])

    fake_proc = MagicMock(pid=4242, returncode=None)
    fake_proc.terminate = MagicMock()
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    builder_mock = MagicMock()
    builder_mock.build = MagicMock(side_effect=write_answers)

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc)),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock(side_effect=TIMEOUT("login prompt"))),
        patch(
            "uqmm.cli.serve_answers_once",
            return_value=MagicMock(port=9999, stop=MagicMock()),
        ),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(return_value=None)),
        pytest.raises(TIMEOUT),
    ):
        main(
            [
                "create",
                "al321",
                "--os",
                "alpine",
                "--version",
                "3.21",
                "--key",
                str(_key(tmp_path)),
            ]
        )

    cfg_path = vm_dir_target / "config.json"
    assert cfg_path.exists()
    assert '"failed"' in cfg_path.read_text()
    fake_proc.terminate.assert_called_once()
