# uqmm research notes

Reference docs for building **uqmm**: a Python CLI QEMU machine manager that provisions VMs from ISO via unattended install, then drives them over SSH.

## Hard constraints

- QEMU **TCG only** — no KVM, no `/dev/kvm`.
- **SLiRP usermode networking only** (`-netdev user`) — no TUN/TAP, no bridging, no root.
- Can build software from source.
- hostfwd reachable from host; guest reaches host at `10.0.2.2`.

## Topics

- [Alpine unattended install](alpine-unattended.md) — answer file format, stock-ISO + serial pexpect (recommended), apkovl/custom-ISO fallback.
- [Cloud-image install (Debian + Ubuntu)](cloud-image.md) — pre-built qcow2 + NoCloud cidata seed. Recommended path for both Debian and Ubuntu.
- [ISO install fallback (Debian + Ubuntu)](iso-install-fallback.md) — Subiquity autoinstall + d-i preseed for compliance / custom-partition / offline scenarios.
- [QEMU control](qemu-control.md) — QMP socket, `qemu.qmp` Python client, lifecycle commands, serial console wiring.
- [TCG + SLiRP](tcg-slirp.md) — TCG tuning, CPU feature support under emulation, SLiRP networking gotchas.
- [Rootless tooling](rootless-tooling.md) — image creation/inspection, ISO building, all without root.
- [Unified config model](unified-config.md) — common `VMConfig` shape + per-technique builder sketch (two builders cover three OSes).

## Two paths, three OS targets

| OS family | Path | Time to SSH-ready (TCG) |
|---|---|---|
| Alpine | Stock ISO + serial pexpect typing 3 commands at root prompt | 2-4 min |
| Debian + Ubuntu | Cloud image qcow2 + NoCloud cidata seed | 30-60 s |

Reboot/CD-eject handling is shared: `-no-reboot` + watch QMP `SHUTDOWN` event + relaunch without install drive (only relevant for the Alpine path; cloud images don't need a second launch).

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
