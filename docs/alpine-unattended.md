# Alpine unattended install

Goal: install Alpine into a disk image with **zero interactive input** (no VNC), then have SSH ready on first reboot.

## Answer file format (`setup-alpine -f`)

Canonical fields, sourced from the [Alpine wiki: Using an answerfile with setup-alpine](https://wiki.alpinelinux.org/wiki/Using_an_answerfile_with_setup-alpine):

```sh
KEYMAPOPTS="us us"                       # or "none"
HOSTNAMEOPTS=alpine
DEVDOPTS=mdev                            # mdev | mdevd | udev
INTERFACESOPTS="auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"
DNSOPTS="-d example.org 1.1.1.1 9.9.9.9"
TIMEZONEOPTS="UTC"
PROXYOPTS="none"
APKREPOSOPTS="-1"                        # -1 = first mirror; -c = community; -f = http
USEROPTS="-a -u -g audio,video,netdev juser"
USERSSHKEY="ssh-ed25519 AAAA..."         # raw key OR a URL
SSHDOPTS="openssh"                       # openssh | dropbear | none
ROOTSSHKEY="ssh-ed25519 AAAA..."         # raw key OR a URL
NTPOPTS="chrony"
DISKOPTS="-m sys -s 0 /dev/vda"          # -s 0 disables swap
LBUOPTS="none"
APKCACHEOPTS="/var/cache/apk"
```

`BOOTLOADER` and `KERNELOPTS` are **not** first-class answer-file keys — they are environment variables consumed by `setup-disk` ([Setup-disk wiki](https://wiki.alpinelinux.org/wiki/Setup-disk)).

**Generate canonical skeleton**: run `setup-alpine -c <file>` against the actual target Alpine version. The wiki page has documented inconsistencies (`INTERFACESOPTS` vs `INTERFACEOPTS`, `APKREPOSOPTS` vs `APKREPOSPTS`) — trust the live `setup-alpine -c` output over the wiki.

**Version drift**: `DEVDOPTS` was added in `alpine-conf` April 2022 ([commit 648d10f](https://github.com/alpinelinux/alpine-conf/commit/648d10f12618f48da9b31bc9e438fdc074d79bfa)); present in every supported release. The 3.21 release notes do not change the schema.

## Truly non-interactive? Not quite.

Even with every field set, `setup-alpine -f` still prompts for:

1. **Root password** — set this in post-commands (`chpasswd`), not via answer file.
2. **Disk-erase confirmation** — suppress with `ERASE_DISKS=/dev/vda setup-alpine -ef answers`. Use the `-e` flag together with `-f` ([eradman.com/posts/autoinstall-alpine](https://eradman.com/posts/autoinstall-alpine.html)).

## Seeding the answer file: ranked methods under our constraints

### 1. Custom-rebuilt ISO with embedded apkovl — RECOMMENDED

Match for our constraints (TCG, SLiRP, no root, no VNC). Pattern verified at [skreutz.com](https://www.skreutz.com/posts/unattended-installation-of-alpine-linux/) and Alpine's own [Unattended Boot and Install](https://wiki.alpinelinux.org/wiki/Unattended_Boot_and_Install) wiki, corroborated by [Diskless Mode](https://wiki.alpinelinux.org/wiki/Diskless_Mode).

Layout:

```
ovl/
  etc/
    auto-setup-alpine/answers
    local.d/auto-setup-alpine.start         # invokes setup-alpine and reboots
    runlevels/default/local                 # symlink enabling local-service
```

Tar as `localhost.apkovl.tar.gz` and inject into the alpine-virt ISO:

```sh
xorriso -indev original.iso \
        -outdev custom.iso \
        -map localhost.apkovl.tar.gz /localhost.apkovl.tar.gz \
        -boot_image any replay
```

On boot, Alpine's diskless init auto-discovers `*.apkovl.tar.gz` at the ISO root, applies the overlay, and the OpenRC `local` service runs `auto-setup-alpine.start`, which calls:

```sh
#!/bin/sh
ERASE_DISKS=/dev/vda timeout 300 setup-alpine -ef /etc/auto-setup-alpine/answers
echo "root:$(head -c 32 /dev/urandom | base64)" | chpasswd
reboot
```

**Works under our exact constraints** — pure CD-ROM boot, no kernel cmdline edits, no second drive, no network needed during install.

### 2. Local HTTP server at 10.0.2.2 + remote answer file

`setup-alpine -f` accepts an HTTP URL directly. SLiRP's gateway `10.0.2.2` maps to the host, so a Python `http.server` is reachable from the guest with no port-forward. But you still need an apkovl on the ISO to *invoke* `setup-alpine -ef http://10.0.2.2:NNNN/answers` — so this is really option 1 with the answers fetched live, useful when the manager templates per-VM answers.

### 3. Second virtual disk with answers

Works (`-drive file=answers.img,format=raw`), but you still need an apkovl on the ISO to run `setup-alpine -ef /media/sdb/answers`. No advantage over option 1.

### 4. `apkovl=` kernel cmdline — N/A for our boot path

The `apkovl=` kernel parameter is a feature of Alpine's *netboot/PXE* initramfs ([PXE boot wiki](https://wiki.alpinelinux.org/wiki/PXE_boot)). With standard `-cdrom`, the ISO's bootloader runs and you'd need to either replace the ISO's syslinux/grub config or use `-kernel`/`-initrd` extracted from the ISO. Custom-ISO route is far less fiddly.

## Post-install state

- `setup-alpine` does **not** auto-reboot. Your apkovl autorun script must call `reboot`.
- `setup-alpine` does **not** auto-eject the CD. Use QEMU `-no-reboot` so QEMU exits when the apkovl script reboots, then relaunch without `-cdrom`.
- After setup: working bootloader (syslinux by default; grub if EFI/`USE_EFI=1`), sshd enabled (if `SSHDOPTS=openssh`), user-injected SSH key in `~user/.ssh/authorized_keys`, root password locked unless set in post-commands.

## Serial console

Alpine virt ISO already has `console=tty0 console=ttyS0,115200` on the kernel cmdline ([Enable Serial Console on Boot wiki](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)) — full boot output on `ttyS0` without intervention.

## musl gotchas (brief)

Pain points that bite Python CLI workloads:

- Glibc-only prebuilt binaries fail (Node.js historically, some closed-source tools).
- `manylinux` Python wheels are glibc-only; install with `apk add py3-numpy py3-pandas` from Alpine repos or accept slow source builds.
- DNS resolver differences (musl skips `/etc/nsswitch.conf`).
- C extensions need `apk add build-base musl-dev linux-headers`.

For "general purpose" workloads, Ubuntu is the safer default; Alpine wins on size and boot speed.

## Sources

- [Alpine wiki: Using an answerfile with setup-alpine](https://wiki.alpinelinux.org/wiki/Using_an_answerfile_with_setup-alpine)
- [Alpine wiki: Setup-disk](https://wiki.alpinelinux.org/wiki/Setup-disk)
- [Alpine wiki: Unattended Boot and Install](https://wiki.alpinelinux.org/wiki/Unattended_Boot_and_Install)
- [Alpine wiki: Diskless Mode](https://wiki.alpinelinux.org/wiki/Diskless_Mode)
- [Alpine wiki: PXE boot](https://wiki.alpinelinux.org/wiki/PXE_boot)
- [Alpine wiki: Enable Serial Console on Boot](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)
- [skreutz.com: Unattended installation of Alpine Linux](https://www.skreutz.com/posts/unattended-installation-of-alpine-linux/)
- [eradman.com: Autoinstall Alpine](https://eradman.com/posts/autoinstall-alpine.html)
- [alpine-conf commit 648d10f (DEVDOPTS added)](https://github.com/alpinelinux/alpine-conf/commit/648d10f12618f48da9b31bc9e438fdc074d79bfa)
