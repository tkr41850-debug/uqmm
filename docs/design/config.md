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

| Concern | Alpine (ISO + pexpect) | Debian/Ubuntu (cloud image) |
|---|---|---|
| Source artifact | `alpine-virt-X.Y.Z.iso` (~50 MB) | `*-genericcloud-amd64.qcow2` / `*-server-cloudimg-amd64.img` (~350-700 MB) |
| Provisioning model | Run installer interactively over serial; install to blank disk | Boot pre-installed image; cloud-init applies seed on first boot |
| Time to SSH-ready (TCG) | 2-4 min | 30-60 s |
| Storage layout | `setup-disk -m sys -s 0 /dev/vda` (one partition, no swap) | Single partition, auto-grown by `cloud-initramfs-growroot` |
| Package manager | `apk` | `apt` (Debian + Ubuntu) |
| Default user | `root` (no password) on live ISO; uqmm-created user after install | `debian` / `ubuntu` (preconfigured); add custom user via cloud-config `users:` |
| Default ttyS0? | Yes (alpine-virt cmdline) | Yes (cloud images preconfigured) |
| Seed delivery | answer file via `wget http://10.0.2.2:8000/answers` typed at root prompt | CIDATA-labeled ISO attached as second virtio drive |

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
    os: Literal["alpine", "debian", "ubuntu"]
    os_version: str                       # "3.21", "13" (trixie), "24.04"
    hostname: str
    user: User                            # primary admin
    timezone: str = "UTC"
    root_ssh: SshAuth | None = None
    disk_size_gb: int = 20
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

### `AlpineSeedBuilder` produces (recommended path: stock ISO + serial pexpect)

- `answers` file (from `VMConfig` fields → `KEYMAPOPTS`/`HOSTNAMEOPTS`/etc.)
- A pexpect drive script (typing `wget`, `setup-alpine -ef`, root password, `reboot`).
- A local HTTP server serving the answers file at install time.
- `qemu_install_args`: `-cdrom alpine-virt-VER.iso -drive file=disk.qcow2,if=virtio -serial unix:SERIAL_SOCK,server=on,wait=on,reconnect-ms=1000 -no-reboot`
- `qemu_runtime_args`: `-drive file=disk.qcow2,if=virtio` (no CD)

No ISO rebuild. The custom-ISO/apkovl approach ([alpine-unattended.md](../research/alpine-unattended.md)) remains available as a fallback for offline-only or stricter reproducibility scenarios.

### `CloudImageBuilder` produces (Debian + Ubuntu, unified)

- Downloaded cloud image (`debian-13-genericcloud-amd64.qcow2` or `noble-server-cloudimg-amd64.img`), cached + resized to target disk size.
- `user-data` (cloud-config: hostname, users, ssh keys, packages, runcmd) + `meta-data` (instance-id, local-hostname).
- `seed.iso` via `xorriso -as mkisofs -V CIDATA ...`.
- `qemu_install_args` is the same as `qemu_runtime_args` — there's no separate install boot. Just: `-drive file=cloudimg.qcow2,if=virtio -drive file=seed.iso,if=virtio,format=raw,readonly=on -no-reboot`.
- The seed disk can be detached on subsequent boots if desired (cloud-init only reads it on first boot, but leaving it attached is harmless).

The Ubuntu autoinstall ISO + Debian d-i preseed paths remain available as fallbacks (see [iso-install-fallback.md](../research/iso-install-fallback.md)) for compliance / custom-partition / non-cloud-init scenarios.

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
    alpine.py            # AlpineSeedBuilder (ISO + pexpect)
    cloudimg.py          # CloudImageBuilder (Debian + Ubuntu, NoCloud cidata)
  qemu/
    __init__.py
    qmp.py               # qemu.qmp wrapper, lifecycle commands
    serial.py            # serial console reader (Alpine path)
    process.py           # subprocess launcher with -no-reboot handling
  ssh.py                 # paramiko/asyncssh client + readiness polling
  cli.py                 # argparse / click entry point
```

Two builders, three OS targets. The cloud image builder collapses Debian and Ubuntu into one parameterized implementation (URL + default username differ).

## Bottom line

The user-facing config object stays unified at intent level. Per-technique divergence is encapsulated in two small builder modules: `AlpineSeedBuilder` (ISO+pexpect) and `CloudImageBuilder` (Debian + Ubuntu via NoCloud cidata). Everything below the builder (lifecycle, QMP, SSH) is fully shared. Adding Fedora or Arch later means one URL + one default-user added to the cloud image builder.
