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


# ── Phase 5 helpers ──────────────────────────────────────────────────────────


def _alpine_args(tmp: Path) -> list[str]:
    return ["create", "al321", "--os", "alpine", "--version", "3.21", "--key", str(_key(tmp))]


def _write_failed_cfg(vm_dir: Path, ssh_authorized_keys: list[str] | None = None) -> None:
    from uqmm.config import VMConfig

    cfg = VMConfig(
        name="al321",
        os="alpine",  # pyright: ignore[reportArgumentType]
        version="3.21",
        ssh_port=22500,
        ssh_authorized_keys=ssh_authorized_keys or ["ssh-ed25519 AAA test@host"],
        state="failed",
    )
    cfg.save(vm_dir / "config.json")


def _happy_builder_mock(vm_dir: Path, art: InstallArtifacts) -> object:
    """Builder mock: build() writes answers + returns art; runtime_args/rebuild_seed auto-mocked."""

    def write_answers(*_a: object, **_kw: object) -> InstallArtifacts:
        vm_dir.mkdir(parents=True, exist_ok=True)
        (vm_dir / "answers").write_text("KEYMAPOPTS=us\n")
        return art

    from unittest.mock import MagicMock

    m = MagicMock()
    m.build = MagicMock(side_effect=write_answers)
    m.rebuild_seed = MagicMock(side_effect=write_answers)
    m.runtime_args = MagicMock(return_value=["qemu-system-x86_64"])
    return m


# ── Step 1: installed marker ──────────────────────────────────────────────────


def test_R10_create_writes_installed_marker_after_install_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    art = InstallArtifacts(
        qemu_install_args=["qemu-system-x86_64"],
        qemu_runtime_args=["qemu-system-x86_64"],
    )

    fake_install = MagicMock(pid=1)
    fake_install.wait = AsyncMock(return_value=0)
    fake_runtime = MagicMock(pid=2)
    fake_runtime.wait = AsyncMock(return_value=0)

    installed_at_ssh_wait: list[bool] = []

    async def _check_marker(host: str, port: int) -> None:
        installed_at_ssh_wait.append((vm_dir_target / "state.installed").exists())

    with (
        patch(
            "uqmm.cli.AlpineSeedBuilder",
            return_value=_happy_builder_mock(vm_dir_target, art),
        ),
        patch(
            "uqmm.cli._launch_qemu",
            new=AsyncMock(side_effect=[fake_install, fake_runtime]),
        ),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(side_effect=_check_marker)),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    assert installed_at_ssh_wait == [True], "state.installed must exist before SSH wait"


def test_R10_markers_removed_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    art = InstallArtifacts(qemu_install_args=["qemu-system-x86_64"], qemu_runtime_args=[])

    fake_proc = MagicMock(pid=1)
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=_happy_builder_mock(vm_dir_target, art)),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc)),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    assert not (vm_dir_target / "state.seeded").exists()
    assert not (vm_dir_target / "state.installed").exists()


