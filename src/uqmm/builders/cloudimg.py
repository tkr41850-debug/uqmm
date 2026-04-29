"""CloudImageBuilder — Debian + Ubuntu, unified cloud-init NoCloud path.

See docs/design/config.md § CloudImageBuilder and docs/research/cloud-image.md.
"""

from __future__ import annotations

from typing import Any

import yaml

from uqmm.config import VMConfig


def render_user_data(cfg: VMConfig) -> str:
    """Render the cloud-init #cloud-config document for `cfg`.

    Disables password auth, skips package upgrade (slow under TCG), creates
    the configured user with the supplied SSH keys + passwordless sudo.
    """
    user_block: dict[str, Any] = {
        "name": cfg.user,
        "sudo": "ALL=(ALL) NOPASSWD:ALL",
        "shell": "/bin/bash",
        "ssh_authorized_keys": list(cfg.ssh_authorized_keys),
    }
    body: dict[str, Any] = {
        "hostname": cfg.effective_hostname(),
        "users": [user_block],
        "ssh_pwauth": False,
        "package_update": False,
        "package_upgrade": False,
    }
    return "#cloud-config\n" + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_meta_data(cfg: VMConfig) -> str:
    """Render the cloud-init NoCloud meta-data file.

    `instance-id` is derived from the VM name so cloud-init's per-instance
    state stays stable across reboots — change-of-instance triggers cloud-init
    to re-run first-boot logic, which we don't want.
    """
    body = {
        "instance-id": f"uqmm-{cfg.name}",
        "local-hostname": cfg.effective_hostname(),
    }
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)
