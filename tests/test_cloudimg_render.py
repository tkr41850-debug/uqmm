import yaml

from uqmm.builders.cloudimg import render_meta_data, render_user_data
from uqmm.config import VMConfig


def make_cfg(**kw: object) -> VMConfig:
    base: dict[str, object] = {
        "name": "deb13",
        "os": "debian",
        "version": "13",
        "ssh_authorized_keys": ["ssh-ed25519 AAA test@host"],
    }
    base.update(kw)
    return VMConfig(**base)  # pyright: ignore[reportArgumentType]


def test_user_data_starts_with_marker() -> None:
    cfg = make_cfg()
    rendered = render_user_data(cfg)
    assert rendered.startswith("#cloud-config\n"), "cloud-init requires the marker line"


def test_user_data_is_valid_yaml() -> None:
    cfg = make_cfg()
    rendered = render_user_data(cfg)
    parsed = yaml.safe_load(rendered)
    assert isinstance(parsed, dict)


def test_user_data_contains_user_with_keys() -> None:
    cfg = make_cfg(user="alice", ssh_authorized_keys=["ssh-rsa AAA k1", "ssh-ed25519 BBB k2"])
    parsed: dict[str, object] = yaml.safe_load(render_user_data(cfg))
    users_raw = parsed["users"]
    assert isinstance(users_raw, list)
    users: list[dict[str, object]] = users_raw  # pyright: ignore[reportAssignmentType, reportUnknownVariableType]
    alice: dict[str, object] | None = next((u for u in users if u.get("name") == "alice"), None)
    assert alice is not None
    assert alice["sudo"] == "ALL=(ALL) NOPASSWD:ALL"
    assert alice["shell"] == "/bin/bash"
    assert alice["ssh_authorized_keys"] == ["ssh-rsa AAA k1", "ssh-ed25519 BBB k2"]


def test_user_data_disables_password_auth() -> None:
    parsed = yaml.safe_load(render_user_data(make_cfg()))
    assert parsed["ssh_pwauth"] is False


def test_user_data_skips_package_upgrade() -> None:
    # First-boot apt upgrade is slow under TCG and not what we want by default.
    parsed = yaml.safe_load(render_user_data(make_cfg()))
    assert parsed.get("package_upgrade", False) is False
    assert parsed.get("package_update", False) is False


def test_user_data_enables_qemu_guest_agent() -> None:
    # qemu-guest-agent isn't preinstalled on Debian genericcloud; the runcmd
    # makes graceful QMP-driven shutdown work consistently across Debian + Ubuntu.
    parsed = yaml.safe_load(render_user_data(make_cfg()))
    runcmd = parsed.get("runcmd")
    assert isinstance(runcmd, list)
    flat = repr(runcmd)  # pyright: ignore[reportUnknownArgumentType]
    assert "qemu-guest-agent" in flat


def test_user_data_hostname_from_cfg() -> None:
    parsed = yaml.safe_load(render_user_data(make_cfg(hostname="alpha")))
    assert parsed["hostname"] == "alpha"


def test_user_data_hostname_falls_back_to_name() -> None:
    parsed = yaml.safe_load(render_user_data(make_cfg(name="vm1", hostname=None)))
    assert parsed["hostname"] == "vm1"


def test_user_data_root_user_uses_root_with_disable_root_false() -> None:
    # cfg.user == "root" should: opt out of cloud-init's default disable_root
    # (which prepends a no-port-forwarding command="..." to root's
    # authorized_keys), and write the key to root directly with no sudo
    # entry (root has it implicitly).
    cfg = make_cfg(user="root", ssh_authorized_keys=["ssh-ed25519 AAA root-key"])
    parsed: dict[str, object] = yaml.safe_load(render_user_data(cfg))
    assert parsed.get("disable_root") is False
    users_raw = parsed["users"]
    assert isinstance(users_raw, list)
    users: list[dict[str, object]] = users_raw  # pyright: ignore[reportAssignmentType, reportUnknownVariableType]
    assert len(users) == 1
    root = users[0]
    assert root["name"] == "root"
    assert root["ssh_authorized_keys"] == ["ssh-ed25519 AAA root-key"]
    assert "sudo" not in root


def test_user_data_non_root_leaves_disable_root_at_default() -> None:
    # Don't set disable_root explicitly when creating a normal user — keep
    # cloud-init's default behavior intact for that path.
    parsed: dict[str, object] = yaml.safe_load(render_user_data(make_cfg(user="alice")))
    assert "disable_root" not in parsed


def test_meta_data_local_hostname() -> None:
    parsed = yaml.safe_load(render_meta_data(make_cfg(name="vm1", hostname="alpha")))
    assert parsed["local-hostname"] == "alpha"


def test_meta_data_instance_id_set_and_stable() -> None:
    cfg = make_cfg(name="vm1")
    a = yaml.safe_load(render_meta_data(cfg))
    b = yaml.safe_load(render_meta_data(cfg))
    assert a["instance-id"] == b["instance-id"], "instance-id must be deterministic for a name"
    assert a["instance-id"] == "uqmm-vm1"
