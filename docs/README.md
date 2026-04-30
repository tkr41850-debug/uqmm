# uqmm docs

**uqmm** is a Python CLI QEMU machine manager that provisions VMs from images via unattended install, then drives them over SSH. Targets QEMU TCG with SLiRP usermode networking — no KVM, no TUN/TAP, no root.

## Layout

- **[research/](research/)** — what we learned about Alpine install, cloud images, QEMU control, TCG/SLiRP constraints, rootless tooling. Reference material; doesn't churn.
- **[design/](design/)** — what we're building: config schema, CLI surface, state layout. Evolves with the codebase.
- **[implementation/baseline/](implementation/baseline/)** — phased plan for getting from green-field to a working `uqmm create` for both install paths.
- **[implementation/01-qol/](implementation/01-qol/)** — quality-of-life pass: state I/O robustness, launch correctness, input validation, re-runnable `create`, Alpine install resumability.
- **[issues/](issues/)** — catalogued user-experience scenarios with verification status and adoption tracking.
- **[gotchas.md](gotchas.md)** — hard-won findings from running uqmm under TCG + SLiRP that supplement (and in places correct) the upstream wiki guidance.

## Two paths, three OS targets

| OS family | Path | Time to SSH-ready (TCG) |
|---|---|---|
| Alpine | Stock ISO + serial pexpect driving `setup-alpine -ef` over the console | ~25 min (live boot + apk install + first-boot sshd-keygen all on emulated CPU — see [gotchas.md](gotchas.md#realistic-install-time-is-25-min-not-2-4-min)) |
| Debian + Ubuntu | Cloud image qcow2 + NoCloud `cidata` seed | 30-60 s |

## Working baseline (current manual state)

```sh
nice -n 5 qemu-system-x86_64 \
    -accel tcg,thread=multi -cpu max,+avx -smp 4 -m 4G \
    -drive file=~/vm/alpine.qcow2,if=virtio \
    -cdrom alpine-virt-3.21.0-x86_64.iso \
    -netdev user,id=mynet0,hostfwd=tcp::5901-:22 \
    -device virtio-net-pci,netdev=mynet0 \
    -display none \
    -serial unix:/tmp/serial.sock,server=on,wait=on \
    -qmp unix:/tmp/qmp.sock,server=on,wait=off \
    -no-reboot
```

After uqmm: `uqmm create alpine-vm --os alpine --version 3.21` returns an SSH-ready VM.
