# ISO install (fallback for Debian + Ubuntu)

For most uqmm uses, [cloud-image.md](cloud-image.md) is the better path: 30-60 s to SSH-ready vs 25-50 min for ISO install under TCG, ~10 lines of Python vs autoinstall YAML + ISO repack.

Use this fallback when:

- You need a clean OS image you produced yourself (compliance, auditability, no upstream-preinstalled tooling like `snapd` on Ubuntu).
- You need a non-cloud-init layout (custom partitions, LUKS, LVM, non-default kernel flavor).
- You need offline install with no internet.

This doc covers Ubuntu Subiquity autoinstall and Debian d-i preseed. Both work but require either ISO modification or `-kernel`/`-initrd` extraction.

---

## Ubuntu Server (Subiquity autoinstall)

### Minimal user-data

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

The `#cloud-config` header and top-level `autoinstall:` key are **required**. Schema reference: [Subiquity autoinstall reference](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html), [Providing autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html).

### Delivery: NoCloud seed ISO

NoCloud requires a filesystem labeled `cidata` / `CIDATA` (ISO9660 or vfat) with `user-data` and `meta-data` files. `meta-data` needs at minimum:

```yaml
instance-id: iid-1
```

Build the seed ISO (rootless):

```sh
xorriso -as mkisofs -o seed.iso -V CIDATA -J -r user-data meta-data
# OR
genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data
```

`CIDATA` is the conventional spelling in docs, but lowercase `cidata` is also valid and matches this repo's implementation/examples ([cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)).

### The "Continue with autoinstall? (yes|no)" headache

By design, Subiquity prompts for confirmation **even with a CIDATA seed correctly attached**, unless the kernel cmdline contains the literal `autoinstall` token ([Zero-touch autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/explanation/zero-touch-autoinstall.html)).

With pure `-cdrom`, you cannot inject the cmdline. Recommended workaround: extract kernel/initrd, boot directly:

```sh
# Rootless extraction
xorriso -osirrox on -indev ubuntu-24.04-live-server-amd64.iso \
        -extract /casper/vmlinuz vmlinuz \
        -extract /casper/initrd initrd
```

QEMU launch:

```
-kernel ./vmlinuz
-initrd ./initrd
-append "autoinstall ds=nocloud console=ttyS0,115200n8"
-cdrom ubuntu-24.04-live-server-amd64.iso
-drive file=seed.iso,format=raw,if=virtio
-drive file=disk.qcow2,if=virtio
```

This keeps the CIDATA-labeled seed drive for autodiscovery while injecting the required `autoinstall` kernel token. Alternative: rebuild ISO with patched `grub.cfg` (more work, lets you keep `-cdrom`-only invocation).

### HTTP-served autoinstall

For network-served NoCloud, use `ds=nocloud;s=http://10.0.2.2:PORT/` (older cloud-init releases may still document `nocloud-net`). Escape the semicolon in GRUB as `\;`:

```
autoinstall console=ttyS0,115200n8 ds=nocloud\;s=http://10.0.2.2:PORT/
```

Trailing slash on URL is **required**. Run `python3 -m http.server 8000 --bind 0.0.0.0` on the host serving `user-data`/`meta-data`/`network-config`.

### Release support and timing

22.04 LTS, 24.04 LTS, and 24.10 all support headless autoinstall ([intro to autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/intro-to-autoinstall.html)). **24.04 LTS** is the safest target.

Under TCG, budget **10–25 minutes per install** with `-smp 4 -m 4G` minimum. Subiquity is a snap-based Python app and is heavy.

### Completion

Autoinstall reboots automatically at the end. Pair with `-no-reboot` so QEMU exits cleanly. Live-server does **not** route to ttyS0 by default — the cmdline workarounds above all add `console=ttyS0,115200n8`.

---

## Debian (debian-installer preseed)

### Boot menu does NOT render on serial

