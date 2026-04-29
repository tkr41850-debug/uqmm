# QEMU control: QMP, lifecycle, serial console

Programmatic control of a headless QEMU process: launch → observe install → catch reboot → relaunch → SSH.

## QMP socket setup

Add a Unix socket QMP endpoint alongside existing args. Modern QEMU uses explicit `on/off` (not deprecated bool form):

```
-qmp unix:/tmp/qmp.sock,server=on,wait=off
```

`server=on,wait=off` means QEMU listens but does not block boot waiting for a client. TCP form `tcp:127.0.0.1:4444,server=on,wait=off` works identically; **prefer Unix sockets** (no port collisions, easier permissions).

You can have multiple `-qmp` flags simultaneously — useful if you want one socket for orchestration and one for an interactive `qmp-shell`.

## Python client: `qemu.qmp`

The official, currently-maintained PyPI package is **`qemu.qmp`** (note the dot). Source at [gitlab.com/qemu-project/python-qemu-qmp](https://gitlab.com/qemu-project/python-qemu-qmp). Marked alpha but is the reference library. Asyncio-only.

```sh
pip install qemu.qmp
```

Do **not** use the third-party `qmp` package (different project, less maintained). Do **not** roll your own JSON-over-socket.

## Minimal client

```python
import asyncio
from qemu.qmp import QMPClient

async def main():
    qmp = QMPClient("vm")
    await qmp.connect("/tmp/qmp.sock")
    await qmp.negotiate()  # qmp_capabilities handshake

    # Lifecycle commands
    await qmp.execute("system_powerdown")    # ACPI shutdown signal
    await qmp.execute("system_reset")        # hard reset
    await qmp.execute("quit")                # kill QEMU process

    # Listen for SHUTDOWN/RESET events
    with qmp.listener(("SHUTDOWN", "RESET")) as listener:
        ev = await listener.get()
        print(ev)

    await qmp.disconnect()

asyncio.run(main())
```

## Block device discovery and CD eject

The `-cdrom` shorthand on x86 default machine becomes `ide1-cd0`:

```python
blocks = await qmp.execute("query-block")
cd = next(b for b in blocks if b.get("type") == "cdrom" or "cd" in b["device"])
```

The `device` argument of `eject` is **deprecated since QEMU 2.8** ([deprecated features](https://www.qemu.org/docs/master/about/deprecated.html)) — use `id`:

```python
await qmp.execute("eject", {"id": cd["qdev"], "force": True})
```

If you used short-form `-cdrom`, `qdev` may be a QOM path rather than a friendly id. **For automation, switch to long form** so eject by id is stable:

```
-drive file=alpine.iso,if=none,id=cd0,media=cdrom
-device ide-cd,drive=cd0,id=cd0
```

Then `eject {"id": "cd0", "force": true}` is deterministic.

## send-key

Multi-key sequences (slow but works headlessly):

```python
await qmp.execute("send-key", {
    "keys": [
        {"type": "qcode", "data": "tab"},
        {"type": "qcode", "data": "ret"}
    ],
    "hold-time": 100
})
```

Fine for one-off "press Enter at the bootloader" cases; do not use for typing long strings.

## Lifecycle pattern (recommended)

1. Launch QEMU with `-no-reboot` + `-serial file:install.log` (or unix socket if you want bidirectional input).
2. Watch QMP for `SHUTDOWN` event with `guest: true` (or watch serial for installer's done marker).
3. With `-no-reboot`, QEMU exits when the installer issues guest reboot.
4. Relaunch QEMU **without** the install ISO drive.
5. Poll SSH on the hostfwd port for readiness.

## CD-eject after install: pick `-no-reboot`

Two options:

| | (a) QMP eject + `system_reset` | (b) `-no-reboot` + relaunch |
|---|---|---|
| Process model | One QEMU process across full lifecycle | Two sequential QEMU launches |
| Risk | Installer may have already rebooted into CD; need to toggle boot order | None — installer signals reboot, QEMU exits cleanly |
| Force-eject needed? | Sometimes, if guest hasn't unmounted | No |
| **Recommendation** | | **Use this.** Simpler, deterministic, matches both Alpine and Ubuntu installer behavior. |

## `-no-reboot` semantics

- **Guest reboot/reset** with `-no-reboot`: QEMU exits cleanly.
- **Guest poweroff**: QEMU exits regardless of `-no-reboot`.
- `subsystem-reset` ignores `-no-reboot` (modern equivalent: `-action reboot=shutdown`).
- `-no-shutdown` is orthogonal — pauses-rather-than-exits on poweroff (useful for state inspection; not what you want here).

Both Alpine `setup-alpine` (via apkovl autorun script `reboot` call) and Ubuntu autoinstall do a real `reboot()`, so `-no-reboot` catches them.

## Detecting install completion

Three signals, ordered by reliability:

1. **QMP `SHUTDOWN` event** with `guest: true` — fired when guest issues reboot or poweroff. Pair with `-no-reboot` to also get process exit. Event names per QMP reference: `SHUTDOWN` and `RESET` (uppercase).
2. **Serial console marker matching** — read serial log/socket for installer-specific strings. Alpine ends with `reboot`; Ubuntu autoinstall logs `subiquity/Reboot/start`.
3. **SSH polling** — only meaningful **after** second-stage boot. Use as readiness gate, not install-done gate.

## Serial console wiring

QEMU's serial chardev is fully bidirectional — host writes go to guest `/dev/ttyS0`, guest writes come back. **This is the text equivalent of VNC** and is what makes stock-ISO unattended installs feasible (see [Alpine unattended install](alpine-unattended.md)).

### Backend options

| Flag | Direction | When to use |
|---|---|---|
| `-serial file:install.log` | output only | Simplest, append-only. Tail from Python for passive observation. |
| `-serial unix:/tmp/serial.sock,server=on,wait=on,reconnect-ms=1000` | bidirectional | Both read AND inject input. **Recommended for serial-driven installs.** |
| `-serial pty` | bidirectional | QEMU allocates a pty and prints its path on stderr. `pexpect.spawn(...)` works directly. |
| `-serial mon:stdio` | bidirectional | Multiplex serial with HMP monitor on stdin/stdout. Manual debugging only. |

### Connect-before-boot — `wait=on,reconnect-ms`

`server=on,wait=on` blocks QEMU launch until a client connects to the socket — **use this for install scripts** so the Python control process doesn't miss bootloader output. Spawn QEMU; immediately connect from Python; QEMU then proceeds with kernel boot.

`reconnect-ms=1000` keeps the chardev alive if the host process drops. Without it, the guest blocks on the next write to ttyS0 once kernel buffers fill — survivable for short installs, fatal for long ones. **Always set `reconnect-ms`** on long-running installs.

### pexpect over Unix socket

Use `pexpect.fdpexpect.fdspawn` on a connected `AF_UNIX` socket:

```python
import socket
import pexpect.fdpexpect

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("/tmp/serial.sock")
c = pexpect.fdpexpect.fdspawn(s, timeout=120, encoding="utf-8")

c.expect("localhost login: ")
c.sendline("root")
```

Alternatively, bridge unix-socket → pty with `socat UNIX-CONNECT:/tmp/serial.sock,rawer PTY,link=/tmp/serial.pty` and use plain `pexpect.spawn` on the pty. Reference: [pexpect SocketSpawn / fdpexpect](https://pexpect.readthedocs.io/en/latest/api/socket_pexpect.html).

### Gotchas

- **`child.delaybeforesend = 0.05`** — avoids racing the guest's line discipline on long pasted commands.
- **`stty cols 200`** as the first command after login — disarms BusyBox getty's default 80-col wrap that can break regex matching.
- **Anchor regex on stable substrings** (`r":~# "`, `r"login: "`) — not full lines.
- **Wrap each `expect()` with a panic-grep alternation** (`Kernel panic|Call Trace|exception`) — crashes fail loudly instead of hanging.
- **Alpine virt ISO** ships with `console=tty0 console=ttyS0,115200` on its syslinux cmdline → full boot output on ttyS0 by default.
- **Ubuntu live-server** does NOT route to ttyS0 by default. Add `console=ttyS0,115200n8` to kernel cmdline (via `-kernel`/`-initrd`/`-append`) for serial-driven observation.

## Sources

- [QEMU QMP Reference](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html)
- [QEMU Invocation docs](https://www.qemu.org/docs/master/system/invocation.html)
- [QEMU Deprecated features](https://www.qemu.org/docs/master/about/deprecated.html) — `eject` `device` arg deprecated since 2.8
- [qemu.qmp on PyPI](https://pypi.org/project/qemu.qmp/)
- [python-qemu-qmp docs](https://qemu.readthedocs.io/projects/python-qemu-qmp/en/latest/)
- [python-qemu-qmp source](https://gitlab.com/qemu-project/python-qemu-qmp)
- [pexpect SocketSpawn / fdpexpect docs](https://pexpect.readthedocs.io/en/latest/api/socket_pexpect.html)
- [QEMU manpage — `-serial`, `-chardev`](https://qemu.readthedocs.io/en/master/system/qemu-manpage.html)
