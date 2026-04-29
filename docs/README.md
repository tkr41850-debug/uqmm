# uqmm docs

**uqmm** is a Python CLI QEMU machine manager that provisions VMs from images via unattended install, then drives them over SSH. Targets QEMU TCG with SLiRP usermode networking — no KVM, no TUN/TAP, no root.

## Layout

- **[research/](research/)** — what we learned about Alpine install, cloud images, QEMU control, TCG/SLiRP constraints, rootless tooling. Reference material; doesn't churn.
- **[design/](design/)** — what we're building: config schema, CLI surface, state layout. Evolves with the codebase.

## Two paths, three OS targets

| OS family | Path | Time to SSH-ready (TCG) |
|---|---|---|
| Alpine | Stock ISO + serial pexpect typing 3 commands at root prompt | 2-4 min |
| Debian + Ubuntu | Cloud image qcow2 + NoCloud cidata seed | 30-60 s |

## Working baseline (current manual state)

```sh
nice -n 5 qemu-system-x86_64 \
    -cpu max,+avx -smp 4 -m 16G \
    -hda ~/vm/alpine.img \
    -cdrom alpine-virt-3.19.1-x86_64.iso \
    -netdev user,id=mynet0,hostfwd=tcp::5901-:22 \
    -device virtio-net-pci,netdev=mynet0 \
    -display none -vnc :0
```

After uqmm: `uqmm create alpine-vm --os alpine --version 3.21` returns an SSH-ready VM.
