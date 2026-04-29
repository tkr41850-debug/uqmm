from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm.builders.base import InstallArtifacts
from uqmm.cli import main
from uqmm.config import VMConfig


def _make_vm(tmp_path: Path, **kw: object) -> Path:
    base: dict[str, object] = {
        "name": "vm1",
        "os": "debian",
        "version": "13",
        "ssh_port": 22500,
    }
    base.update(kw)
    cfg = VMConfig(**base)  # pyright: ignore[reportArgumentType]
    vm_dir = tmp_path / "data" / "uqmm" / "vms" / cfg.name
    vm_dir.mkdir(parents=True)
    cfg.save(vm_dir / "config.json")
    return vm_dir


# ---- status ----------------------------------------------------------------


def test_status_no_vms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no VMs" in out


def test_status_named_vm_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path)
    with patch("uqmm.cli.probe", new=AsyncMock(return_value="stopped")):
        rc = main(["status", "vm1"])
    assert rc == 0
    assert "stopped" in capsys.readouterr().out


def test_status_all_vms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path, name="vm1")
    _make_vm(tmp_path, name="vm2")
    with patch("uqmm.cli.probe", new=AsyncMock(return_value="running")):
        rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vm1" in out and "vm2" in out
    assert out.count("running") == 2


# ---- list ------------------------------------------------------------------


def test_list_renders_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path, name="vm1", os="debian", version="13", ssh_port=22500)
    with patch("uqmm.cli.probe", new=AsyncMock(return_value="running")):
        rc = main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vm1" in out
    assert "debian/13" in out
    assert "running" in out
    assert "22500" in out


# ---- start -----------------------------------------------------------------


def test_start_refuses_failed_vm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path, state="failed")
    rc = main(["start", "vm1"])
    assert rc != 0


def test_start_refuses_running_vm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path)
    with (
        patch("uqmm.cli.probe", new=AsyncMock(return_value="running")),
        patch("uqmm.cli.CloudImageBuilder"),
        patch("uqmm.cli._launch_qemu", new=AsyncMock()),
    ):
        rc = main(["start", "vm1"])
    assert rc != 0


def test_start_launches_qemu_without_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _make_vm(tmp_path)
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=["qemu-system-x86_64"])
    with (
        patch("uqmm.cli.probe", new=AsyncMock(return_value="stopped")),
        patch(
            "uqmm.cli.CloudImageBuilder",
            return_value=MagicMock(build=MagicMock(return_value=art)),
        ),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=MagicMock())) as launch,
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()) as wait_ssh,
    ):
        rc = main(["start", "vm1"])
    assert rc == 0
    launch.assert_awaited_once()
    wait_ssh.assert_not_called()


def test_start_with_wait_polls_ssh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _make_vm(tmp_path)
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=["qemu-system-x86_64"])
    with (
        patch("uqmm.cli.probe", new=AsyncMock(return_value="stopped")),
        patch(
            "uqmm.cli.CloudImageBuilder",
            return_value=MagicMock(build=MagicMock(return_value=art)),
        ),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()) as wait_ssh,
    ):
        rc = main(["start", "vm1", "--wait"])
    assert rc == 0
    wait_ssh.assert_awaited_once()


# ---- stop ------------------------------------------------------------------


def test_stop_idempotent_when_already_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path)
    with patch("uqmm.cli.probe", new=AsyncMock(return_value="stopped")):
        rc = main(["stop", "vm1"])
    assert rc == 0


def test_stop_graceful_sends_powerdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path)

    fake_client = MagicMock()
    fake_client.disconnect = AsyncMock()

    probe_results = iter(["running", "stopped"])

    async def fake_probe(_vm: Path) -> str:
        return next(probe_results)

    with (
        patch("uqmm.cli.probe", new=AsyncMock(side_effect=fake_probe)),
        patch("uqmm.cli.qmp.connect", new=AsyncMock(return_value=fake_client)),
        patch("uqmm.cli.qmp.system_powerdown", new=AsyncMock()) as powerdown,
        patch("uqmm.cli.qmp.quit", new=AsyncMock()) as quit_,
    ):
        rc = main(["stop", "vm1"])
    assert rc == 0
    powerdown.assert_awaited_once()
    quit_.assert_not_awaited()


def test_stop_force_sends_quit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    _make_vm(tmp_path)

    fake_client = MagicMock()
    fake_client.disconnect = AsyncMock()

    probe_results = iter(["running", "stopped"])

    async def fake_probe(_vm: Path) -> str:
        return next(probe_results)

    with (
        patch("uqmm.cli.probe", new=AsyncMock(side_effect=fake_probe)),
        patch("uqmm.cli.qmp.connect", new=AsyncMock(return_value=fake_client)),
        patch("uqmm.cli.qmp.system_powerdown", new=AsyncMock()) as powerdown,
        patch("uqmm.cli.qmp.quit", new=AsyncMock()) as quit_,
    ):
        rc = main(["stop", "vm1", "--force"])
    assert rc == 0
    powerdown.assert_not_awaited()
    quit_.assert_awaited_once()
