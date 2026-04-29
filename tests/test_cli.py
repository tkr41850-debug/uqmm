from pathlib import Path
from unittest.mock import patch

import pytest

from uqmm.cli import main
from uqmm.config import VMConfig


def test_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "uqmm" in out.lower()


def test_create_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["create", "--help"])
    out = capsys.readouterr().out
    assert rc == 0
    # Flag surface is the contract — assert the key flags appear in --help.
    for flag in ("--os", "--version", "--vcpus", "--memory-mb", "--ssh-port", "--key"):
        assert flag in out, f"{flag} missing from create --help"


def test_status_zero_vms(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    # No VMs: should not crash and should say something useful.
    assert "no" in out.lower() or "vms" in out.lower() or out.strip() == ""


def test_list_zero_vms(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    rc = main(["list"])
    capsys.readouterr()
    assert rc == 0


def test_create_without_name_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Missing required positional `name` — cyclopts surfaces a parse error.
    rc = main(["create", "--os", "alpine", "--version", "3.21"])
    assert rc != 0


def _write_key(tmp_path: Path) -> Path:
    k = tmp_path / "id.pub"
    k.write_text("ssh-ed25519 AAA test@host\n")
    return k


def test_C10_corrupt_config_blocks_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    bad_vm = tmp_path / "data" / "uqmm" / "vms" / "broken"
    bad_vm.mkdir(parents=True)
    (bad_vm / "config.json").write_text("{ not valid json")
    # No --ssh-port so the allocator calls read_occupied_ports() and hits the corrupt config.
    rc = main(
        [
            "create",
            "newvm",
            "--os",
            "debian",
            "--version",
            "13",
            "--key",
            str(_write_key(tmp_path)),
        ]
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "broken" in err


def test_P10_list_skips_corrupt_config_with_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    vms = tmp_path / "data" / "uqmm" / "vms"
    good = vms / "goodvm"
    bad = vms / "badvm"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    VMConfig(name="goodvm", os="alpine", version="3.21", ssh_port=22500).save(good / "config.json")
    (bad / "config.json").write_text("{ not valid json")

    with patch("uqmm.cli.probe", return_value="stopped"):
        rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "goodvm" in out
    assert "badvm" in out
    assert "invalid-config" in out


def test_P10_status_named_corrupt_returns_invalid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    bad_vm = tmp_path / "data" / "uqmm" / "vms" / "broken"
    bad_vm.mkdir(parents=True)
    (bad_vm / "config.json").write_text("{ not valid json")

    rc = main(["status", "broken"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "invalid-config" in out


def test_P10_status_all_continues_past_corrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    vms = tmp_path / "data" / "uqmm" / "vms"
    good = vms / "goodvm"
    bad = vms / "badvm"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    VMConfig(name="goodvm", os="alpine", version="3.21", ssh_port=22500).save(good / "config.json")
    (bad / "config.json").write_text("{ not valid json")

    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "invalid-config" in out
