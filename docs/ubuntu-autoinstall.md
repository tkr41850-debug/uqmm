# Ubuntu autoinstall (Subiquity)

Goal: install Ubuntu Server into a disk image with zero interactive input via Subiquity's autoinstall + cloud-init NoCloud.

## Minimal user-data

```yaml
#cloud-config
autoinstall:
  version: 1
  identity:
    hostname: ubuntu
    username: ubuntu
    password: "$6$rounds=4096$..."     # mkpasswd -m sha-512
  ssh:
    install-server: true
    allow-pw: false
    authorized-keys:
      - ssh-ed25519 AAAA...
  storage:
    layout:
      name: direct                     # 'lvm' default uses LVM
  packages: []
  late-commands: []
```

The `#cloud-config` header and top-level `autoinstall:` key are **required** for the cloud-init delivery path. Schema reference: [Subiquity autoinstall reference](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html), [Providing autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html).

## Delivery: NoCloud seed ISO

NoCloud requires a filesystem labeled `CIDATA` (ISO9660 or vfat) with `user-data` and `meta-data` files. `meta-data` needs at minimum:

```yaml
instance-id: iid-1
```

Build the seed ISO (rootless):

```sh
xorriso -as mkisofs -o seed.iso -V CIDATA -J -r user-data meta-data
# OR
genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data
```

**Volume label MUST be uppercase `CIDATA`** ([cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)).

QEMU attach (Subiquity quickstart pattern):

```
-drive file=disk.qcow2,if=virtio
-cdrom ubuntu-24.04-live-server-amd64.iso
-drive file=seed.iso,format=raw,if=virtio
```

Reference: [autoinstall quickstart](https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/autoinstall-quickstart.html).

## The "Continue with autoinstall? (yes|no)" headache

By design, Subiquity prompts for confirmation **even with a CIDATA seed correctly attached**, unless the kernel cmdline contains the literal `autoinstall` token ([Zero-touch autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/explanation/zero-touch-autoinstall.html)).

With pure `-cdrom`, you cannot inject the cmdline. Two viable workarounds:

### Workaround A: extract kernel/initrd, boot directly — RECOMMENDED

Extract once per release (rootless):

```sh
# Option 1: 7z
7z e ubuntu-24.04-live-server-amd64.iso casper/vmlinuz casper/initrd

# Option 2: xorriso
xorriso -osirrox on -indev ubuntu-24.04-live-server-amd64.iso \
        -extract /casper/vmlinuz vmlinuz \
        -extract /casper/initrd initrd
```

QEMU launch:

```
-kernel ./vmlinuz
-initrd ./initrd
-append "autoinstall ds=nocloud;s=/cidata/ console=ttyS0,115200n8"
-cdrom ubuntu-24.04-live-server-amd64.iso
-drive file=seed.iso,format=raw,if=virtio
-drive file=disk.qcow2,if=virtio
```

Matches Canonical's own quickstart path. Simpler than ISO repacking.

### Workaround B: rebuild ISO with patched grub.cfg

Use `xorriso -as mkisofs ...` to repackage the ISO with `autoinstall` and `ds=nocloud` injected into `boot/grub/grub.cfg` and `isolinux/isolinux.cfg`. More work but lets you keep `-cdrom`-only invocation.

## Alternate: HTTP-served autoinstall

Cmdline `ds=nocloud-net;s=http://10.0.2.2:PORT/` (semicolon must be escaped in GRUB as `\;`):

```
autoinstall console=ttyS0,115200n8 ds=nocloud-net\;s=http://10.0.2.2:PORT/
```

Trailing slash on URL is **required**. Many setups also need `cloud-config-url=/dev/null` to avoid double-fetching. Run `python3 -m http.server 8000 --bind 0.0.0.0` on the host serving `user-data`/`meta-data`/`network-config`.

## Release support

22.04 LTS, 24.04 LTS, and 24.10 all support headless autoinstall ([intro to autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/intro-to-autoinstall.html)).

- **24.04 LTS** — safest target.
- **22.04** — older Subiquity, occasional YAML schema quirks.
- **24.10** — interim, non-LTS.

Under TCG, budget **10–25 minutes per install** with `-smp 4 -m 4G` minimum. Subiquity is a snap-based Python app and is heavy.

## Completion

Autoinstall reboots automatically at the end. Pair with `-no-reboot` so QEMU exits instead of looping back into the install.

`late-commands` runs in `/target` chroot — useful for `curtin in-target -- systemctl enable ssh` etc., but `ssh.install-server: true` already covers SSH for headless first-boot.

## Serial console

Live-server does **not** route to ttyS0 by default. The kernel cmdline `console=ttyS0,115200n8` is required for serial-driven observation. Already shown in the workarounds above.

## Sources

- [Subiquity autoinstall reference](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html)
- [Providing autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html)
- [Autoinstall quickstart](https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/autoinstall-quickstart.html)
- [Intro to autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/intro-to-autoinstall.html)
- [Zero-touch autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/explanation/zero-touch-autoinstall.html)
- [Operate the server installer](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/operate-server-installer.html)
- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [cloud-init: Run locally with QEMU](https://docs.cloud-init.io/en/latest/howto/launch_qemu.html)
