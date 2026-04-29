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
    return (
        patch(
            "uqmm.cli.CloudImageBuilder",
            return_value=MagicMock(build=MagicMock(return_value=art)),
        ),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=MagicMock(pid=4242))),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(return_value=None)),
    )


def test_create_cloudimg_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    art = InstallArtifacts(
        qemu_install_args=["qemu-system-x86_64", "-nographic"],
        qemu_runtime_args=["qemu-system-x86_64", "-nographic"],
    )
    p_builder, p_launch, p_ssh = _patches(art)
    with p_builder, p_launch, p_ssh:
        rc = main(
            [
                "create",
                "deb13",
                "--os",
                "debian",
                "--version",
                "13",
                "--key",
                str(_key(tmp_path)),
            ]
        )
    assert rc == 0
    cfg_path = tmp_path / "data" / "uqmm" / "vms" / "deb13" / "config.json"
    assert cfg_path.exists(), "config.json must be persisted on success"
    text = cfg_path.read_text()
    assert '"created"' in text
    assert "ssh-ed25519 AAA" in text


def test_create_alpine_still_unimplemented(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with pytest.raises(NotImplementedError):
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


def test_create_refuses_existing_vm_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    existing = tmp_path / "data" / "uqmm" / "vms" / "deb13"
    existing.mkdir(parents=True)
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])
    p_builder, p_launch, p_ssh = _patches(art)
    with p_builder, p_launch, p_ssh:
        rc = main(
            [
                "create",
                "deb13",
                "--os",
                "debian",
                "--version",
                "13",
                "--key",
                str(_key(tmp_path)),
            ]
        )
    assert rc != 0


def test_create_marks_failed_on_ssh_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])

    fake_proc = MagicMock(pid=4242, returncode=None)
    fake_proc.terminate = MagicMock()
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    builder_p = patch(
        "uqmm.cli.CloudImageBuilder",
        return_value=MagicMock(build=MagicMock(return_value=art)),
    )
    launch_p = patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc))
    ssh_p = patch(
        "uqmm.cli._wait_ssh_ready",
        new=AsyncMock(side_effect=TimeoutError("no banner")),
    )
    with builder_p, launch_p, ssh_p, pytest.raises(TimeoutError):
        main(
            [
                "create",
                "deb13",
                "--os",
                "debian",
                "--version",
                "13",
                "--key",
                str(_key(tmp_path)),
            ]
        )

    # Directory must be left in place so `uqmm log` works for diagnosis.
    cfg_path = tmp_path / "data" / "uqmm" / "vms" / "deb13" / "config.json"
    assert cfg_path.exists()
    assert '"failed"' in cfg_path.read_text()
    # QEMU must be reaped — no stale qemu.pid, terminate() was called.
    fake_proc.terminate.assert_called_once()
    pidfile = tmp_path / "data" / "uqmm" / "vms" / "deb13" / "qemu.pid"
    assert not pidfile.exists()


def test_create_allocates_ssh_port_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])
    p_builder, p_launch, p_ssh = _patches(art)
    with (
        p_builder,
        p_launch,
        p_ssh,
        patch("uqmm.cli.state.pick_ssh_port", return_value=22789) as picker,
    ):
        rc = main(
            [
                "create",
                "deb13",
                "--os",
                "debian",
                "--version",
                "13",
                "--key",
                str(_key(tmp_path)),
            ]
        )
    assert rc == 0
    picker.assert_called_once()
    cfg = (tmp_path / "data" / "uqmm" / "vms" / "deb13" / "config.json").read_text()
    assert '"ssh_port": 22789' in cfg
