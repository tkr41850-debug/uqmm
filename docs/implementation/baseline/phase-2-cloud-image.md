# Phase 2 — Cloud-image E2E (Debian + Ubuntu)

End state: `uv run uqmm create deb13 --os debian --version 13 --key ~/.ssh/id_ed25519.pub` provisions a Debian VM and returns when SSH responds. Same for Ubuntu (`--os ubuntu --version 24.04`).

Anchors: [../../design/config.md § CloudImageBuilder](../../design/config.md#cloudimagebuilder-debian--ubuntu-unified), [../../research/cloud-image.md](../../research/cloud-image.md), [../../research/qemu-control.md](../../research/qemu-control.md), [../../design/toolchain.md](../../design/toolchain.md) (CIDATA gotcha).

## Deliverables

- `uqmm.builders.base` — `SeedBuilder` Protocol + `InstallArtifacts` dataclass.
- `uqmm.builders.cloudimg.CloudImageBuilder` — qcow2 rebase + resize + cidata seed.
- `uqmm.qemu.process` — async subprocess launcher with stderr drain + pidfile.
- `uqmm.qemu.qmp` — thin async wrapper around `qemu.qmp.QMPClient`.
- `uqmm.ssh` — banner-poll readiness via `socket`.
- `uqmm.cli.create` — wired end-to-end for the cloud-image branch.

## Step 1 — Builder contract

`src/uqmm/builders/base.py`:

```python
@dataclass
class InstallArtifacts:
    qemu_install_args: list[str]
    qemu_runtime_args: list[str]
    seed_paths: list[Path]

class SeedBuilder(Protocol):
    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts: ...
```

No tests yet — protocol only.

**Commit:** `feat(builders/base): SeedBuilder protocol + InstallArtifacts`

## Step 2 — cidata user-data / meta-data rendering

`src/uqmm/builders/cloudimg.py` — start with the YAML rendering, no ISO yet.

- `render_user_data(cfg) -> str` — produces a `#cloud-config` document with `hostname`, `users:` (the configured `user`, sudo, ssh keys), `ssh_pwauth: false`, `package_update: false`, optional `runcmd` for first-boot polish.
- `render_meta_data(cfg) -> str` — `instance-id` (random), `local-hostname` (cfg.hostname or cfg.name).
- Use `pyyaml` (`safe_dump`, `default_flow_style=False`); prepend `#cloud-config\n`.

**Tests** (`tests/test_cloudimg_render.py`) — test-first, snapshot-style:

- user-data contains `users:` with the configured login, `ssh_authorized_keys`, sudo `ALL=(ALL) NOPASSWD:ALL`.
- meta-data contains `local-hostname: <cfg.hostname or cfg.name>`.
- `package_upgrade` is **not** set to `true` (slow on first boot; we don't need it).
- YAML is valid (re-parse round-trip).

**Commit:** `feat(builders/cloudimg): user-data/meta-data rendering`

## Step 3 — cidata seed.iso via pycdlib

Add to `cloudimg.py`:

- `build_seed_iso(user_data: str, meta_data: str, out: Path) -> None` — `pycdlib.PyCdlib`, `vol_ident="cidata"` (lowercase per [toolchain.md gotcha #2](../../design/toolchain.md#gotchas)), Joliet, add both files at root, write to `out`.

**Tests** (`tests/test_cloudimg_seed.py`):

- Build the ISO into a tmp path; re-open with `pycdlib`; both files are present and contents match.
- Volume ident is `cidata` (lowercase).

**Commit:** `feat(builders/cloudimg): cidata seed.iso via pycdlib`

## Step 4 — disk rebase + resize

Add to `cloudimg.py`:

- `prepare_disk(base: Path, out: Path, size_gb: int) -> None`:
  - `qemu-img create -f qcow2 -F qcow2 -b <base> <out>` (backing file)
  - `qemu-img resize <out> <size_gb>G`
- Subprocess via `subprocess.run` with `check=True`, capture stderr on failure into the raised exception message.

**Tests** (`tests/test_cloudimg_disk.py`):

- Mock `subprocess.run`; assert correct argv for create + resize.
- On non-zero exit, original stderr is included in the raised exception.

**Commit:** `feat(builders/cloudimg): disk rebase + resize`

## Step 5 — Wire `CloudImageBuilder.build`

Add to `cloudimg.py`:

- `class CloudImageBuilder: def build(self, cfg, vm_dir) -> InstallArtifacts:`
  - resolve image (calls `resolve.resolve_image(cfg)`)
  - prepare disk (`vm_dir/disk.qcow2`)
  - render seed YAML, write seed ISO (`vm_dir/seed.iso`)
  - return args:
    - `qemu_install_args == qemu_runtime_args` (no separate install for cloud-image)
    - common args: `-machine q35 -m <memory_mb> -smp <vcpus> -nographic -no-reboot -drive file=<vm_dir>/disk.qcow2,if=virtio -drive file=<vm_dir>/seed.iso,if=virtio,format=raw,readonly=on -netdev user,id=net0,hostfwd=tcp:127.0.0.1:<port>-:22 -device virtio-net-pci,netdev=net0 -qmp unix:<vm_dir>/qmp.sock,server=on,wait=off -serial unix:<vm_dir>/serial.sock,server=on,wait=off`
  - The `<port>` is filled in at launch time by the caller, not the builder. Builder returns args with a placeholder; CLI substitutes. (Or: builder takes the port as part of `cfg` after CLI has resolved it. Pick the latter — simpler.)

**Tests** (`tests/test_cloudimg_build.py`):

- `build(cfg, vm_dir)` returns `InstallArtifacts` with both arg lists identical, three seed paths (disk, seed iso, ... actually two — the "seed paths" tracked are files we created, not the source image).
- Args contain expected `-drive`, `-netdev`, `-qmp`, `-serial`.

**Commit:** `feat(builders/cloudimg): wire build() end-to-end`

## Step 6 — `uqmm.qemu.process`

`src/uqmm/qemu/process.py`:

- `async def launch(args: list[str], pidfile: Path, stderr_log: Path) -> asyncio.subprocess.Process`:
  - `asyncio.create_subprocess_exec(*args, stdout=DEVNULL, stderr=PIPE)`
  - Spawn a TaskGroup task that reads stderr line-by-line and appends to `stderr_log` (avoids the pipe-deadlock gotcha — [toolchain.md gotcha #5](../../design/toolchain.md#gotchas)).
  - Write `proc.pid` to `pidfile`.
  - Return the process handle.
- Caller is responsible for awaiting `proc.wait()` and removing the pidfile on exit.

**Tests** — light, mostly mock-driven:

- `launch` writes pidfile; stderr drain task reads from a fake pipe and writes to log file.

**Commit:** `feat(qemu/process): async launcher + stderr drain + pidfile`

## Step 7 — `uqmm.qemu.qmp`

`src/uqmm/qemu/qmp.py`:

- Thin wrapper around `qemu.qmp.QMPClient`:
  - `async def connect(sock: Path, timeout: float = 30.0) -> QMPClient` — retry-connect with backoff (the socket appears asynchronously after QEMU starts).
  - `async def system_powerdown(client) -> None`.
  - `async def quit(client) -> None`.
  - `async def wait_shutdown(client, timeout: float) -> bool` — listens for the `SHUTDOWN` event.
- Used by Alpine path (phase 3) and `stop` (phase 4); cloud-image phase only needs `connect` + `quit` for cleanup on failure.

**Tests** — mock `QMPClient`; verify command names and event handling logic.

**Commit:** `feat(qemu/qmp): QMPClient wrapper + lifecycle`

## Step 8 — `uqmm.ssh.banner`

`src/uqmm/ssh/__init__.py` (or `ssh.py` — keep flat unless we add more):

- `async def wait_ready(host: str, port: int, timeout: float = 300.0) -> None`:
  - In a loop until deadline: `await asyncio.open_connection(host, port)`; read up to 64 bytes; if starts with `b"SSH-"` → done. Else close + sleep 1s + retry.
  - Raise `TimeoutError` on deadline.

**Tests** (`tests/test_ssh_ready.py`):

- Mock the connection; serve `b"SSH-2.0-OpenSSH_9.6\r\n"` → returns successfully.
- Connection refused first three attempts then succeeds → returns successfully.
- Always-fail → raises `TimeoutError` once deadline hits (use a tiny timeout).

**Commit:** `feat(ssh): banner-poll readiness`

## Step 9 — Wire `uqmm create` for cloud-image

`src/uqmm/cli.py`:

- `create` command now does:
  1. Build `VMConfig` from args. If `ssh_port is None`, allocate via `state.pick_ssh_port`.
  2. Make `vm_dir = state.vm_dir(name)`. Refuse if already exists.
  3. Dispatch to `CloudImageBuilder().build(cfg, vm_dir)` (or `AlpineSeedBuilder` — but stub the alpine branch with `NotImplementedError("phase 3")` for now).
  4. `await process.launch(artifacts.qemu_install_args, ...)`.
  5. `await ssh.wait_ready("127.0.0.1", cfg.ssh_port)`.
  6. Persist `cfg.save(vm_dir / "config.json")`.
  7. Print success line; return 0.
- On any exception during 3–6: mark cfg.state = `failed`, save, leave `vm_dir` for diagnosis (per [cli.md § Errors during create](../../design/cli.md#errors-during-create)). Re-raise.

**Tests** (`tests/test_create_cloudimg.py`) — integration via `main(...)`:

- Mock `CloudImageBuilder.build`, `process.launch`, `ssh.wait_ready` (all happy path). Assert `config.json` written; success exit code.
- `ssh.wait_ready` raises `TimeoutError` → `config.json` shows `state: "failed"`; `vm_dir` not deleted.
- `vm_dir` exists already → exits non-zero before doing any work.

**Commit:** `feat(cli/create): wire cloud-image branch end-to-end`

## Step 10 — Optional E2E smoke

`tests/test_e2e_debian.py`, gated behind `pytest.mark.skipif(not os.getenv("UQMM_E2E"))`:

- Real `uqmm create deb13 --os debian --version 13 --key <generated tmp keypair>`.
- Assert `ssh -p <port> uqmm@127.0.0.1 hostname` returns `deb13` within 5 minutes.
- Cleanup: `uqmm delete deb13` — but `delete` isn't implemented yet, so this test will rely on phase 4. Defer the cleanup half until then; for now leave the VM dir in place and document the manual cleanup step.

**Commit:** `test(e2e): Debian cloud-image smoke (UQMM_E2E=1)`

## Step 11 — Phase close-out

- Run the gated E2E once locally to prove the path actually works (this is the moment of truth — TCG cloud-init boot should land in 30–90 s).
- `uv run pytest` (default; E2E skipped) green.
- `uv run basedpyright` clean.
- Subagent review across the phase: "does this implementation match [../../design/config.md § CloudImageBuilder](../../design/config.md#cloudimagebuilder-debian--ubuntu-unified)? any drift?"
