# Unified config model

Goal: a single `VMConfig` shape that drives unattended install on both Alpine and Ubuntu, with per-OS builders absorbing the divergence.

## What's truly shared across OSes

- **Identity**: hostname, timezone.
- **Primary user**: name, password hash, SSH authorized keys, supplementary groups.
- **Root SSH policy**: enabled/disabled, authorized keys.
- **sshd**: enable/disable.
- **Disk**: size, layout intent (`simple` vs `lvm`).
- **Packages**: extra packages to install at provision time.
- **Post-install commands**: shell snippets to run after base install (`late-commands` for Ubuntu, `local.d` script entries for Alpine).
- **Network mode**: DHCP (forced by SLiRP).

## What fundamentally differs

| Concern | Alpine | Ubuntu |
|---|---|---|
| Storage layout DSL | `setup-disk` flags (`DISKOPTS="-m sys -s 0 /dev/vda"`) | Subiquity `storage:` graph or `layout: { name: direct\|lvm }` |
| Package manager | `apk` | `apt` |
| Repo selection | `APKREPOSOPTS` | `apt:` block in autoinstall |
| Delivery vehicle | apkovl tarball injected into ISO | CIDATA seed ISO + cmdline `autoinstall` |
| Bootloader hook | `setup-disk` `BOOTLOADER` env var | Subiquity grub config |
| Auto-reboot at end? | No — apkovl script must `reboot` | Yes |
| Default ttyS0? | Yes (alpine-virt cmdline) | No (must add `console=ttyS0,115200n8`) |

The smart abstraction stops at the **high-level intent** and dispatches to per-OS builders for storage and delivery.

## Python sketch

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

@dataclass
class SshAuth:
    authorized_keys: list[str]
    permit_root_login: bool = False
    password_login: bool = False

@dataclass
class User:
    name: str
    password_hash: str | None = None      # crypt(3) sha-512; None = locked
    groups: list[str] = field(default_factory=lambda: ["wheel"])
    ssh: SshAuth | None = None

@dataclass
class VMConfig:
    os: Literal["alpine", "ubuntu"]
    os_version: str                       # "3.21", "24.04"
    hostname: str
    user: User                            # primary admin
    timezone: str = "UTC"
    root_ssh: SshAuth | None = None
    disk_size_gb: int = 20
    disk_layout: Literal["simple", "lvm"] = "simple"
    packages: list[str] = field(default_factory=list)
    post_install: list[str] = field(default_factory=list)
```

## Per-OS builder boundary

Each builder takes `VMConfig` → produces install/runtime QEMU args plus seed files.

```python
@dataclass
class InstallArtifacts:
    qemu_install_args: list[str]      # for the install boot
    qemu_runtime_args: list[str]      # for the post-install boot
    seed_paths: list[Path]            # files that must persist between launches

class SeedBuilder(Protocol):
    def build(self, cfg: VMConfig, workdir: Path) -> InstallArtifacts: ...
```

### `AlpineSeedBuilder` produces

- `answers` file (from `VMConfig` fields → `KEYMAPOPTS`/`HOSTNAMEOPTS`/etc.)
- `localhost.apkovl.tar.gz` containing answers + autorun script
- Custom ISO via `xorriso -map ... -boot_image any replay`
- `qemu_install_args`: `-cdrom custom.iso -drive file=disk.qcow2,if=virtio -no-reboot`
- `qemu_runtime_args`: `-drive file=disk.qcow2,if=virtio` (no CD)

### `UbuntuSeedBuilder` produces

- `user-data` + `meta-data` (from `VMConfig` fields)
- `seed.iso` via `xorriso -as mkisofs -V CIDATA ...`
- Extracted `vmlinuz` + `initrd` from live-server ISO (cached per release)
- `qemu_install_args`: `-kernel vmlinuz -initrd initrd -append "autoinstall ds=nocloud;s=/cidata/ console=ttyS0,115200n8" -cdrom ubuntu.iso -drive file=seed.iso,format=raw,if=virtio -drive file=disk.qcow2,if=virtio -no-reboot`
- `qemu_runtime_args`: `-drive file=disk.qcow2,if=virtio`

## Lifecycle layer (fully shared)

The QMP control, serial console reading, `-no-reboot` exit handling, and SSH readiness polling are entirely OS-agnostic. They consume `InstallArtifacts` and produce a "VM is ready" signal regardless of which builder ran.

```
launch_install_qemu(artifacts.qemu_install_args, qmp_sock, serial_path)
  └─ wait for QEMU process exit (triggered by -no-reboot + guest reboot)
  └─ verify install via serial log markers
launch_runtime_qemu(artifacts.qemu_runtime_args, qmp_sock, hostfwd_port)
  └─ poll SSH on 127.0.0.1:hostfwd_port until ready
  └─ return SSH client
```

## Suggested module layout

```
uqmm/
  __init__.py
  config.py              # VMConfig, User, SshAuth dataclasses
  builders/
    __init__.py
    base.py              # SeedBuilder protocol, InstallArtifacts
    alpine.py            # AlpineSeedBuilder
    ubuntu.py            # UbuntuSeedBuilder
  qemu/
    __init__.py
    qmp.py               # qemu.qmp wrapper, lifecycle commands
    serial.py            # serial console reader
    process.py           # subprocess launcher with -no-reboot handling
  ssh.py                 # paramiko/asyncssh client + readiness polling
  cli.py                 # argparse / click entry point
```

## Bottom line

The user-facing config object stays unified at intent level. Per-OS divergence is encapsulated in two small builder modules. Everything below the builder (lifecycle, QMP, SSH) is fully shared. Adding a third OS later (Debian, Fedora) is one new builder module.
