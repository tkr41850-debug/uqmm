from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm.config import VMConfig
from uqmm.discover import probe


@pytest.mark.asyncio
async def test_C9_probe_retries_partial_pidfile(tmp_path: Path) -> None:
    """When the pidfile is empty on first read, retry before unlinking."""
    vm_dir = _make_vm(tmp_path)
    pidfile = vm_dir / "qemu.pid"
    pidfile.write_text("")  # empty — simulates partial write

    read_count = {"n": 0}
    original_read_text = Path.read_text

    def patched_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "qemu.pid":
            read_count["n"] += 1
            if read_count["n"] == 1:
                return ""  # first read: partial/empty
            return "12345\n"  # second read: complete
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    fake_qmp = MagicMock()
    fake_qmp.disconnect = AsyncMock()

    with (
        patch.object(Path, "read_text", patched_read_text),
        patch("uqmm.discover.asyncio.sleep", new=AsyncMock()),
        patch("uqmm.discover.os.kill", return_value=None),
        patch("uqmm.discover.qmp.connect", new=AsyncMock(side_effect=TimeoutError)),
    ):
        result = await probe(vm_dir)

    assert result == "starting"
    assert pidfile.exists()  # not unlinked on retry success


@pytest.mark.asyncio
async def test_P10_probe_returns_invalid_config_on_corrupt_json(tmp_path: Path) -> None:
    vm_dir = tmp_path / "vm"
    vm_dir.mkdir()
    (vm_dir / "config.json").write_text("{ not valid json")
    assert await probe(vm_dir) == "invalid-config"


def _make_vm(tmp_path: Path, **cfg_kw: object) -> Path:
    vm_dir = tmp_path / "vm"
    vm_dir.mkdir(parents=True, exist_ok=True)
    base: dict[str, object] = {
        "name": "vm1",
        "os": "alpine",
        "version": "3.21",
        "ssh_port": 22500,
    }
    base.update(cfg_kw)
    cfg = VMConfig(**base)  # pyright: ignore[reportArgumentType]
    cfg.save(vm_dir / "config.json")
    return vm_dir


@pytest.mark.asyncio
async def test_probe_not_created(tmp_path: Path) -> None:
    assert await probe(tmp_path / "absent") == "not-created"


@pytest.mark.asyncio
async def test_probe_failed_state(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path, state="failed")
    assert await probe(vm_dir) == "failed"


@pytest.mark.asyncio
async def test_probe_stopped_no_pidfile(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path)
    assert await probe(vm_dir) == "stopped"


@pytest.mark.asyncio
async def test_probe_stopped_stale_pidfile_is_cleaned(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path)
    pidfile = vm_dir / "qemu.pid"
    pidfile.write_text("999999\n")  # almost certainly no such PID

    # Patch os.kill so the probe sees the PID as dead even on the off chance
    # 999999 is real on this host.
    with patch("uqmm.discover.os.kill", side_effect=ProcessLookupError):
        result = await probe(vm_dir)
    assert result == "stopped"
    assert not pidfile.exists()


@pytest.mark.asyncio
async def test_probe_starting_when_qmp_unreachable(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path)
    (vm_dir / "qemu.pid").write_text("12345\n")

    with (
        patch("uqmm.discover.os.kill", return_value=None),
        patch("uqmm.discover.qmp.connect", new=AsyncMock(side_effect=TimeoutError)),
    ):
        assert await probe(vm_dir) == "starting"


@pytest.mark.asyncio
async def test_probe_running_when_ssh_banner_present(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path)
    (vm_dir / "qemu.pid").write_text("12345\n")

    fake_qmp = MagicMock()
    fake_qmp.disconnect = AsyncMock()

    with (
        patch("uqmm.discover.os.kill", return_value=None),
        patch("uqmm.discover.qmp.connect", new=AsyncMock(return_value=fake_qmp)),
        patch("uqmm.discover._ssh_banner_ok", return_value=True),
    ):
        assert await probe(vm_dir) == "running"


@pytest.mark.asyncio
async def test_probe_unreachable_when_qmp_up_but_no_ssh(tmp_path: Path) -> None:
    vm_dir = _make_vm(tmp_path)
    (vm_dir / "qemu.pid").write_text("12345\n")

    fake_qmp = MagicMock()
    fake_qmp.disconnect = AsyncMock()

    with (
        patch("uqmm.discover.os.kill", return_value=None),
        patch("uqmm.discover.qmp.connect", new=AsyncMock(return_value=fake_qmp)),
        patch("uqmm.discover._ssh_banner_ok", return_value=False),
    ):
        assert await probe(vm_dir) == "unreachable"
