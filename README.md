# uqmm

Headless QEMU machine manager. Provisions Alpine, Debian, and Ubuntu VMs from images via unattended install, then drives them over SSH. Targets QEMU TCG with SLiRP usermode networking — no KVM, no TUN/TAP, no root.

```sh
uqmm create deb13 --os debian --version 13 --key ~/.ssh/id_ed25519.pub
uqmm ssh deb13
```

See [docs/](docs/) for design, research, and implementation plan.
