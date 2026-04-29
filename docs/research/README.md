# Research notes

Findings from investigating how to install OSes unattended into headless QEMU under tight constraints. Reference material consumed by the design docs.

## Hard constraints

- QEMU **TCG only** — no KVM, no `/dev/kvm`.
- **SLiRP usermode networking only** (`-netdev user`) — no TUN/TAP, no bridging, no root.
- Can build software from source.
- hostfwd reachable from host; guest reaches host at `10.0.2.2`.

## Topics

- [Alpine unattended install](alpine-unattended.md) — answer file format, stock ISO + serial pexpect (recommended), apkovl/custom-ISO fallback.
- [Cloud-image install (Debian + Ubuntu)](cloud-image.md) — pre-built qcow2 + NoCloud cidata seed. Recommended path for Debian and Ubuntu.
- [ISO install fallback (Debian + Ubuntu)](iso-install-fallback.md) — Subiquity autoinstall + d-i preseed for compliance, custom-partition, offline scenarios.
- [QEMU control](qemu-control.md) — QMP socket, `qemu.qmp` Python client, lifecycle commands, serial console wiring.
- [TCG + SLiRP](tcg-slirp.md) — TCG tuning, CPU feature support under emulation, SLiRP networking gotchas.
- [Rootless tooling](rootless-tooling.md) — image creation/inspection, ISO building, all without root.

## Three blockers, three answers

| Blocker | Answer |
|---|---|
| No-VNC install for Alpine | Stock ISO + serial pexpect: wait at `localhost login:`, type `root`, fetch answers via `wget`, run `setup-alpine -ef`. Custom-ISO/apkovl is a fallback. |
| No-VNC install for Debian/Ubuntu | Skip the install entirely — boot a cloud-image qcow2 with a NoCloud cidata seed. ISO install (Subiquity/d-i preseed) is fallback. |
| Reboot/CD-eject handling (Alpine path) | `-no-reboot` + watch QMP `SHUTDOWN` event + relaunch without install drive. |
