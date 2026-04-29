import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from uqmm import state
from uqmm.config import VMConfig


def test_data_root_honors_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert state.data_root() == tmp_path / "uqmm"


def test_data_root_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.data_root() == tmp_path / ".local" / "share" / "uqmm"


def test_cache_root_honors_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert state.cache_root() == tmp_path / "uqmm"


def test_cache_root_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.cache_root() == tmp_path / ".cache" / "uqmm"


def test_vm_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert state.vm_dir("vm1") == tmp_path / "uqmm" / "vms" / "vm1"


def test_iter_vm_dirs_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert list(state.iter_vm_dirs()) == []


def test_iter_vm_dirs_finds_vms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "uqmm" / "vms" / "a").mkdir(parents=True)
    (tmp_path / "uqmm" / "vms" / "b").mkdir(parents=True)
    (tmp_path / "uqmm" / "vms" / "stray-file").write_text("not a vm")
    found = sorted(p.name for p in state.iter_vm_dirs())
    assert found == ["a", "b"]


def test_pick_ssh_port_skips_occupied() -> None:
    with patch("uqmm.state.socket.socket") as mock_sock:
        mock_sock.return_value.__enter__.return_value.bind = MagicMock()  # always succeeds
        port = state.pick_ssh_port({22000}, lo=22000, hi=22002)
        assert port == 22001


def test_pick_ssh_port_first_free() -> None:
    with patch("uqmm.state.socket.socket") as mock_sock:
        mock_sock.return_value.__enter__.return_value.bind = MagicMock()
        port = state.pick_ssh_port(set(), lo=22000, hi=22002)
        assert port == 22000


def test_pick_ssh_port_skips_bind_failure() -> None:
    bind_results = iter([OSError("EADDRINUSE"), None])  # first fails, second succeeds

    def fake_bind(_addr: tuple[str, int]) -> None:
        result = next(bind_results)
        if isinstance(result, OSError):
            raise result

    with patch("uqmm.state.socket.socket") as mock_sock:
        mock_sock.return_value.__enter__.return_value.bind = fake_bind
        port = state.pick_ssh_port(set(), lo=22000, hi=22002)
        assert port == 22001


def test_pick_ssh_port_exhausted() -> None:
    with patch("uqmm.state.socket.socket") as mock_sock:
        mock_sock.return_value.__enter__.return_value.bind.side_effect = OSError("EADDRINUSE")
        with pytest.raises(RuntimeError, match="no free SSH port"):
            state.pick_ssh_port(set(), lo=22000, hi=22002)


def test_pick_ssh_port_all_occupied() -> None:
    occupied = {22000, 22001, 22002}
    with patch("uqmm.state.socket.socket") as mock_sock:
        mock_sock.return_value.__enter__.return_value.bind = MagicMock()
        with pytest.raises(RuntimeError, match="no free SSH port"):
            state.pick_ssh_port(occupied, lo=22000, hi=22002)


def test_read_occupied_ports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    vms = tmp_path / "uqmm" / "vms"
    a = vms / "a"
    b = vms / "b"
    c_no_port = vms / "c"
    for d in (a, b, c_no_port):
        d.mkdir(parents=True)
    VMConfig(name="a", os="alpine", version="3.21", ssh_port=22050).save(a / "config.json")
    VMConfig(name="b", os="debian", version="13", ssh_port=22070).save(b / "config.json")
    VMConfig(name="c", os="ubuntu", version="24.04").save(c_no_port / "config.json")
    assert state.read_occupied_ports() == {22050, 22070}


def test_read_occupied_ports_skips_corrupt_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    vms = tmp_path / "uqmm" / "vms"
    good = vms / "good"
    bad = vms / "bad"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    VMConfig(name="good", os="alpine", version="3.21", ssh_port=22055).save(
        good / "config.json"
    )
    (bad / "config.json").write_text("{ this is not valid json")
    # Corrupt config is silently skipped — port allocator stays useful.
    assert state.read_occupied_ports() == {22055}


def test_pick_ssh_port_real_bind_smoke() -> None:
    # No mocking — exercises the real socket.bind path against the loopback
    # so we know the implementation actually closes the test socket.
    port = state.pick_ssh_port(set(), lo=22500, hi=22999)
    assert 22500 <= port <= 22999
    # The picked port must be free (we just released it); we can re-bind here.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