The standard `debian-X.Y.Z-amd64-netinst.iso` ships an isolinux/syslinux config with no `serial` directive — the boot menu only paints on VGA. There is no "Install (Serial Console)" menu entry on amd64. This is a long-standing issue ([Debian bug #1108876](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1108876), filed July 2025).

### Stock-ISO + serial pexpect — fragile but possible

Even though the bootloader menu is invisible on serial, isolinux **does** read keystrokes from the serial port. Path:

1. After a fixed sleep (no prompt to expect on — you're typing blind), send `Esc` to drop to the `boot:` prompt.
2. Type the install command literally:

```
install console=ttyS0,115200n8 gfxpayload=text auto=true priority=critical \
        preseed/url=http://10.0.2.2:8000/preseed.cfg ---
```

This works without ISO modification but is strictly worse than Alpine's path because there's no anchor string to `expect()` on. Caveat: a UEFI boot uses GRUB instead of isolinux with different keystroke behavior — pin BIOS via QEMU's default firmware (SeaBIOS) to keep this path working.

### Minimal preseed.cfg

Verified against the [official trixie example](https://www.debian.org/releases/trixie/example-preseed.txt):

```
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string uqmm-debian
d-i netcfg/get_domain string local
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

d-i passwd/root-login boolean false
d-i passwd/user-fullname string uqmm
d-i passwd/username string uqmm
d-i passwd/user-password-crypted password $6$...   # mkpasswd -m sha-512
d-i passwd/user-default-groups string sudo

d-i clock-setup/utc boolean true
d-i time/zone string Etc/UTC
d-i clock-setup/ntp boolean true

d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string sudo qemu-guest-agent ca-certificates
d-i pkgsel/upgrade select full-upgrade
popularity-contest popularity-contest/participate boolean false

d-i grub-installer/bootdev string /dev/vda
d-i finish-install/reboot_in_progress note

d-i preseed/late_command string \
  in-target mkdir -p /home/uqmm/.ssh; \
  in-target sh -c 'echo "ssh-ed25519 AAAA..." > /home/uqmm/.ssh/authorized_keys'; \
  in-target chown -R uqmm:uqmm /home/uqmm/.ssh; \
  in-target chmod 700 /home/uqmm/.ssh; \
  in-target chmod 600 /home/uqmm/.ssh/authorized_keys; \
  in-target sh -c 'echo "uqmm ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/uqmm'
```

### Preseed delivery options

| Method | ISO mod required? | Notes |
|---|---|---|
| `preseed/url=http://10.0.2.2:8000/preseed.cfg` on cmdline | No (with the keystroke trick above) | **Recommended for the d-i path.** |
| `preseed/file=/cdrom/preseed.cfg` | Yes | Embed in remastered ISO. |
| Preseed embedded into initrd | Yes | Extract initrd, append preseed.cfg, rebuild ([wiki.debian.org/DebianInstaller/Qemu](https://wiki.debian.org/DebianInstaller/Qemu)). |
| `auto=true` bare | Falls back to DHCP option 210 / TFTP / local file | Not usable with stock SLiRP DHCP. |

**There is no way to preseed without either editing the ISO/initrd OR injecting a kernel cmdline.** The bootloader has to be told *something*.

### d-i preseed gotchas

- Preseed file is debconf data, not shell — **exactly one space** between key and value, no inline comments after values, blank lines OK.
- A network-fetched preseed cannot answer pre-network questions (locale, keyboard, network config itself). Use `auto=true priority=critical` so d-i defers them until after preseed download.
- `late_command` runs as a single shell string; chain with `;` and prefix in-VM commands with `in-target`.
- HTTPS preseed URLs fail trust validation in d-i unless you set `debian-installer/allow_unauthenticated_ssl=true`. Use plain HTTP from 10.0.2.2.
- Sample preseed mismatched to release version causes mystery extra prompts — use the file from the matching release.
- `partman` recipes are picky; `partman-auto/method=regular` + `choose_recipe=atomic` is the safe single-partition default.

### Release targeting

Target **trixie (Debian 13)** — stable since 2025-08-09, latest point release 13.4 on 2026-03-14. Bookworm (Debian 12) goes to LTS on 2026-07-11 ([release.debian.org](https://release.debian.org/)).

---

## Sources

- [Subiquity autoinstall reference](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html)
- [Providing autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html)
- [Autoinstall quickstart](https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/autoinstall-quickstart.html)
- [Zero-touch autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/explanation/zero-touch-autoinstall.html)
- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [Debian Installation Guide ch05s03 (boot parameters / serial)](https://www.debian.org/releases/stable/amd64/ch05s03.en.html)
- [Debian Installation Guide Appendix B (preseeding)](https://www.debian.org/releases/bookworm/amd64/apb.en.html)
- [Debian trixie example-preseed.txt](https://www.debian.org/releases/trixie/example-preseed.txt)
- [Debian wiki: DebianInstaller/Preseed](https://wiki.debian.org/DebianInstaller/Preseed)
- [Debian wiki: DebianInstaller/Qemu](https://wiki.debian.org/DebianInstaller/Qemu)
- [Debian releases page](https://www.debian.org/releases/index.en.html)
- [Debian bug #1108876 — netinst serial not default (2025)](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1108876)
- [debian-user 2020/05 thread on serial install](https://lists.debian.org/debian-user/2020/05/msg00351.html) and [working cmdline reply](https://lists.debian.org/debian-user/2020/05/msg00361.html)
