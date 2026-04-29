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

| Flag | When to use |
|---|---|
| `-serial file:install.log` | Simplest, append-only. Tail from Python. |
| `-serial unix:/tmp/serial.sock,server=on,wait=off` | Bidirectional. Both read AND inject input (e.g. press Enter at syslinux prompt). Recommended for any interactive bootloader step. |
| `-serial mon:stdio` | Multiplex serial with HMP monitor on stdin/stdout. Good for manual debugging, not automation. |

**Alpine virt ISO** ships with `console=tty0 console=ttyS0,115200` on kernel cmdline → full boot output on ttyS0 by default.

**Ubuntu live-server** does NOT route to ttyS0 by default. Add `console=ttyS0,115200n8` to kernel cmdline (via `-kernel`/`-initrd`/`-append`) for serial-driven observation.

## Sources

- [QEMU QMP Reference](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html)
- [QEMU Invocation docs](https://www.qemu.org/docs/master/system/invocation.html)
- [QEMU Deprecated features](https://www.qemu.org/docs/master/about/deprecated.html) — `eject` `device` arg deprecated since 2.8
- [qemu.qmp on PyPI](https://pypi.org/project/qemu.qmp/)
- [python-qemu-qmp docs](https://qemu.readthedocs.io/projects/python-qemu-qmp/en/latest/)
- [python-qemu-qmp source](https://gitlab.com/qemu-project/python-qemu-qmp)
