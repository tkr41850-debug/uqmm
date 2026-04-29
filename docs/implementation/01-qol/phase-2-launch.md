# Phase 2 — Launch & startup correctness

Tighten the moment between "launch QEMU" and "tell the user it worked". Today the CLI happily reports success while QEMU is dying, sits 5 minutes waiting for a VM that already exited, leaves orphaned overlays after a `qemu-img resize` failure, and silently overrides Alpine install resources.

End state: `start` fails fast on missing artifacts or early QEMU death; cloud-image guests can `sudo reboot` without exiting; `start` works on `unreachable` VMs; failed cloud-image overlay creation is cleaned up; users see when Alpine bumps install resources.

Issues: **[C5](../../issues/concurrency.md), [C6](../../issues/concurrency.md), [R3](../../issues/retry.md), [I8](../../issues/config.md), [P1](../../issues/post-create.md), [P3](../../issues/post-create.md)**.

Anchors: [../../design/cli.md](../../design/cli.md), [../../design/config.md](../../design/config.md).

## Step 1 — Drop `-no-reboot` from runtime args (P3)

`src/uqmm/builders/cloudimg.py:165-195` — `_qemu_args()` is shared between install and runtime. `-no-reboot` is the right behavior during install (a guest reboot during cloud-init is almost always a config error and we want fast-fail) but wrong at runtime (a `sudo reboot` should reboot the VM, not stop it).

Split into `_qemu_install_args()` and `_qemu_runtime_args()` matching the Alpine builder's shape (`builders/alpine.py:120-151`). Install keeps `-no-reboot`; runtime drops it.

**Tests** (`tests/test_cloudimg_build.py`) — test-first:

- `test_P3_install_args_have_no_reboot` — assert `-no-reboot` in `qemu_install_args`.
- `test_P3_runtime_args_omit_no_reboot` — assert `-no-reboot` not in `qemu_runtime_args`.
- `test_P3_runtime_args_via_runtime_args_method` — `CloudImageBuilder().runtime_args(cfg, vm_dir)` does not contain `-no-reboot`.

**Commit:** `fix(builders/cloudimg): drop -no-reboot from runtime args (P3)`

## Step 2 — Resize-failure cleanup (R3)

`src/uqmm/builders/cloudimg.py:99-127` — `prepare_disk()` runs `qemu-img create` then `qemu-img resize`. If create succeeds and resize fails, `disk.qcow2` is left behind, and the user-reported bug then blocks retry.

Two-line fix: write to `out.with_suffix(out.suffix + ".tmp")`, run resize against that, then `os.replace(tmp, out)` only on success. Any failure unlinks the tmp file. The same pattern works for the Alpine builder's `build_disk()` (`builders/alpine.py:50-58`) — apply both for symmetry.

**Tests** (`tests/test_cloudimg_disk.py`, `tests/test_alpine_build.py`) — test-first:

- `test_R3_resize_failure_removes_partial_overlay` — patch the second `subprocess.run` (resize) to raise; assert no `disk.qcow2` exists at the target path after.
- `test_R3_create_failure_removes_partial_overlay` — same, patch the first call.
- `test_R3_alpine_create_failure_cleanup` — `build_disk()` mirror.

**Commit:** `fix(builders): clean up partial overlay on disk-prep failure (R3)`

## Step 3 — Preflight artifact check on `start` (C5)

`src/uqmm/cli.py:254-280` — `_start()` reconstructs runtime args without checking the files exist. If `disk.qcow2` was deleted out-of-band, QEMU exits immediately and `start` (without `--wait`) prints "started" before that's visible.

Add a preflight in both builders' `runtime_args()`: stat each path it would reference, raise `FileNotFoundError` with the missing path. `_start()` catches and converts to a CLI error.

**Tests** (`tests/test_cli.py`, `tests/test_cloudimg_build.py`, `tests/test_alpine_build.py`) — test-first:

- `test_C5_runtime_args_raises_on_missing_disk` — call `CloudImageBuilder().runtime_args()` with no disk.qcow2 in vm_dir; assert FileNotFoundError naming the path.
- `test_C5_runtime_args_raises_on_missing_seed_cloudimg` — same for seed.iso.
- `test_C5_start_reports_missing_artifact` — `main(["start", "foo"])` with deleted disk; non-zero exit, message names the missing file.