def test_R10_markers_kept_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state.installed is kept when SSH wait fails so the next retry can skip reinstall."""
    from pexpect import TIMEOUT

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])

    fake_install = MagicMock(pid=1, returncode=None)
    fake_install.terminate = MagicMock()
    fake_install.kill = MagicMock()
    fake_install.wait = AsyncMock(return_value=0)
    fake_runtime = MagicMock(pid=2, returncode=None)
    fake_runtime.terminate = MagicMock()
    fake_runtime.kill = MagicMock()
    fake_runtime.wait = AsyncMock(return_value=0)

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=_happy_builder_mock(vm_dir_target, art)),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(side_effect=[fake_install, fake_runtime])),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(side_effect=TIMEOUT("ssh"))),
        pytest.raises(TIMEOUT),
    ):
        main(_alpine_args(tmp_path))

    assert (vm_dir_target / "state.installed").exists(), "marker must survive failure for resume"
    assert '"failed"' in (vm_dir_target / "config.json").read_text()


# ── Step 2: resume from seeded ────────────────────────────────────────────────


def test_R10_resume_from_seeded_skips_build_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    vm_dir_target.mkdir(parents=True)
    (vm_dir_target / "disk.qcow2").write_bytes(b"existing-disk")
    (vm_dir_target / "state.seeded").touch()
    _write_failed_cfg(vm_dir_target)

    art = InstallArtifacts(qemu_install_args=["qemu-system-x86_64"], qemu_runtime_args=[])

    builder_mock = _happy_builder_mock(vm_dir_target, art)

    fake_proc = MagicMock(pid=1)
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc)),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    builder_mock.build.assert_not_called()  # type: ignore[union-attr]
    builder_mock.rebuild_seed.assert_called_once()  # type: ignore[union-attr]
    # disk must be untouched
    assert (vm_dir_target / "disk.qcow2").read_bytes() == b"existing-disk"


def test_R10_resume_full_rebuild_when_no_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    vm_dir_target.mkdir(parents=True)
    _write_failed_cfg(vm_dir_target)
    # no markers, no disk — full rebuild

    art = InstallArtifacts(qemu_install_args=["qemu-system-x86_64"], qemu_runtime_args=[])
    builder_mock = _happy_builder_mock(vm_dir_target, art)

    fake_proc = MagicMock(pid=1)
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(return_value=fake_proc)),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    builder_mock.build.assert_called_once()  # type: ignore[union-attr]
    builder_mock.rebuild_seed.assert_not_called()  # type: ignore[union-attr]


# ── Step 3: resume from installed ────────────────────────────────────────────


def test_R11_resume_from_installed_skips_install_qemu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    vm_dir_target.mkdir(parents=True)
    (vm_dir_target / "disk.qcow2").write_bytes(b"installed")
    (vm_dir_target / "state.installed").touch()
    _write_failed_cfg(vm_dir_target)

    launch_calls: list[object] = []
    fake_proc = MagicMock(pid=1)
    fake_proc.wait = AsyncMock(return_value=0)

    async def track_launch(args: object, **_kw: object) -> object:
        launch_calls.append(args)
        return fake_proc

    builder_mock = MagicMock()
    builder_mock.runtime_args = MagicMock(return_value=["qemu-system-x86_64"])

    mock_serve = MagicMock()

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(side_effect=track_launch)),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", mock_serve),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    assert len(launch_calls) == 1, "only runtime QEMU should launch"
    mock_serve.assert_not_called()
    builder_mock.build.assert_not_called()
    builder_mock.rebuild_seed.assert_not_called()


def test_R11_resume_from_installed_refuses_seed_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    vm_dir_target.mkdir(parents=True)
    (vm_dir_target / "disk.qcow2").write_bytes(b"installed")
    (vm_dir_target / "state.installed").touch()
    # saved config has different keys than what the new create will supply
    _write_failed_cfg(vm_dir_target, ssh_authorized_keys=["ssh-ed25519 OLD old@host"])

    with (
        patch("uqmm.cli.AlpineSeedBuilder"),
        patch("uqmm.cli._launch_qemu"),
        patch("uqmm.cli._wait_ssh_ready"),
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 1
    err = capsys.readouterr().err
    assert "baked" in err or "setup-alpine" in err


def test_R11_resume_from_installed_clean_error_if_disk_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    vm_dir_target.mkdir(parents=True)
    (vm_dir_target / "state.installed").touch()
    # disk intentionally absent
    _write_failed_cfg(vm_dir_target)

    rc = main(_alpine_args(tmp_path))

    assert rc == 1
    err = capsys.readouterr().err
    assert "disk.qcow2" in err or "missing" in err
    assert '"failed"' in (vm_dir_target / "config.json").read_text()


def test_R12_resume_from_installed_retries_ssh_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two create calls: first fails at SSH wait (state.installed kept); second succeeds."""
    from pexpect import TIMEOUT

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    vm_dir_target = tmp_path / "data" / "uqmm" / "vms" / "al321"
    art = InstallArtifacts(qemu_install_args=[], qemu_runtime_args=[])

    fake_install = MagicMock(pid=1, returncode=None)
    fake_install.terminate = MagicMock()
    fake_install.kill = MagicMock()
    fake_install.wait = AsyncMock(return_value=0)
    fake_runtime1 = MagicMock(pid=2, returncode=None)
    fake_runtime1.terminate = MagicMock()
    fake_runtime1.kill = MagicMock()
    fake_runtime1.wait = AsyncMock(return_value=0)

    # First run: install succeeds, SSH wait fails.
    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=_happy_builder_mock(vm_dir_target, art)),
        patch(
            "uqmm.cli._launch_qemu",
            new=AsyncMock(side_effect=[fake_install, fake_runtime1]),
        ),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once", return_value=MagicMock(port=9999, stop=MagicMock())),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock(side_effect=TIMEOUT("ssh"))),
        pytest.raises(TIMEOUT),
    ):
        main(_alpine_args(tmp_path))

    assert (vm_dir_target / "state.installed").exists()
    assert '"failed"' in (vm_dir_target / "config.json").read_text()
    # Simulate the disk that the real installer would have written.
    (vm_dir_target / "disk.qcow2").write_bytes(b"installed")

    # Second run: resumes from state.installed — no reinstall.
    second_launch_calls: list[object] = []
    fake_runtime2 = MagicMock(pid=3)
    fake_runtime2.wait = AsyncMock(return_value=0)

    async def track(args: object, **_kw: object) -> object:
        second_launch_calls.append(args)
        return fake_runtime2

    builder_mock2 = MagicMock()
    builder_mock2.runtime_args = MagicMock(return_value=["qemu-system-x86_64"])

    with (
        patch("uqmm.cli.AlpineSeedBuilder", return_value=builder_mock2),
        patch("uqmm.cli._launch_qemu", new=AsyncMock(side_effect=track)),
        patch("uqmm.cli._wait_ssh_ready", new=AsyncMock()),
        patch("uqmm.cli.open_serial", new=AsyncMock(return_value=MagicMock())),
        patch("uqmm.cli.drive_install", new=MagicMock()),
        patch("uqmm.cli.serve_answers_once") as mock_serve2,
    ):
        rc = main(_alpine_args(tmp_path))

    assert rc == 0
    assert len(second_launch_calls) == 1, "second run must skip install QEMU"
    mock_serve2.assert_not_called()
    assert '"created"' in (vm_dir_target / "config.json").read_text()
