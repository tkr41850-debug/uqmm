"""Tests for per-VM create lockfile (R5)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm import state
from uqmm.builders.base import InstallArtifacts
from uqmm.cli import main
from uqmm.config import VMConfig
from uqmm.state import CreateInProgressError


def _key(tmp: Path) -> Path:
    p = tmp / "id.pub"
    p.write_text("ssh-ed25519 AAA test@host\n")
    return p


def _create_args(tmp: Path) -> list[str]:
    return [
        "create",
        "vm1",
        "--os",
        "debian",
        "--version",
        "13",
        "--key",
        str(_key(tmp)),
    ]


def test_R5_acquire_release_round_trip(tmp_path: Path) -> None:
    vm_dir = tmp_path / "vm"
    vm_dir.mkdir()
    with state.acquire_create_lock(vm_dir):
        pass  # acquired and released

    # Re-acquire succeeds after release.
    with state.acquire_create_lock(vm_dir):
        pass


def test_R5_concurrent_acquire_raises(tmp_path: Path) -> None:
    import subprocess
    import sys

    vm_dir = tmp_path / "vm"
    vm_dir.mkdir()

    # Subprocess acquires the flock and signals readiness via stdout, then
    # waits for us to signal done via stdin before releasing.
    script = (
        "import sys, fcntl, os; "
        "f = sys.argv[1] + '/create.lock'; "
        "open(f, 'w').close(); "
        "fd = os.open(f, os.O_RDWR); "
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); "
        "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
        "sys.stdin.readline(); "
        "os.close(fd)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(vm_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    assert proc.stdin is not None
    try:
        proc.stdout.readline()  # wait for subprocess to acquire
        with pytest.raises(CreateInProgressError), state.acquire_create_lock(vm_dir):
            pass
    finally:
        proc.stdin.write(b"done\n")
        proc.stdin.flush()
        proc.wait(timeout=5)


def test_R5_create_refuses_with_creating_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "vm1"
    vm_dir_target.mkdir(parents=True)
    cfg = VMConfig(
        name="vm1",
        os="debian",  # pyright: ignore[reportArgumentType]
        version="13",
        ssh_port=22500,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
        state="creating",
    )
    cfg.save(vm_dir_target / "config.json")

    with patch(
        "uqmm.cli.state.acquire_create_lock",
        side_effect=CreateInProgressError("create already in progress for vm1"),
    ):
        rc = main(_create_args(tmp_path))
    assert rc != 0
    err = capsys.readouterr().err
    assert "in progress" in err


def test_R5_create_resumes_creating_when_unlocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "vm1"
    vm_dir_target.mkdir(parents=True)
    cfg = VMConfig(
        name="vm1",
        os="debian",  # pyright: ignore[reportArgumentType]
        version="13",
        ssh_port=22500,
        ssh_authorized_keys=["ssh-ed25519 AAA"],
        state="creating",
    )
    cfg.save(vm_dir_target / "config.json")

    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])
    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch(
            "uqmm.cli.CloudImageBuilder", return_value=MagicMock(build=MagicMock(return_value=art))
        ),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc)),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
    ):
        # No lock held → stale creating → treated as resume (failed)
        rc = main(_create_args(tmp_path))
    assert rc == 0
    # Final state is created
    saved = VMConfig.load(vm_dir_target / "config.json")
    assert saved.state == "created"