**Commit:** `fix(builders,cli): preflight runtime artifacts before launch (C5)`

## Step 4 — Race process exit against SSH wait (C6)

`src/uqmm/ssh.py:17-50` (`wait_ready`) and `src/uqmm/cli.py:104-126,131-181,254-280` (callers) — currently `wait_ready` only polls TCP. If QEMU dies, the CLI sits up to 300 s waiting on a port that will never open.

Refactor: lift the wait into `_wait_ssh_or_exit(proc, host, port)` that uses `asyncio.wait` to race two awaitables: the existing SSH wait and `proc.wait()`. Whichever completes first wins; if `proc.wait()` wins, raise a clear `RuntimeError("qemu exited with code N before SSH became ready; see install.log")`.

Apply at all four call sites: `_create_cloudimg`, `_create_alpine` (after install relaunch), `_start --wait`, and (newly) the post-launch health check from C5.

**Tests** (`tests/test_ssh.py`, `tests/test_cli.py`) — test-first:

- `test_C6_wait_returns_when_proc_exits_first` — fake proc that exits immediately; fake socket connect that hangs; assert raise within ~100ms (not 300s).
- `test_C6_wait_returns_when_ssh_ready_first` — opposite case; assert success.
- `test_C6_create_surfaces_qemu_exit_code` — full main(["create", ...]) with QEMU exiting code 1 mid-wait; assert non-zero exit, error mentions exit code.

**Commit:** `fix(ssh,cli): race proc.wait() against SSH readiness (C6)`

## Step 5 — Allow `start` on `unreachable` (P1)

`src/uqmm/cli.py:254-258` — `_start()` refuses three states: `starting`, `running`, `unreachable`. The first two are reasonable; `unreachable` (process alive, QMP up, SSH not answering) often means the VM has hung and the user wants to recover.

Change: refuse `starting` + `running` only. For `unreachable`, fall through to the launch path? No — the process is already running, we shouldn't double-launch. Instead: print a message suggesting `uqmm stop foo --force && uqmm start foo`, and exit with a distinct code. That's a UX-only change — clearer than "already unreachable; start it first".

Alternative considered: auto-stop+restart on `unreachable`. Rejected — too magical, hides what's happening, and may discard guest state.

**Tests** (`tests/test_cli.py`) — test-first:

- `test_P1_start_unreachable_suggests_force_stop` — patch `probe()` to return `unreachable`; assert message mentions `--force` and `stop`.
- `test_P1_start_running_unchanged` — still refuses with the existing message.

**Commit:** `fix(cli): suggest force-stop when start hits unreachable (P1)`

## Step 6 — Notice when Alpine install resources are bumped (I8)

`src/uqmm/builders/alpine.py:120-128` — `_qemu_install_args()` silently bumps vcpus to >=4 and memory to >=4096 during Alpine install. The runtime config uses the user's actual request, so a `--vcpus 1 --memory-mb 512` Alpine VM "succeeded" without ever running with those values.

Print a stderr notice from the builder when either bump kicks in: `note: alpine install raised vcpus 1 → 4 / memory 512MB → 4096MB; runtime will use your requested values`. Builder is the right layer because it knows the bump policy; phasing it through the config or CLI would leak that knowledge.

**Tests** (`tests/test_alpine_build.py`) — test-first:

- `test_I8_install_args_print_bump_notice` — capfd; assert message on stderr when vcpus < 4 or memory_mb < 4096.
- `test_I8_no_notice_when_at_or_above_minimum` — vcpus=4, memory_mb=4096 → silent.

**Commit:** `feat(builders/alpine): notice when install bumps resources (I8)`

## Step 7 — Phase close-out

- `uv run pytest` — full suite green.
- `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check` clean.
- Flip C5/C6/R3/I8/P1/P3 in [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status) from `planned` → `fixed`.
- Subagent diff review across the phase: focus on whether the C6 `_wait_ssh_or_exit` is plumbed through every caller.
