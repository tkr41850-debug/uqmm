import pytest

from uqmm.cli import main


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
