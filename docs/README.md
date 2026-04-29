# uqmm research notes

Reference docs for building **uqmm**: a Python CLI QEMU machine manager that provisions VMs from ISO via unattended install, then drives them over SSH.

## Hard constraints

- QEMU **TCG only** — no KVM, no `/dev/kvm`.
- **SLiRP usermode networking only** (`-netdev user`) — no TUN/TAP, no bridging, no root.
- Can build software from source.
- hostfwd reachable from host; guest reaches host at `10.0.2.2`.

## Topics

- [Alpine unattended install](alpine-unattended.md) — answer file format, apkovl overlay, custom ISO build.
- [Ubuntu autoinstall](ubuntu-autoinstall.md) — cloud-init NoCloud, CIDATA seed ISO, autoinstall cmdline workaround.
- [QEMU control](qemu-control.md) — QMP socket, `qemu.qmp` Python client, lifecycle commands, serial console wiring.
- [TCG + SLiRP](tcg-slirp.md) — TCG tuning, CPU feature support under emulation, SLiRP networking gotchas.
- [Rootless tooling](rootless-tooling.md) — image creation/inspection, ISO building, all without root.
- [Unified config model](unified-config.md) — common `VMConfig` shape covering both OSes + per-OS builder sketch.

## Three blockers, three answers

| Blocker | Answer |
|---|---|
| No-VNC install for Alpine | Custom ISO with embedded apkovl that runs `setup-alpine -ef` via OpenRC `local` service |
| No-VNC install for Ubuntu | CIDATA seed ISO + `-kernel`/`-initrd` extracted from live-server ISO + `-append "autoinstall ..."` |
| Reboot/CD-eject handling | `-no-reboot` + watch QMP `SHUTDOWN` event + relaunch without install drive |

## Working baseline (manual VNC, current state)

```
nice -n 5 qemu-system-x86_64 \
    -cpu max,+avx \
    -smp 4 \
    -m 16G \
    -hda ~/vm/alpine.img \
    -cdrom alpine-virt-3.19.1-x86_64.iso \
    -netdev user,id=mynet0,hostfwd=tcp::5901-:22 \
    -device virtio-net-pci,netdev=mynet0 \
    -display none \
    -vnc :0
```

After uqmm is built, this becomes a fully unattended pipeline: `uqmm provision <name> --os alpine --version 3.21` → returns SSH client.
