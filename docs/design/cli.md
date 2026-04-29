# CLI

uqmm command surface and on-disk state layout.

See [config.md](config.md) for `VMConfig` schema and [toolchain.md](toolchain.md) for library + Python version picks. This doc covers commands, state, and the discovery/lifecycle logic.

## Entry point

`uqmm/cli.py` exposes `main()` taking `argv` as a parameter so integration tests can drive the CLI without spawning a subprocess:

```python
import sys
from cyclopts import App

# version_flags=() disables cyclopts' built-in --version flag — without this,
# `create --version 3.21` is intercepted at the app level, the uqmm version
# is printed, and the subcommand never runs.
app = App(name="uqmm", version_flags=())

# @app.command def create(...): ...

def main(argv: list[str] | None = None) -> int:
    """Entry point. argv defaults to sys.argv[1:] when called via console-script."""
    return app(argv if argv is not None else sys.argv[1:])
```

The `[project.scripts]` entry `uqmm = "uqmm.cli:main"` calls `main()` with no args — falls through to `sys.argv`. Tests call `main(["create", "vm1", "--os", "alpine", ...])` directly and mock `subprocess`/`socket`/`pexpect` at the boundaries.

## Commands

| Command | Purpose |
|---|---|
| `create` | Provision + first boot until SSH-ready (Docker-style). |
| `start` | Boot an existing (already-created) VM. |
| `stop` | Graceful shutdown via QMP `system_powerdown`; `--force` for `quit`. |
| `delete` | Stop if running; remove VM directory; free port. |
| `status` | Per-VM state. Without `<name>`, shows all. |
| `list` | Tabular listing of all VMs. |
| `ssh` | Resolve port, exec `ssh` with passthrough args. |
| `log` | Print captured serial log; `--follow` tails. |

### `uqmm create <name>`

Docker-style: returns when cloud-init / setup-alpine has succeeded and SSH responds.

```
uqmm create <name> --os {alpine|debian|ubuntu} --version <v>
                   [--image PATH_OR_URL]
                   [--vcpus N] [--memory-mb N] [--disk-size-gb N]
                   [--ssh-port N] [--user U] [--key PATH]
                   [--hostname H]
```

Steps:

1. Resolve image: use `--image` if given (local file or URL); else look up `os + version` → canonical URL (see [research/cloud-image.md](../research/cloud-image.md), [research/alpine-unattended.md](../research/alpine-unattended.md)).
2. If URL, download to `$XDG_CACHE_HOME/uqmm/images/`. Reuse cached file if present.
3. Allocate VM directory at `$XDG_DATA_HOME/uqmm/vms/<name>/`. Error if it already exists.
4. Copy/qcow2-rebase the image to `disk.qcow2`; resize to `disk_size_gb`.
5. Build seed: CIDATA ISO (cloud-image path) or answer file + local HTTP server (Alpine path).
6. Allocate hostfwd port if `--ssh-port` not given.
7. Launch QEMU with `-no-reboot`, QMP socket, serial socket.
8. Alpine path: drive install via pexpect over serial; wait for QMP `SHUTDOWN`; relaunch without install drive.
9. Cloud-image path: poll SSH on hostfwd port; QEMU stays running.
10. Persist resolved config to `config.json`. Return success.

