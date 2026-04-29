# Phase 3 — Alpine pexpect E2E

End state: `uv run uqmm create al321 --os alpine --version 3.21 --key ~/.ssh/id_ed25519.pub` boots stock Alpine ISO, drives `setup-alpine` via pexpect, reboots into the installed system, returns when SSH responds.

This is the most failure-prone phase — pexpect over a unix socket against a live serial console, plus `-no-reboot` + relaunch coordination. Plan for two or three pexpect-tuning iterations before it stabilizes.

Anchors: [../../design/config.md § AlpineSeedBuilder](../../design/config.md#alpineseedbuilder-stock-iso--serial-pexpect), [../../research/alpine-unattended.md](../../research/alpine-unattended.md), [../../research/qemu-control.md](../../research/qemu-control.md), [../../design/toolchain.md](../../design/toolchain.md) (pexpect gotchas).

## Deliverables

- `uqmm.builders.alpine.AlpineSeedBuilder` — answers file rendering + install/runtime args.
- `uqmm.serve` — one-shot HTTP server serving `answers` at `http://10.0.2.2:<port>/answers`.
- `uqmm.qemu.serial` — pexpect over a connected unix-socket FD via `SocketSpawn`.
- `uqmm.alpine_drive` — the actual install state machine (expect / sendline pairs).
- `uqmm.cli.create` — alpine branch wired: launch QEMU, drive install, wait for SHUTDOWN, relaunch runtime, wait SSH.

## Step 1 — Answers file rendering

`src/uqmm/builders/alpine.py`:

- `render_answers(cfg) -> str` — emits a setup-alpine answers file. Required vars per [../../research/alpine-unattended.md](../../research/alpine-unattended.md):
  - `KEYMAPOPTS="us us"`
  - `HOSTNAMEOPTS="-n <hostname>"`
  - `INTERFACESOPTS="..."` — DHCP on eth0
  - `DNSOPTS="..."`, `TIMEZONEOPTS="-z UTC"`, `PROXYOPTS="none"`, `APKREPOSOPTS="-1"`
  - `USEROPTS="-a -u -g 'wheel,audio,video' <user>"`, `USERSSHKEY="<concatenated keys>"`
  - `SSHDOPTS="-c openssh"`, `NTPOPTS="-c chrony"`
  - `DISKOPTS="-m sys -s 0 /dev/vda"`
  - `LBUOPTS="none"`, `APKCACHEOPTS="none"`
- Concatenate `ssh_authorized_keys` with newlines into a single string.

**Tests** (`tests/test_alpine_answers.py`):

- Snapshot of the rendered file against an expected string for a known config.
- Multi-key list joins correctly.
- Hostname uses `cfg.hostname or cfg.name`.

**Commit:** `feat(builders/alpine): answers file rendering`

## Step 2 — One-shot HTTP server for answers

`src/uqmm/serve.py`:

- `serve_answers_once(content: str) -> tuple[int, threading.Thread]`:
  - Bind to `0.0.0.0:0` (so guest at `10.0.2.2` reaches us through SLiRP); pick a free port.
  - Use `http.server.ThreadingHTTPServer` with a custom handler that responds 200 to `GET /answers` and shuts the server down after one successful response.
  - Run `serve_forever` in a daemon thread; return port + thread handle.
  - Caller uses `thread.join(timeout=...)` to wait for the request.

**Tests** (`tests/test_serve.py`):

- Spin up the server, GET `http://127.0.0.1:<port>/answers`, assert body matches.
- Server stops after one request (second connection refused).

**Commit:** `feat(serve): one-shot HTTP answers delivery`

## Step 3 — `AlpineSeedBuilder.build`

Add to `alpine.py`:

- `class AlpineSeedBuilder: def build(self, cfg, vm_dir) -> InstallArtifacts:`
  - Resolve image (the ISO).
  - Create blank disk: `qemu-img create -f qcow2 <vm_dir>/disk.qcow2 <disk_size_gb>G`.
  - Render answers file; write `<vm_dir>/answers` (debug artifact).
  - `qemu_install_args`:
    - `-cdrom <iso> -boot d`
    - `-drive file=<vm_dir>/disk.qcow2,if=virtio,format=qcow2`
    - `-machine q35 -m <memory_mb> -smp <vcpus>` — bumped to ≥4096 / ≥4 if config is below those, per [../../design/config.md § VMConfig](../../design/config.md#vmconfig).
    - `-nographic -no-reboot`
    - `-netdev user,id=net0,hostfwd=tcp:127.0.0.1:<ssh_port>-:22`
    - `-device virtio-net-pci,netdev=net0`
    - `-qmp unix:<vm_dir>/qmp.sock,server=on,wait=off`
    - `-serial unix:<vm_dir>/serial.sock,server=on,wait=on,reconnect-ms=1000`
  - `qemu_runtime_args`: same minus `-cdrom`/`-boot d`/`-no-reboot`.

**Tests** (`tests/test_alpine_build.py`):

- Args contain `-cdrom`, `-no-reboot`, serial socket; runtime args do not.
- Memory/vcpu auto-bump kicks in below thresholds.

**Commit:** `feat(builders/alpine): build() with install + runtime args`

## Step 4 — `uqmm.qemu.serial`

`src/uqmm/qemu/serial.py`:

- `async def open_serial(sock: Path, timeout: float = 30.0) -> SocketSpawn`:
  - Connect a stdlib `socket` to the unix socket (retry until QEMU is listening).
  - Wrap with `pexpect.socket_pexpect.SocketSpawn(sock, timeout=...)` (per [../../design/toolchain.md](../../design/toolchain.md) — `SocketSpawn`, not `fdpexpect`).
  - Set `logfile_read = open(<vm_dir>/install.log, "ab")` so all serial output is captured.
- Wrap pexpect calls in a thin sync class so callers can `expect`, `sendline`, `expect_exact`. The class is sync because pexpect is sync; the *outer* coordination is async (run the drive script in `loop.run_in_executor`).

**Tests** (`tests/test_serial.py`):

- Spin up a unix socket server in a thread that scripted-replies; assert `expect`/`sendline` pairs work.

**Commit:** `feat(qemu/serial): SocketSpawn-based serial driver`

## Step 5 — Alpine drive script (the state machine)

`src/uqmm/alpine_drive.py`:

```python
def drive_install(serial: SocketSpawn, answers_url: str, root_password: str | None = None) -> None:
    serial.expect(r"localhost login: ", timeout=180)
    serial.sendline("root")
    serial.expect(r"# ", timeout=10)
    serial.sendline(f"wget -qO /tmp/answers {answers_url} && echo WGET_OK")
    serial.expect(r"WGET_OK", timeout=30)
    serial.sendline("setup-alpine -ef /tmp/answers")
    # password prompt(s)
    serial.expect(r"New password: ", timeout=300)
    serial.sendline(root_password or "uqmm-disposable")
    serial.expect(r"Retype password: ", timeout=10)
    serial.sendline(root_password or "uqmm-disposable")
    # wait for installer to complete
    serial.expect(r"Installation is complete", timeout=600)
    serial.sendline("poweroff")
    # let -no-reboot + SHUTDOWN handle the rest at the orchestration layer
```

The exact prompts are wiki-documented but in practice need adjusting against real output. Iterate.

**Tests** (`tests/test_alpine_drive.py`):

- Feed canned serial transcripts to a fake spawn; assert correct sendline calls.
- Timeout on missing prompt raises a clear error mentioning what was being awaited.

**Commit:** `feat(alpine_drive): pexpect install state machine`

## Step 6 — Wire `uqmm create` for alpine

`src/uqmm/cli.py` — alpine branch:

1. Build cfg, allocate port, make vm_dir, dispatch to `AlpineSeedBuilder.build`.
2. Render answers; start `serve_answers_once(content)` → get port.
3. `await process.launch(artifacts.qemu_install_args, ...)`.
4. `await qmp.connect(<vm_dir>/qmp.sock)`.
5. Open serial (via executor); run `drive_install(...)` in `loop.run_in_executor`.
6. Wait for `qmp.wait_shutdown(client)` (set after drive_install issues `poweroff`).
7. `await process.wait_exited(proc)` — `-no-reboot` causes QEMU to exit after guest poweroff.
8. Relaunch with `qemu_runtime_args`.
9. `await ssh.wait_ready(...)`.
10. Persist config.json. Done.

Failure handling same as cloud-image: mark `state: "failed"`, leave directory.

**Tests** (`tests/test_create_alpine.py`):

- Integration via `main([...])` with all boundaries mocked. Verify the call sequence (start server → launch QEMU → drive install → wait shutdown → relaunch → wait ssh → save config).
- pexpect timeout in `drive_install` → cfg.state = `failed`, dir left intact.

**Commit:** `feat(cli/create): wire alpine branch end-to-end`

## Step 7 — Optional E2E smoke

`tests/test_e2e_alpine.py`, `UQMM_E2E=1`-gated:

- Real `uqmm create al321 --os alpine --version 3.21 --key <tmp keypair>`. Expect 3–5 minute wall time (TCG, no KVM).
- `ssh -p <port> uqmm@127.0.0.1 cat /etc/alpine-release` returns `3.21.x`.

**Commit:** `test(e2e): Alpine smoke (UQMM_E2E=1)`

## Step 8 — Phase close-out

- E2E run locally must succeed at least once before merging the phase.
- Capture an `install.log` from a successful run and check it into `tests/fixtures/alpine-install.log` for use in pexpect unit-test transcripts (canonicalize timestamps first).
- Subagent review across phase: "does the implementation match [../../research/alpine-unattended.md § stock-ISO + serial pexpect](../../research/alpine-unattended.md)? any prompts in `drive_install` that the wiki/transcript suggests are wrong?"
