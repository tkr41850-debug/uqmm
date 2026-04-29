# Phase 4 — Lifecycle commands

End state: every command in [../../design/cli.md § Commands](../../design/cli.md#commands) works against created VMs. Status discovery, port reuse, graceful shutdown, ssh exec, log tailing.

Anchors: [../../design/cli.md](../../design/cli.md), [../../design/config.md § Lifecycle layer](../../design/config.md#lifecycle-layer-fully-shared).

## Deliverables

- `uqmm.discover` — central status probe (pidfile → QMP → SSH banner cascade).
- `uqmm.cli` — fleshed-out `start`, `stop`, `delete`, `status`, `list`, `ssh`, `log`.

## Step 1 — `uqmm.discover`

`src/uqmm/discover.py`:

- `Status = Literal["not-created", "stopped", "starting", "running", "unreachable", "failed"]`.
- `async def probe(vm_dir: Path) -> Status`:
  1. If `vm_dir / "config.json"` doesn't exist → `not-created`.
  2. Load config; if `cfg.state == "failed"` → `failed`.
  3. If no `qemu.pid` → `stopped`.
  4. Read PID; `os.kill(pid, 0)` raises `ProcessLookupError` → clean stale pidfile → `stopped`.
  5. Try `qmp.connect(qmp.sock, timeout=1.0)` — fails → `starting`.
  6. Try `socket.create_connection(("127.0.0.1", cfg.ssh_port), timeout=1.0)` + read 16 bytes; starts with `b"SSH-"` → `running`; else → `unreachable`.

**Tests** (`tests/test_discover.py`) — table-driven, mocking each layer:

- Each branch covers the matching status.
- Stale pidfile (PID dead) is cleaned up.

**Commit:** `feat(discover): status probe state machine`

## Step 2 — `uqmm status [<name>]` and `uqmm list`

`src/uqmm/cli.py`:

- `status <name>` — single probe, print `<status>` (and `<port>` if running). Without `<name>`, walk all VM dirs.
- `list` — same data, formatted as a `rich.table.Table` with columns `name`, `os/version`, `status`, `ssh-port`.

**Tests**:

- `main(["status"])` with zero VMs → prints "no VMs"; exit 0.
- `main(["status", "vm1"])` → mocked probe returns `running` → stdout contains `running`.
- `main(["list"])` → table rendered (capture stdout via pytest `capsys`).

**Commit:** `feat(cli): status + list`

## Step 3 — `uqmm start <name> [--wait]`

`src/uqmm/cli.py`:

- Load `cfg = VMConfig.load(vm_dir / "config.json")`.
- Refuse if `cfg.state == "failed"` (per [../../design/cli.md § Errors during create](../../design/cli.md#errors-during-create)).
- Refuse if already running (`probe` returns `running` / `starting` / `unreachable`).
- Build runtime args (re-run the appropriate builder's `build()`; or store args in config.json — pick the former, simpler since builders are pure given cfg + vm_dir).
- `await process.launch(...)`.
- If `--wait`: `await ssh.wait_ready(...)` then return.
- Else: return immediately after the process is launched.

**Tests**:

- `main(["start", "vm1"])` → success path mocks process + builder.
- `--wait` waits for SSH; without `--wait`, returns before SSH check.
- VM in `running` state → exits non-zero with clear message.

**Commit:** `feat(cli): start with --wait`

## Step 4 — `uqmm stop <name> [--force]`

`src/uqmm/cli.py`:

- Load cfg, probe.
- Not running → exit 0 (idempotent — matches Docker `stop` semantics for an already-stopped container).
- `--force` → `qmp.connect`, `qmp.quit(client)`, kill via SIGKILL if QMP itself is unreachable.
- Default: `qmp.connect`, `qmp.system_powerdown(client)`, `await wait_exited(proc, timeout=30)`. On timeout, escalate to `qmp.quit`. Then SIGKILL fallback.
- Remove `qemu.pid`, `qmp.sock`, `serial.sock` (the latter two are auto-removed by QEMU on clean exit; remove anyway as cleanup).

**Tests**:

- Graceful path: powerdown command sent; process exits within deadline.
- Timeout escalation: powerdown sent; deadline hit; quit sent.
- `--force`: quit sent immediately, no powerdown.

**Commit:** `feat(cli): stop with graceful + force paths`

## Step 5 — `uqmm delete <name>`

`src/uqmm/cli.py`:

- If running: invoke `stop` logic.
- `shutil.rmtree(vm_dir)`.
- Port frees naturally (no port file; allocator just won't see it next time).

**Tests**:

- Running VM is stopped first, then directory removed.
- Already-stopped VM → directory removed.
- Non-existent VM → exit 0 with message (or non-zero — pick non-zero, matches `not-created`).

**Commit:** `feat(cli): delete (stop + rmtree)`

## Step 6 — `uqmm ssh <name> [-- ssh-args...]`

`src/uqmm/cli.py`:

- Load cfg. Refuse if not running (probe).
- Build argv: `["ssh", "-p", str(cfg.ssh_port), "-o", "StrictHostKeyChecking=accept-new", f"{cfg.user}@127.0.0.1", *passthrough]`.
- `os.execvp("ssh", argv)` — replaces process; never returns on success.

**Tests** — monkeypatch `os.execvp` to a spy; assert argv:

- No passthrough → exec'd with the base args.
- Passthrough `-- uname -a` → appended after the host argument.
- Cyclopts handles `--` separation correctly; if not, file an issue and switch to `cmd: list[str] = Argument(nargs=-1)` style.

**Commit:** `feat(cli): ssh via os.execvp`

## Step 7 — `uqmm log <name> [--follow]`

`src/uqmm/cli.py`:

- Print contents of `vm_dir / "install.log"`. If `--follow`, tail-follow with `loop.run_in_executor` reading appends; exit on Ctrl-C (KeyboardInterrupt).

**Tests**:

- Print path: stdout matches log contents.
- `--follow`: appended bytes appear in stdout (use a tmp log + a writer thread).

**Commit:** `feat(cli): log with --follow`

## Step 8 — Phase close-out

- Full lifecycle smoke (manual or scripted): `create deb13` → `status deb13` → `ssh deb13 uname -a` → `stop deb13` → `start deb13 --wait` → `delete deb13`. Repeat for `al321`.
- `uv run pytest` (default) green; with `UQMM_E2E=1` green (this validates lifecycle on real VMs end-to-end).
- Subagent review: "does the implementation cover every command and state in [../../design/cli.md](../../design/cli.md)? any commands or flags missing?"
- Tag a `v0.1.0` release once green.
