# Phase 4 — Re-runnable `create`

The user-reported bug. Today, any `create` failure leaves the vm_dir behind and a retry — even with corrected args — errors with "VM directory already exists". Recovery requires `delete + create`, which throws away potentially reusable artifacts.

End state: `create` is state-aware. On a fresh directory, it works as today. On a `failed` directory, it can resume from the last good checkpoint, regenerating seed/answers from current args. On a `created` directory, it is idempotent if args match; informative if they don't.

Depends on: phase 1's atomic `config.json` writes (the `state` field becomes load-bearing across crash/retry boundaries).

Issues: **[R14](../../issues/retry.md), [R5](../../issues/retry.md), [R1](../../issues/retry.md)**.

Anchors: [../../design/cli.md § Errors during create](../../design/cli.md#errors-during-create), [../../design/config.md](../../design/config.md).

## Design overview

Today the state machine is `created` ↔ `failed`. Add `creating` as a third value. `create` writes `state="creating"` plus the resolved config to disk **before** any expensive work, then transitions to `created` on success, `failed` on a handled exception. A SIGKILL leaves the file in `creating`; recovery treats `creating` and `failed` the same way for resume purposes.

On a second `create <name>` run:

| Existing state | Args match saved config? | Action |
|---|---|---|
| (no vm_dir) | — | Today's flow: mkdir, save config with state=creating, build, launch. |
| `created` | yes | Idempotent success: print the SSH-ready line, exit 0. |
| `created` | no | Refuse with a diff and suggest `delete` (we don't reconfigure live VMs in this phase — that's P12 territory). |
| `creating` | — | Either a concurrent run or a crashed run. Use a lockfile to disambiguate. If the lock is held, refuse with "create already in progress". If stale, treat as `failed`. |
| `failed` | yes (or compatible) | Resume: regenerate seed/answers from current args, reuse `disk.qcow2` if present, relaunch. |
| `failed` | no — but only seed-affecting fields differ (keys, hostname, user) | Resume with new seed (the disk hasn't been booted yet for cloud-image; for Alpine, see phase 5). |
| `failed` | no — disk-affecting fields differ (disk_size_gb, image, version, os) | Refuse with a diff and suggest `delete`. |

This phase implements the flow up to the `failed` cloud-image resume case. Alpine-specific resume gets its own phase (5) because the install path has more checkpoints to think about.

## Step 1 — Add `creating` state and persist config early (R1, R5 prep)

`src/uqmm/config.py:14` — extend `State = Literal["created", "failed", "creating"]`.

`src/uqmm/cli.py:75-101` — restructure `create()`:

1. Validate args (already happens via I9/I12/I15).
2. `state.validate_vm_name(name)`.
3. Compute `cfg` with `state="creating"`.
4. If `vm_dir.exists()`: branch into the resume/idempotent decision logic (next steps).
5. Otherwise: `vm_dir.mkdir(parents=True)`, immediately `cfg.save(vm_dir / "config.json")` (state=creating), then proceed to `_create_cloudimg` / `_create_alpine`.

Update `_create_cloudimg` / `_create_alpine` to flip `cfg.state = "created"` only on success and `cfg.state = "failed"` on handled exceptions (today they only handle the failed side).

**This addresses R1 directly:** Ctrl-C during image download now leaves `state=creating`, not `state=failed`, distinguishing "user cancelled before any work" from "work happened and broke".

**Tests** (`tests/test_config.py`, `tests/test_create_cloudimg.py`, `tests/test_create_alpine.py`) — test-first:

- `test_R1_creating_state_round_trip` — `VMConfig(state="creating")` → `to_json` → `from_json` round-trips.
- `test_R1_create_writes_creating_before_build` — patch the builder to record file state at `build()` entry; assert `config.json` exists with state=creating.
- `test_R1_create_flips_to_created_on_success` — happy path; final state is `created`.
- `test_R1_create_flips_to_failed_on_handled_error` — patch builder to raise; final state is `failed`.

**Commit:** `feat(config,cli): persist creating state before expensive work (R1)`

## Step 2 — Lockfile for concurrent / crashed creates (R5)

Add `state.acquire_create_lock(vm_dir) -> contextlib.AbstractContextManager` using `fcntl.flock(LOCK_EX | LOCK_NB)` on `vm_dir / "create.lock"`. Returns a context manager; raises a sentinel `CreateInProgressError` if the lock is held.

In `cli.create()`: when an existing vm_dir has `state="creating"`, attempt the lock:
- Lock acquired → previous run was killed; treat as resumable (proceed to step 3 logic with `state="creating"` reinterpreted as `"failed"` for routing).
- Lock held → another process is running; print "create already in progress for <name>" and exit non-zero.

For fresh creates (step 1), acquire the lock immediately after mkdir and hold it for the whole flow. The lock file stays on disk after success/failure; flock state is per-process and doesn't outlive a crash, so the on-disk file's existence is meaningless and the OS-level lock state is what we check.

**Tests** (`tests/test_state.py`, `tests/test_cli.py`) — test-first:

- `test_R5_acquire_release_round_trip` — acquire, release; second acquire from same process succeeds.
- `test_R5_concurrent_acquire_raises` — fork a subprocess that holds the lock; main process's acquire raises CreateInProgressError. (Or use a thread + a real flock-on-fd if subprocess feels heavy.)
- `test_R5_create_refuses_with_creating_locked` — pre-create vm_dir + lock held by sibling; `main(["create", ...])` non-zero with the in-progress message.
- `test_R5_create_resumes_creating_when_unlocked` — pre-create vm_dir with state=creating, no lock; assert flow proceeds (step 3+).

**Commit:** `feat(state,cli): per-VM create lock + crashed-creating recovery (R5)`

## Step 3 — Idempotent success when args match (R14)

In `cli.create()`, when `vm_dir` exists with `state="created"`:

1. Load saved `VMConfig`.
2. Compute would-be config from current args.
3. Compare on the dimensions that materially differ for `create`: `os`, `version`, `image`, `vcpus`, `memory_mb`, `disk_size_gb`, `ssh_port`, `user`, `ssh_authorized_keys` (sorted), `hostname`. Don't compare `state`.
4. If equal: probe; if status is `running` and SSH responds, print the existing "ready" line and exit 0. If stopped/unreachable, exit with a hint to `uqmm start --wait` (don't auto-start — surprising).
5. If different: print a one-screen diff and exit non-zero with a hint to `uqmm delete && uqmm create`. Do not modify the existing VM.

`VMConfig.matches_create_args(other) -> bool` is the natural place for the comparison.

**Tests** (`tests/test_config.py`, `tests/test_cli.py`) — test-first:

- `test_R14_matches_create_args_equal` — two configs with same fields (different order in keys list) match.
- `test_R14_matches_create_args_state_ignored` — `state="created"` vs `state="failed"` doesn't break match.
- `test_R14_create_rerun_idempotent_when_match` — pre-populate created + running VM; `main(["create", ...])` exits 0 with the ready message; no extra QEMU launched.
- `test_R14_create_rerun_diffs_when_mismatch` — pre-populate with vcpus=2; rerun with vcpus=4; non-zero exit, output mentions `vcpus: 2 → 4` and `delete`.

**Commit:** `feat(cli): idempotent create when args match saved config (R14)`

## Step 4 — Resume from `failed` directory (R5 main case, addresses user bug)

In `cli.create()`, when `vm_dir` exists with `state="failed"` (or `"creating"` after step 2 reinterpretation):

1. Load saved `VMConfig` (best-effort — corrupt config = treat as fresh-but-mkdir-conflict).
2. If saved config exists, compare seed-affecting fields (keys, user, hostname) and disk-affecting fields (os, version, image, disk_size_gb) separately.
3. If disk-affecting fields differ → refuse with diff + delete hint (same as R14 mismatch path).
4. Otherwise → resume:
   - Cloud image: regenerate `seed.iso` from current args (which may have new keys/hostname). If `disk.qcow2` exists, reuse it. If not, run `prepare_disk` (this naturally covers the case where R3 cleaned up after a resize failure).
   - Alpine: regenerate `answers` from current args. Disk reuse — see phase 5.
5. Save config with `state="creating"`, then run the rest of the create flow.

The seed regeneration is safe for cloud-image only on first boot (R16 — the stable instance-id makes cloud-init skip user-data on a re-boot). Detect this: if `disk.qcow2` exists AND was previously booted (we don't track this directly today; proxy: `state="failed"` after the first builder.build() that includes a launch attempt). For now, naively regenerate the seed — phase 5 thinking applies more rigorously to Alpine; for cloud-image, a still-failing-on-first-boot VM hasn't consumed the seed.

**Tests** (`tests/test_create_cloudimg.py`, `tests/test_cli.py`) — test-first:

- `test_R5_resume_failed_regenerates_seed` — pre-populate failed VM with disk + seed; rerun with new --key; assert seed.iso changes, disk.qcow2 unchanged.
- `test_R5_resume_failed_rebuilds_missing_disk` — pre-populate failed VM with seed but no disk; rerun; disk recreated.
- `test_R5_disk_args_diff_refuses_resume` — pre-populate failed VM, rerun with different --version; non-zero exit, diff mentions version, no QEMU launched.
- `test_R5_resume_addresses_user_bug` — end-to-end: failed run from bad version, retry with good version → success. (Mock the QEMU + SSH; this is the headline regression test.)

**Commit:** `feat(cli): resume create from failed/creating state (R5)`

## Step 5 — Update spec doc

`docs/design/cli.md § Errors during `create`` says today: "uqmm start refuses on failed state — must delete and create again". Replace that section with the new state machine. Document:

- The `creating` state and the lockfile.
- The args-match decision tree.
- The disk-affecting vs seed-affecting field split.
- The R16 caveat: rerunning with new keys may not take effect if the guest already consumed the seed (cloud-init's stable instance-id). Phase 5 addresses Alpine; cloud-image users should `delete` if they need a fully clean re-cloud-init.

Same commit as the implementation, so design and code don't drift.

**Commit:** (folded into step 4) `docs(cli): document state-aware create retry`

## Step 6 — Phase close-out

- `uv run pytest` — full suite green; the headline test from step 4 (the user bug) is the regression marker.
- `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check` clean.
- Flip R14, R5, R1 in [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status) from `planned` → `fixed`.
- Subagent review: walk every state transition in the new state machine; confirm every path either advances state or releases the lock cleanly. Pay attention to the `KeyboardInterrupt` path — phase 1's atomic save should mean an interrupt mid-save doesn't tear the file, but verify.

Bump the catalog: R6 ("two simultaneous creates race past existence check") is now folded into the lockfile path — flip it to `fixed` too if the test in step 2 covers the race. Same for C1 / C2 if the lock makes `delete` and `stop` correctly observe in-progress creates (they should — the lock file is detectable even though `flock` state isn't, and a `stop`/`delete` during creating can refuse on lock-presence).

Re-evaluate R15 (regenerate seed for hostname/key changes): partially addressed — for the failed/creating resume case, R15 is fixed. For the `created` case it's still deferred (would require an `update` command — P12).
