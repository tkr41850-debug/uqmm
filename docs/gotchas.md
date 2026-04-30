# Gotchas

Hard-won findings from actually running uqmm under QEMU TCG + SLiRP. These supplement (and in some places correct) the upstream wiki guidance referenced from `docs/research/`.

## Alpine ISO + serial pexpect path

### Alpine virt 3.21 syslinux cmdline does NOT include `console=ttyS0`

The Alpine wiki ([KVM](https://wiki.alpinelinux.org/wiki/KVM), [Enable Serial Console on Boot](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)) implies the virt ISO's bootloader already routes the kernel and getty to ttyS0. As of Alpine 3.21 this is **out of date** — the syslinux/grub cmdline only has `console=tty0`. Boot the stock ISO with `-cdrom` alone and you get a blank serial socket.

**Fix:** bypass isolinux entirely. Mount the ISO with `pycdlib`, extract `boot/vmlinuz-virt` and `boot/initramfs-virt`, and launch with:

```
-kernel  /tmp/uqmm-vmlinuz
-initrd  /tmp/uqmm-initramfs
-append  "modules=loop,squashfs,sd-mod,usb-storage console=ttyS0,115200"
```

The `modules=` list is the same one Alpine's own isolinux config passes — without it the squashfs root never mounts.

### `reconnect-ms` is rejected by QEMU 11.0+ on server-listen sockets

QEMU 11.0 tightened chardev option validation. `-serial unix:...,server=on,wait=on,reconnect-ms=1000` now fails to start with `reconnect-ms is invalid for server-listen sockets`. The flag was only ever meaningful for client sockets that need to retry the connect.

**Fix:** drop `reconnect-ms` on every server-listen serial/QMP/monitor chardev. The original rationale (keep the chardev alive if the host process drops) doesn't apply to server-listen — QEMU just keeps the listen socket open.

### Realistic install time is ~25 min, not 2-4 min

Under TCG with 2 vcpus + 1 GB RAM, end-to-end stock-ISO install (live boot → DHCP → wget answers → `setup-alpine -ef` → reboot → first-boot sshd-keygen → SSH-ready) is **~25 minutes**, not the few minutes a glance at the wiki might suggest. Live-ISO boot, the apk install of the base system + OpenRC, and the first-boot RSA host-key generation each cost real seconds on emulated CPU.

The cloud-image path (Debian/Ubuntu) is in a completely different bucket: 30-60 s.

### Answer-file pitfalls

| Field | Pitfall | Use instead |
|---|---|---|
| `DNSOPTS=""` | Clobbers udhcpc-written `/etc/resolv.conf` — DNS dies before `setup-apkrepos` runs | `DNSOPTS="-n 10.0.2.3"` (the SLiRP DNS forwarder) |
| `APKREPOSOPTS="-1"` | "Auto-pick first mirror" requires resolving `mirrors.alpinelinux.org`, which is flaky from inside SLiRP | `APKREPOSOPTS="https://dl-cdn.alpinelinux.org/alpine/v$VER/main"` — pass the full URL including `/v$VER/main`. Despite the wiki claim, `setup-apkrepos` does **not** auto-append the version/repo path to a positional URL |
| `USEROPTS="-g 'wheel,audio' u"` | Word-split: embedded quotes are preserved literally and end up in `/etc/group` membership | `USEROPTS="-g wheel,audio u"` — no quotes around the comma list |

### Marker matching over serial echoes the typed command back

Sending `echo UQMM_DONE` over a bidirectional serial chardev produces two ttyS0 lines: the kernel-echoed input (`echo UQMM_DONE`) followed by the actual stdout (`UQMM_DONE`). A naive `expect("UQMM_DONE")` matches the typed-command echo and races ahead before the command has actually run.

**Fix:** anchor the marker on a leading newline — `expect(r"\nUQMM_DONE\b")` — so only the stdout line matches.

## Cloud-image path (Debian + Ubuntu)

### Default user is not root

Debian generic-cloud and Ubuntu cloud images ship with a default unprivileged user (`debian`, `ubuntu`) and **password auth disabled**. The NoCloud seed's `user-data` controls passwordless sudo and authorized_keys for that user; trying to SSH in as `root` will fail even with the right key.

### Resize qcow2 BEFORE first boot

`qemu-img resize disk.qcow2 +18G` only changes the qcow2 envelope; the guest filesystem is grown on first boot by `cloud-init` / `growpart`. Resize **before** the first launch — resizing after first boot leaves the disk envelope larger but the partition + filesystem stuck at the original ~2 GB, and `growpart` won't re-run on subsequent boots without manual intervention.

## Tooling choices

These are not gotchas exactly, but the rationale for the choices in `docs/design/toolchain.md`:

- **`pip install qemu.qmp`** — official asyncio QMP client, maintained alongside QEMU.
- **`xorriso`** for ISO building — rootless, reliable, the de facto tool for ISO patching.
- **`guestmount` / `guestfish`** (libguestfs FUSE) for image inspection — rootless. Avoid `qemu-nbd`; it requires the `nbd` kernel module and root to `modprobe nbd` + `qemu-nbd --connect`.
