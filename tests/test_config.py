import json
from pathlib import Path
from unittest.mock import patch

import pytest

from uqmm.config import VMConfig


def make_minimal() -> VMConfig:
    return VMConfig(name="vm1", os="alpine", version="3.21")


def test_defaults() -> None:
    cfg = make_minimal()
    assert cfg.image is None
    assert cfg.vcpus == 2
    assert cfg.memory_mb == 2048
    assert cfg.disk_size_gb == 20
    assert cfg.ssh_port is None
    assert cfg.user == "uqmm"
    assert cfg.ssh_authorized_keys == []
    assert cfg.hostname is None
    assert cfg.state == "created"


def test_effective_hostname_falls_back_to_name() -> None:
    cfg = make_minimal()
    assert cfg.effective_hostname() == "vm1"
    cfg2 = VMConfig(name="vm1", os="alpine", version="3.21", hostname="myhost")
    assert cfg2.effective_hostname() == "myhost"


def test_json_round_trip_minimal() -> None:
    cfg = make_minimal()
    restored = VMConfig.from_json(cfg.to_json())
    assert restored == cfg


def test_json_round_trip_full() -> None:
    cfg = VMConfig(
        name="vm1",
        os="ubuntu",
        version="24.04",
        image="https://example.com/img.qcow2",
        vcpus=4,
        memory_mb=4096,
        disk_size_gb=40,
        ssh_port=22042,
        user="alice",
        ssh_authorized_keys=["ssh-ed25519 AAA...", "ssh-rsa BBB..."],
        hostname="alpha",
        state="failed",
    )
    restored = VMConfig.from_json(cfg.to_json())
    assert restored == cfg


def test_json_is_pretty() -> None:
    cfg = make_minimal()
    blob = cfg.to_json()
    # human-readable for git diffs / hand-editing
    assert "\n" in blob
    parsed = json.loads(blob)
    assert parsed["name"] == "vm1"


def test_from_json_rejects_unknown_os() -> None:
    blob = json.dumps({"name": "vm1", "os": "freebsd", "version": "14"})
    with pytest.raises(ValueError, match="os"):
        VMConfig.from_json(blob)


def test_from_json_rejects_invalid_state() -> None:
    blob = json.dumps({"name": "vm1", "os": "alpine", "version": "3.21", "state": "garbage"})
    with pytest.raises(ValueError, match="state"):
        VMConfig.from_json(blob)


def test_from_json_requires_name() -> None:
    blob = json.dumps({"os": "alpine", "version": "3.21"})
    with pytest.raises((ValueError, TypeError)):
        VMConfig.from_json(blob)


def test_save_and_load(tmp_path: Path) -> None:
    cfg = VMConfig(name="vm1", os="debian", version="13", ssh_port=22500)
    path = tmp_path / "config.json"
    cfg.save(path)
    assert path.exists()
    restored = VMConfig.load(path)
    assert restored == cfg


def test_C8_save_atomic_via_tmp_rename(tmp_path: Path) -> None:
    cfg = VMConfig(name="vm1", os="alpine", version="3.21", ssh_port=22500)
    path = tmp_path / "config.json"
    written_paths: list[str] = []
    original_write = Path.write_text

    def track_write(self: Path, text: str, *args: object, **kwargs: object) -> None:
        written_paths.append(str(self))
        original_write(self, text, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "write_text", track_write):
        cfg.save(path)

    # write_text is called only once, on the .tmp file (os.replace does the rename)
    assert len(written_paths) == 1
    assert written_paths[0].endswith(".tmp")
    # final file exists with full content; tmp file is gone
    assert path.exists()
    assert not Path(written_paths[0]).exists()
    assert VMConfig.load(path) == cfg


def test_C8_save_does_not_truncate_on_failure(tmp_path: Path) -> None:
    good_cfg = VMConfig(name="vm1", os="alpine", version="3.21", ssh_port=22500)
    path = tmp_path / "config.json"
    good_cfg.save(path)
    original_blob = path.read_text()

    bad_cfg = VMConfig(name="vm1", os="debian", version="13", ssh_port=22600)
    with patch.object(Path, "write_text", side_effect=OSError("disk full")), pytest.raises(OSError):
        bad_cfg.save(path)

    assert path.read_text() == original_blob


def test_from_json_ignores_unknown_extra_fields() -> None:
    # forward-compat: a future field should not crash older readers.
    blob = json.dumps(
        {
            "name": "vm1",
            "os": "alpine",
            "version": "3.21",
            "future_field": "ignored",
        }
    )
    cfg = VMConfig.from_json(blob)
    assert cfg.name == "vm1"
