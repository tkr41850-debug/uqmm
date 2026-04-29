"""CloudImageBuilder — Debian + Ubuntu, unified cloud-init NoCloud path.

See docs/design/config.md § CloudImageBuilder and docs/research/cloud-image.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pycdlib
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


def build_seed_iso(user_data: str, meta_data: str, out: Path) -> None:
    """Write a NoCloud cidata ISO containing user-data + meta-data to `out`.

    Volume identifier is lowercase `cidata` per the 2025 cloud-init
    deprecation of uppercase variants — see docs/design/toolchain.md
    gotcha #2.
    """
    # ISO9660 level 1 limits names to 8.3, so the lower layer uses 8.3
    # placeholders; cloud-init reads the Joliet (long-filename) layer, which
    # exposes the canonical `user-data` / `meta-data` names.
    iso = pycdlib.PyCdlib()
    iso.new(joliet=3, vol_ident="cidata")
    try:
        for name, iso_basename, content in (
            ("user-data", "USERDATA", user_data),
            ("meta-data", "METADATA", meta_data),
        ):
            data = content.encode("utf-8")
            iso.add_fp(
                _bytes_io(data),
                len(data),
                iso_path=f"/{iso_basename}.;1",
                joliet_path=f"/{name}",
            )
        iso.write(str(out))
    finally:
        iso.close()


def _bytes_io(data: bytes) -> Any:
    """Return a fresh BytesIO for `data` (pycdlib rewinds the stream itself)."""
    import io

    return io.BytesIO(data)
