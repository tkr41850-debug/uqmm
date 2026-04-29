"""SeedBuilder protocol + InstallArtifacts.

Each per-OS builder takes a VMConfig + the VM's working directory and produces
the QEMU args needed for install-time and runtime, plus the seed files written
to disk.

For cloud-image-based OSes the install/runtime args are identical (cloud-init
runs in-image on first boot, no separate install phase). For Alpine they differ
— the install boot adds `-cdrom` and `-no-reboot`, the runtime boot drops both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from uqmm.config import VMConfig


@dataclass
class InstallArtifacts:
    qemu_install_args: list[str]
    qemu_runtime_args: list[str]
    seed_paths: list[Path] = field(default_factory=list)


class SeedBuilder(Protocol):
    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts: ...