If any step fails, see [Errors during `create`](#errors-during-create) below.

### `uqmm start <name> [--wait]`

Boot an existing VM. Returns immediately by default; `--wait` blocks until SSH banner responds.

### `uqmm stop <name> [--force]`

Graceful by default: send `system_powerdown` via QMP, wait up to 30 s for QEMU process exit, escalate to QMP `quit` if the guest doesn't comply. `--force` skips graceful — `quit` immediately.

### `uqmm delete <name>`

Stops the VM if running, removes `$XDG_DATA_HOME/uqmm/vms/<name>/`, frees the port.

### `uqmm status [<name>]`

Per-VM state. Without `<name>`, shows all VMs.

| State | Meaning |
|---|---|
| `not-created` | No VM directory exists. |
| `stopped` | VM directory exists; no `qemu.pid` or PID is dead. |
| `starting` | PID alive; QMP socket not yet responding. |
| `running` | PID alive; QMP responds; SSH port answers SSH banner. |
| `unreachable` | PID alive; QMP responds; SSH does not answer. |
| `failed` | Provisioning failed during `create`; retry with `uqmm create` or `uqmm delete`. |
| `creating` | Create in progress (or crashed — probe treats it as `failed`). |
| `invalid-config` | `config.json` present but unreadable; VM needs manual inspection. |

### `uqmm list`

Tabular: `name`, `os/version`, `status`, `ssh-port`. Same data source as `status`.

### `uqmm ssh <name> [-- ssh-args...]`

Resolves port from state, then `os.execvp("ssh", argv)` so the user's TTY is attached directly to OpenSSH:

```sh
ssh -p <port> -o StrictHostKeyChecking=accept-new <user>@127.0.0.1 <ssh-args...>
```

`os.execvp` (not `subprocess.run`) replaces the uqmm process image with `ssh` — no Python in the chain once exec succeeds, so signals, terminal resize, and Ctrl-C behavior are identical to running `ssh` directly. `StrictHostKeyChecking=accept-new` auto-pins the host key on first connection without prompting; subsequent mismatches still error.

uqmm uses no Python SSH library — see [toolchain.md § No Python SSH library](toolchain.md#no-python-ssh-library) for rationale.

### `uqmm log <name> [--follow]`

Print the captured serial log: `install.log` from create + ongoing serial buffer if running. `--follow` tails like `tail -f` and exits on Ctrl-C.

## On-disk state

Following XDG Base Directory conventions:

```
$XDG_DATA_HOME/uqmm/                      (default ~/.local/share/uqmm/)
  vms/
    <name>/
      config.json                         serialized VMConfig (incl. resolved ssh_port + state field)
      disk.qcow2                          main disk
      seed.iso                            NoCloud cidata (cloud-image path); absent on Alpine path
      qmp.sock                            present only while running
      serial.sock                         present only while running
      qemu.pid                            PID of running qemu-system-*
      install.log                         captured serial output from create + later sessions

$XDG_CACHE_HOME/uqmm/                     (default ~/.cache/uqmm/)
  images/
    debian-13-genericcloud-amd64.qcow2    downloaded base images; re-fetchable
    noble-server-cloudimg-amd64.img
    alpine-virt-3.21.0-x86_64.iso
```

## Status discovery

Walk `$XDG_DATA_HOME/uqmm/vms/*/qemu.pid`. For each:

1. Read PID; check `os.kill(pid, 0)`. `ESRCH` → process dead → mark `stopped`, clean stale pidfile.
2. Process alive: connect to `qmp.sock`. No response → `starting`.
3. QMP responds: connect to `127.0.0.1:<ssh_port>`, read 64 bytes. Starts with `SSH-` → `running`. Otherwise → `unreachable`.

Cleaner than scanning the process table; survives PID reuse better.

## Port allocation

`create` picks an unused port in `22000-23000` if `--ssh-port` is not specified:

1. Read every existing VM's `config.json` and collect their `ssh_port` values.
2. For each candidate in `22000..23000`: skip if in use by another VM, or if `bind()` to `127.0.0.1:port` fails.
3. First success wins; persist to the new VM's `config.json`.

Port is sticky once recorded — `start` reuses it; `delete` frees it. No separate port-file.

## SSH key resolution

If no `--key` is passed, uqmm tries `~/.ssh/id_ed25519.pub`, then `~/.ssh/id_rsa.pub`. If neither exists, `create` errors with a hint to generate one or pass `--key`. Multiple `--key` flags accumulate.

## Errors during `create`

`config.json` carries a `state` field that tracks the create lifecycle:

| `state` | Meaning |
|---|---|
| `creating` | Create in progress (or crashed — see lockfile below). |
| `created` | Provisioning succeeded; VM is usable. |
| `failed` | Provisioning failed; VM directory kept for `uqmm log` diagnosis. |

### Fresh create

1. `vm_dir/` is created.
2. A per-process exclusive flock is acquired on `vm_dir/create.lock` for the entire create flow.
3. `config.json` is written immediately with `state="creating"`.
4. On success: `state` is flipped to `"created"`.
5. On failure (handled exception): `state` is flipped to `"failed"`; `uqmm log <name>` shows the serial output.

### Re-running `create <name>` (state machine)

| Existing state | Args match saved? | Action |
|---|---|---|
| (no vm_dir) | — | Fresh create. |
| `created` | yes | Idempotent success: print "already created", suggest `uqmm start --wait`. |
| `created` | no | Refuse with diff; suggest `uqmm delete <name> && uqmm create <name> …`. |
| `creating` | — | Try the flock. If held: "create already in progress" (concurrent process). If available: stale crash — resume as `failed`. |
| `failed` or `creating` (stale) | disk fields match | Resume: hold flock, regenerate seed from current args, reuse disk if present. |
| `failed` or `creating` (stale) | disk fields differ | Refuse with diff; suggest `uqmm delete`. |

Disk-affecting fields (prevent seed-only resume): `os`, `version`, `image`, `disk_size_gb`.

### Alpine resume sub-state (checkpoint markers)

For Alpine (`os == "alpine"`), the resume path inspects marker files in `vm_dir/` to decide where to pick up:

| Markers present | Seed fields changed? | Action |
|---|---|---|
| `state.installed` | no | Skip install entirely; relaunch runtime QEMU + wait SSH |
| `state.installed` | yes (keys/user/hostname) | Refuse: setup-alpine baked old credentials into disk; hint `delete` |
| `state.seeded` only | any | Regenerate `answers` via `rebuild_seed()` (reuses disk); run install QEMU |
| (none) | — | Full build: new disk + answers; full install |

Both markers are removed when `state = "created"` is saved. They survive failures so retries skip completed work without re-running setup-alpine on an already-installed disk.

See `docs/design/config.md § AlpineSeedBuilder § Checkpoint markers` for the full table and limitation notes.

### `uqmm start` on a failed VM

`start` refuses if `state == "failed"`. Use `uqmm create <name>` (resume) or `uqmm delete <name> && uqmm create <name>` to recover.
