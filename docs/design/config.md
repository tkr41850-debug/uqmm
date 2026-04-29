# Config

`VMConfig` is the single user-facing config object that drives provisioning across all three OS targets. Per-technique builders absorb the divergence below the abstraction.

## VMConfig

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class VMConfig:
    # Identity
    name: str                                  # required; identifier and default hostname
    os: Literal["alpine", "debian", "ubuntu"]  # required
    version: str                               # required: "3.21", "13" (trixie), "24.04"

    # Source — local file or URL; if None, uqmm resolves os+version → canonical URL
    image: str | None = None

    # Resources
    vcpus: int = 2                             # bumped automatically to 4 for alpine install
    memory_mb: int = 2048                      # bumped automatically to 4096 for alpine install
    disk_size_gb: int = 20

    # Network + access
    ssh_port: int | None = None                # None = auto-assigned in 22000-23000 at create time
    user: str = "uqmm"                         # SSH login name (overrides cloud-image defaults)
    ssh_authorized_keys: list[str] = field(default_factory=list)
    hostname: str | None = None                # None = use name
```

Once `create` resolves a port, it's recorded in `config.json` and reused on every subsequent `start` so any `~/.ssh/config` aliases stay valid. `delete` frees it.

`image` accepts a local file path (used as-is) or an HTTP(S) URL (downloaded to `$XDG_CACHE_HOME/uqmm/images/`). If omitted, uqmm resolves `os + version` to the canonical upstream URL — see [research/cloud-image.md](../research/cloud-image.md) and [research/alpine-unattended.md](../research/alpine-unattended.md) for the URL patterns.

## What's truly shared across OSes

- Identity: hostname (defaults to `name`).
- Primary user: SSH authorized keys + login name.
- Disk size.
- Network mode: DHCP (forced by SLiRP).

## What fundamentally differs

| Concern | Alpine (ISO + pexpect) | Debian/Ubuntu (cloud image) |
|---|---|---|
| Source artifact | `alpine-virt-X.Y.Z.iso` (~50 MB) | `*-genericcloud-amd64.qcow2` / `*-server-cloudimg-amd64.img` (~350-700 MB) |
| Provisioning model | Run installer interactively over serial; install to blank disk | Boot pre-installed image; cloud-init applies seed on first boot |
| Time to SSH-ready (TCG) | 2-4 min | 30-60 s |
| Storage layout | `setup-disk -m sys -s 0 /dev/vda` | Single partition, auto-grown by `cloud-initramfs-growroot` |
| Package manager | `apk` | `apt` |
| Default user (pre-uqmm) | `root`, no password (live ISO) | `debian` / `ubuntu` (preconfigured); uqmm overrides via cloud-config `users:` |
| Default ttyS0? | Yes (alpine-virt cmdline) | Yes (cloud images preconfigured) |
| Seed delivery | answer file via `wget http://10.0.2.2:8000/answers` typed at root prompt | CIDATA-labeled ISO attached as second virtio drive |

The smart abstraction stops at the **high-level intent** and dispatches to per-technique builders for storage and delivery.

## Per-technique builders

Each builder takes `VMConfig` → produces install/runtime QEMU args plus seed files.

```python
from pathlib import Path
from typing import Protocol

@dataclass
class InstallArtifacts:
    qemu_install_args: list[str]      # for the install / first boot
    qemu_runtime_args: list[str]      # for subsequent boots
    seed_paths: list[Path]            # files that must persist between launches

class SeedBuilder(Protocol):
    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts: ...
```

### `AlpineSeedBuilder` (stock ISO + serial pexpect)

- `answers` file (from `VMConfig` fields → `KEYMAPOPTS` / `HOSTNAMEOPTS` / `USEROPTS` / `USERSSHKEY` / etc.)
- A pexpect drive script (typing `wget`, `setup-alpine -ef`, root password, `reboot`).
- A local HTTP server serving the answers file at install time.
- `qemu_install_args`: `-cdrom alpine-virt-VER.iso -drive file=disk.qcow2,if=virtio -serial unix:SERIAL_SOCK,server=on,wait=on,reconnect-ms=1000 -no-reboot`
- `qemu_runtime_args`: `-drive file=disk.qcow2,if=virtio` (no CD)

No ISO rebuild. The custom-ISO/apkovl approach ([alpine-unattended.md](../research/alpine-unattended.md)) remains available as a fallback for offline or stricter-reproducibility scenarios.

### `CloudImageBuilder` (Debian + Ubuntu, unified)

- Cloud image (`debian-13-genericcloud-amd64.qcow2` / `noble-server-cloudimg-amd64.img`) downloaded to cache and qcow2-rebased to `vm_dir/disk.qcow2`, resized to `disk_size_gb`.
- `user-data` (cloud-config: hostname, users, ssh keys, packages, runcmd) + `meta-data` (instance-id, local-hostname).
- `seed.iso` via `xorriso -as mkisofs -V CIDATA ...`.
- `qemu_install_args` is the same as `qemu_runtime_args` — there's no separate install boot. Just: `-drive file=disk.qcow2,if=virtio -drive file=seed.iso,if=virtio,format=raw,readonly=on -no-reboot`.
- The seed disk can be detached on subsequent boots (cloud-init only reads it on first boot), but leaving it attached is harmless.

The Ubuntu autoinstall ISO + Debian d-i preseed paths remain available as fallbacks (see [iso-install-fallback.md](../research/iso-install-fallback.md)) for compliance / custom-partition / non-cloud-init scenarios.

## Lifecycle layer (fully shared)

QMP control, serial console reading, `-no-reboot` exit handling, and SSH readiness polling are entirely OS-agnostic. They consume `InstallArtifacts` and produce a "VM is ready" signal regardless of which builder ran.

```
launch_install_qemu(artifacts.qemu_install_args, qmp_sock, serial_path)
  ├─ Alpine: drive install via pexpect; wait for QEMU process exit (-no-reboot + guest reboot)
  └─ Cloud image: poll SSH; QEMU stays running (no second-stage relaunch)
launch_runtime_qemu(artifacts.qemu_runtime_args, qmp_sock, hostfwd_port)
  ├─ Alpine only: relaunch without install drive
  ├─ Poll SSH on 127.0.0.1:hostfwd_port until ready
  └─ Return SSH client
```

## Module layout

```
uqmm/
  __init__.py
  config.py              # VMConfig dataclass + (de)serialization
  resolve.py             # os+version → canonical image URL; download + cache
  state.py               # VM directory layout, PID file, port allocation
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

Two builders, three OS targets. The cloud-image builder collapses Debian and Ubuntu into one parameterized implementation (URL + default username differ).

See [cli.md](cli.md) for the user-facing command surface and on-disk state layout.
