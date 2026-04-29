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

**Version drift**: `DEVDOPTS` was added in `alpine-conf` April 2022 ([commit 648d10f](https://github.com/alpinelinux/alpine-conf/commit/648d10f12618f48da9b31bc9e438fdc074d79bfa)); present in every supported release.

## Truly non-interactive? Not quite.

Even with every field set, `setup-alpine -ef` still prompts for:

1. **Root password** (twice — "New password:" and "Retype password:"). Drive these via pexpect.
2. **Disk-erase confirmation** — suppress with `ERASE_DISKS=/dev/vda` env var.

## Two viable approaches

### RECOMMENDED: Stock ISO + serial-driven pexpect

**Custom ISO is NOT necessary.** Alpine's own [Packer installation wiki](https://wiki.alpinelinux.org/wiki/Packer_installation) documents exactly this pattern: boot stock `alpine-virt-X.Y.Z-x86_64.iso`, attach to QEMU's bidirectional serial port, type a few commands at the root prompt.

Why this works out of the box:

1. Alpine virt ISO already has `console=tty0 console=ttyS0,115200` on its syslinux cmdline ([Enable Serial Console on Boot wiki](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)) — getty respawns on `ttyS0` after kernel boot.
2. Live ISO root has **no password** ([Installing Alpine in a virtual machine](https://wiki.alpinelinux.org/wiki/Installing_Alpine_in_a_virtual_machine)).
3. BusyBox `wget` is built in.
4. SLiRP DHCPs the guest at 10.0.2.15 with gateway 10.0.2.2 → host's `python3 -m http.server` is reachable.

Required QEMU args (in addition to the baseline):

```
-serial unix:/tmp/uqmm.sock,server=on,wait=on,reconnect-ms=1000
-cdrom alpine-virt-3.21.0-x86_64.iso
-drive file=disk.qcow2,if=virtio
-no-reboot
```

`wait=on` blocks QEMU launch until the Python control process connects — no boot output is missed. `reconnect-ms` keeps the chardev alive if the host process drops, so the install doesn't deadlock on a blocked write.

Pexpect drive script (~30 lines), modeled on the [Packer installation](https://wiki.alpinelinux.org/wiki/Packer_installation) wiki:

```python
import socket
import pexpect.fdpexpect

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("/tmp/uqmm.sock")
c = pexpect.fdpexpect.fdspawn(s, timeout=120, encoding="utf-8")
c.delaybeforesend = 0.05

c.expect("localhost login: ");      c.sendline("root")
c.expect(r":~# ")
c.sendline("stty cols 200")          # avoid wrapped output confusing regex
c.expect(r":~# ")
c.sendline("ifconfig eth0 up && udhcpc -i eth0")
c.expect(r":~# ")
c.sendline(
    "wget -O /tmp/answers http://10.0.2.2:8000/answers && "
    "export ERASE_DISKS=/dev/vda && "
    "setup-alpine -ef /tmp/answers")

# setup-alpine prompts for new root password twice even with -f
c.expect("New password: ");          c.sendline(ROOT_PW)
c.expect("Retype password: ");       c.sendline(ROOT_PW)
c.expect(r":~# ", timeout=600)       # install finishes
c.sendline("reboot")
```

Anchor regex on stable substrings (`r":~# "`, `r"login: "`) rather than full lines. Wrap each `expect()` with a panic-grep alternation (`Kernel panic|Call Trace|exception`) so a crashed install fails loudly instead of hanging.

**Total moving parts:**

- Stock ISO (no rebuild).
- Answer file generated from `VMConfig`.
- `python3 -m http.server` on host serving the answer file.
- ~30-line pexpect script.

See [QEMU control: serial console wiring](qemu-control.md#serial-console-wiring) for the generic bidirectional-chardev mechanism.

### Alternative: Custom ISO with embedded apkovl

When to choose this: pre-network customization, fully offline install, or stricter reproducibility. Pattern verified at [skreutz.com](https://www.skreutz.com/posts/unattended-installation-of-alpine-linux/) and Alpine's [Unattended Boot and Install](https://wiki.alpinelinux.org/wiki/Unattended_Boot_and_Install) wiki, corroborated by [Diskless Mode](https://wiki.alpinelinux.org/wiki/Diskless_Mode).

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

On boot, Alpine's diskless init auto-discovers `*.apkovl.tar.gz` at the ISO root, applies the overlay, and the OpenRC `local` service runs:

```sh
#!/bin/sh
ERASE_DISKS=/dev/vda timeout 300 setup-alpine -ef /etc/auto-setup-alpine/answers
echo "root:$(head -c 32 /dev/urandom | base64)" | chpasswd
reboot
```

**Pros:** offline-installable, no host HTTP server, no expect timing concerns.
**Cons:** ISO build dependency (`xorriso`), more moving parts, slower iteration during development.

### Why not other approaches

- **`apkovl=` kernel cmdline** — Alpine *netboot/PXE* feature only ([PXE boot wiki](https://wiki.alpinelinux.org/wiki/PXE_boot)). With standard `-cdrom`, the ISO's bootloader runs and you can't pass kernel args without ISO rebuild or `-kernel`/`-initrd` extraction.
- **Second virtual disk with answers** — works as a transport, but you still need either the serial pexpect path or a custom ISO to invoke `setup-alpine`. No standalone advantage.

## Post-install state

- `setup-alpine` does **not** auto-reboot — the pexpect script (or apkovl autorun) must call `reboot`.
- `setup-alpine` does **not** auto-eject the CD. Use QEMU `-no-reboot` so QEMU exits when the script reboots, then relaunch without `-cdrom`.
- After setup: working bootloader (syslinux by default; grub if EFI/`USE_EFI=1`), sshd enabled (if `SSHDOPTS=openssh`), user-injected SSH key in `~user/.ssh/authorized_keys`.

## Comparison table

| Approach | Upfront cost | Brittleness across releases | Error handling |
|---|---|---|---|
| **Stock ISO + serial pexpect** (recommended) | Lowest — ~30 lines pexpect, 20-line answers, `python -m http.server` | Low — `setup-alpine -f` is a stable contract; only login + 1 wget + 1 invocation are typed | Good — answerfile owns most fields; ~3 expect points to maintain |
| Custom ISO with apkovl | Highest — needs `xorriso`, syslinux config, apkovl tarball with `etc/local.d/auto-setup.start` | Lowest — apkovl format is stable; you control the script | Best — script runs in real shell, can `set -e`, log everything |
| Pure pexpect typing every prompt | Medium — long brittle expect/send chain | Highest — any new prompt breaks script | Worst — missed prompt hangs indefinitely |

## musl gotchas (brief)

Pain points that bite Python CLI workloads:

- Glibc-only prebuilt binaries fail (Node.js historically, some closed-source tools).
- `manylinux` Python wheels are glibc-only; install with `apk add py3-numpy py3-pandas` from Alpine repos or accept slow source builds.
- DNS resolver differences (musl skips `/etc/nsswitch.conf`).
- C extensions need `apk add build-base musl-dev linux-headers`.

For "general purpose" workloads, Ubuntu is the safer default; Alpine wins on size and boot speed.

## Sources

- [Alpine wiki: Packer installation (the canonical stock-ISO + serial pattern)](https://wiki.alpinelinux.org/wiki/Packer_installation)
- [Alpine wiki: Using an answerfile with setup-alpine](https://wiki.alpinelinux.org/wiki/Using_an_answerfile_with_setup-alpine)
- [Alpine wiki: setup-alpine](https://wiki.alpinelinux.org/wiki/Setup-alpine)
- [Alpine wiki: Setup-disk](https://wiki.alpinelinux.org/wiki/Setup-disk)
- [Alpine wiki: Unattended Boot and Install](https://wiki.alpinelinux.org/wiki/Unattended_Boot_and_Install)
- [Alpine wiki: Diskless Mode](https://wiki.alpinelinux.org/wiki/Diskless_Mode)
- [Alpine wiki: PXE boot](https://wiki.alpinelinux.org/wiki/PXE_boot)
- [Alpine wiki: Enable Serial Console on Boot](https://wiki.alpinelinux.org/wiki/Enable_Serial_Console_on_Boot)
- [Alpine wiki: Installing Alpine in a virtual machine (root, no password)](https://wiki.alpinelinux.org/wiki/Installing_Alpine_in_a_virtual_machine)
- [Alpine wiki: KVM (alpine-virt + console=ttyS0)](https://wiki.alpinelinux.org/wiki/KVM)
- [Alpine wiki: BusyBox (wget applet in base)](https://wiki.alpinelinux.org/wiki/BusyBox)
- [skreutz.com: Unattended installation of Alpine Linux](https://www.skreutz.com/posts/unattended-installation-of-alpine-linux/)
- [eradman.com: Autoinstall Alpine](https://eradman.com/posts/autoinstall-alpine.html)
- [Wejn: Alpine unattended install](https://wejn.org/2022/04/alpinelinux-unattended-install/)
- [pexpect SocketSpawn / fdpexpect docs](https://pexpect.readthedocs.io/en/latest/api/socket_pexpect.html)
- [alpine-conf commit 648d10f (DEVDOPTS added)](https://github.com/alpinelinux/alpine-conf/commit/648d10f12618f48da9b31bc9e438fdc074d79bfa)
