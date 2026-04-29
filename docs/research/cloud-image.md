# Cloud-image install (Debian + Ubuntu)

Goal: skip the install entirely. Boot a pre-built cloud-image qcow2, seed it via cloud-init NoCloud, get SSH-ready in 30-60 seconds.

This is the **recommended path for Debian and Ubuntu**. See [iso-install-fallback.md](iso-install-fallback.md) for Subiquity autoinstall / d-i preseed when cloud images don't fit.

## Why cloud images win

| | Cloud image | ISO install |
|---|---|---|
| Time to SSH-ready (TCG) | 30-60 s | 25-50 min |
| Code complexity | ~10 lines Python | autoinstall YAML + ISO repack OR d-i preseed + bootloader keystroke trick |
| ISO modification needed? | No | Yes (Ubuntu) or fragile (Debian) |
| Default user | `debian` / `ubuntu` (not root) | configurable |
| Password auth | locked, SSH key only | configurable |

For "give me a Linux VM I can SSH into" — what uqmm does — there is no good reason to install from ISO when a cloud image exists.

## Image sources

| OS | URL pattern | Image to use |
|---|---|---|
| Debian 13 (trixie) | https://cloud.debian.org/images/cloud/trixie/latest/ | `debian-13-genericcloud-amd64.qcow2` |
| Debian 12 (bookworm) | https://cloud.debian.org/images/cloud/bookworm/latest/ | `debian-12-genericcloud-amd64.qcow2` |
| Ubuntu 24.04 (Noble) | https://cloud-images.ubuntu.com/noble/current/ | `noble-server-cloudimg-amd64.img` |
| Ubuntu 22.04 (Jammy) | https://cloud-images.ubuntu.com/jammy/current/ | `jammy-server-cloudimg-amd64.img` |

**Debian variant selection** ([wiki.debian.org/Cloud/SystemsComparison](https://wiki.debian.org/Cloud/SystemsComparison)): use `genericcloud` (smallest, no firmware bloat). **Avoid `nocloud-*`** despite the suggestive name — that variant ships *without* cloud-init NoCloud configured (it's intended for image-customization workflows). The `generic` variant adds firmware/drivers we don't need under SLiRP/virtio.

## NoCloud cidata seed

The cloud-init NoCloud datasource looks for a filesystem labeled `CIDATA` (or `cidata`) containing `user-data` and `meta-data`.

`user-data`:

```yaml
#cloud-config
hostname: uqmm-debian
users:
  - name: uqmm
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ssh-ed25519 AAAA... user@host
ssh_pwauth: false
package_update: false
runcmd:
  - [ systemctl, enable, --now, qemu-guest-agent ]   # not preinstalled on Debian
```

`meta-data`:

```yaml
instance-id: uqmm-1
local-hostname: uqmm-debian
```

Build the seed ISO (rootless):

```sh
genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data
# OR
xorriso -as mkisofs -o seed.iso -V CIDATA -J -r user-data meta-data
```

## QEMU launch

```sh
qemu-system-x86_64 \
    -accel tcg,thread=multi -cpu max,+avx -smp 4 -m 2G \
    -drive file=debian-13-genericcloud-amd64.qcow2,if=virtio \
    -drive file=seed.iso,if=virtio,format=raw,readonly=on \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=n0 \
    -display none \
    -serial unix:/tmp/uqmm.sock,server=on,wait=off,reconnect-ms=1000 \
    -qmp unix:/tmp/qmp.sock,server=on,wait=off
```

No `-cdrom`, no `-kernel`/`-initrd`, no kernel cmdline edits. The cloud image's bootloader is preconfigured for serial console.

## Resize the rootfs before first boot

The cloud image qcow2 base is ~2 GB. The image's root partition auto-grows via `cloud-initramfs-growroot` on first boot, but only to the size of the qcow2. Resize **before** boot:

```sh
qemu-img resize debian-13-genericcloud-amd64.qcow2 +18G   # → 20 GB rootfs
```

## SSH readiness signal

Poll the hostfwd port for the SSH banner:

```python
import socket, time

def wait_ssh(port: int, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            banner = s.recv(64)
            s.close()
            if banner.startswith(b"SSH-"):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
        time.sleep(2)
    return False
```

## Cloud image gotchas

- **Default user is `debian` or `ubuntu`** — not root, not your username. Set the user explicitly in `users:` (as shown above). The default user still exists; either accept it or `users: [...]` overrides the implicit default.
- **Password auth is disabled** by default. SSH key only. If your code path expects password login, add `ssh_pwauth: true` and set a password via `chpasswd:`.
- **`qemu-guest-agent` is preinstalled on Ubuntu** but **NOT on Debian**. Add to `runcmd` if uqmm relies on it for graceful shutdown / IP reporting.
- **First-boot network wait** — cloud-init's "wait for network" adds 10-30 s under TCG with SLiRP. SLiRP DHCP responds immediately but cloud-init's interface up/down dance is slow.
- Use `-cpu max` under TCG; default `qemu64` is significantly slower for crypto/dpkg work cloud-init does on first boot.
- Cloud images sometimes ship with `console=` already set in GRUB to route to both tty0 and ttyS0 — no kernel cmdline edits needed for serial observation.

## Debian release targeting

Target **trixie (Debian 13)** — stable since 2025-08-09, latest point release 13.4 on 2026-03-14 ([release.debian.org](https://release.debian.org/)). Bookworm (Debian 12) goes to LTS on 2026-07-11.

## Sources

- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [cloud.debian.org image listing](https://cloud.debian.org/images/cloud/)
- [Debian wiki: Cloud (FAQ)](https://wiki.debian.org/Cloud/)
- [Debian wiki: Cloud/SystemsComparison](https://wiki.debian.org/Cloud/SystemsComparison)
- [Ubuntu Noble cloud images](https://cloud-images.ubuntu.com/noble/current/)
- [Debian releases page](https://www.debian.org/releases/index.en.html)
- [cloud-init Run locally with QEMU](https://docs.cloud-init.io/en/latest/howto/launch_qemu.html)
