# Phase 5 — Alpine install resumability

Phase 4 makes cloud-image creates re-runnable from `failed`. Alpine has more internal checkpoints, so a more granular resume model pays off: a failure during the install run shouldn't force a re-install on retry, and a failure between install and runtime relaunch shouldn't lose the installed disk.

End state: `create alpine-vm` after a failure picks up at the right step — re-driving install only if the disk is still blank, relaunching runtime if the disk is installed but never reached SSH.

Depends on: phase 4. Builds on the `state="failed"` resume path; adds Alpine-specific sub-state.

Issues: **[R10](../../issues/retry.md), [R11](../../issues/retry.md), [R12](../../issues/retry.md)**.

Anchors: [../../design/config.md § AlpineSeedBuilder](../../design/config.md), `src/uqmm/builders/alpine.py`, `src/uqmm/cli.py:131-181`.

## Design overview

The Alpine create flow has three observable checkpoints:

```
mkdir → write disk + answers → launch installer (CD) → installer runs → installer reboot (QEMU exits) → relaunch runtime → SSH ready
                              ↑                       ↑                                              ↑
                              [seed-built]            [installer-running]                            [installed]
```

Today, any failure between `mkdir` and `SSH ready` produces `state="failed"` with no further detail. We can't tell whether the disk has a working Alpine on it without booting and probing.

Add a sub-state field to track this. Cheap and durable: write a marker file at each transition. Storing it in `config.json` would mean an extra save call at each transition (with phase-1 atomicity that's fine, but it's noisier than necessary).

Use marker files in `vm_dir/`:

| Marker | Written when | Means |
|---|---|---|
| `state.seeded` | After `build_disk` + `render_answers` succeed | Disk is blank, answers exist, no install attempted yet |
| `state.installed` | After installer QEMU exits cleanly (line `cli.py:154-159`) | OS is on disk, runtime relaunch hasn't happened yet |

(No `state.ssh-ready` — that's just `state="created"` in `config.json`.)

The phase-4 `failed`-state resume looks at these markers to decide where to pick up:

| Markers present | Phase-4 args match? | Action |
|---|---|---|
| (none, or only `seeded`) | yes | Re-drive install from scratch (disk reusable; answers regenerable) |
| `installed` | yes | Skip install entirely; relaunch runtime + wait SSH |
| any | no — disk-affecting | Refuse with diff (same as phase 4) |
| `installed` | no — seed-affecting (keys, hostname, user) | The seed has been consumed by setup-alpine. New keys won't take effect without re-installing. Refuse with diff + hint. |

The last row is the Alpine analog of R16 for cloud-image: once setup-alpine has run, the user account and SSH keys are baked into the disk. Document the limitation; phase 5 doesn't try to mutate the running guest.

## Step 1 — Add checkpoint markers to AlpineSeedBuilder + create flow (R10 prep)

`src/uqmm/builders/alpine.py:68-85` — `build()` writes disk + answers, then returns. After both writes succeed, also touch `vm_dir / "state.seeded"`. (Single empty marker file is enough; no contents.)

`src/uqmm/cli.py:131-172` — `_create_alpine`: after the install QEMU exits cleanly (line 155-159, after `await proc.wait()` and before the runtime relaunch), touch `vm_dir / "state.installed"`.

Crash-safe: marker files are touch-and-fsync, idempotent. If a previous run already touched them, re-touching is fine.

Don't introduce a `state.AlpineCheckpoints` enum yet — small functions like `vm_dir / "state.seeded"` keep the wiring obvious.

**Tests** (`tests/test_alpine_build.py`, `tests/test_create_alpine.py`) — test-first:

- `test_R10_build_writes_seeded_marker` — `AlpineSeedBuilder().build(cfg, vm_dir)`; assert `vm_dir / "state.seeded"` exists.
- `test_R10_create_writes_installed_marker_after_install_exit` — drive `_create_alpine` with mocked QEMU that exits cleanly; assert marker present before the runtime launch starts.
- `test_R10_marker_idempotent` — touch twice; no error.

**Commit:** `feat(builders/alpine,cli): checkpoint markers for install resumability (R10 prep)`

## Step 2 — Resume install from `seeded` (R10)

Wire the phase-4 resume path to inspect Alpine markers. `cli.create()` for `os == "alpine"`, when entering the resume branch from a `failed`/`creating` state:

1. If `state.installed` exists → jump to step 3 (runtime relaunch).
2. Else if `state.seeded` exists → reuse `disk.qcow2`, regenerate `answers` from current args (in case keys/hostname changed), re-touch the marker, run the install QEMU again. Note: re-running setup-alpine on a non-blank disk is fine; it'll partition/format/`apk add` from scratch.
3. Else → run the full `build()` (rare: should only happen if a previous run died before `build()` returned, which means no disk either).

For the regenerate-answers case, also detect when seed-affecting fields differ from saved config — if the user passed a new `--key`, regenerate; if they passed a new `--os` or `--version`, refuse (already covered by phase-4's disk-affecting field check).

**Tests** (`tests/test_create_alpine.py`) — test-first:

- `test_R10_resume_from_seeded_skips_build_disk` — pre-populate vm_dir with `state.seeded`, disk, answers; mock builder; assert `build_disk` is NOT called but install QEMU IS launched.
- `test_R10_resume_regenerates_answers_for_new_key` — pre-populate seeded; rerun with new `--key`; assert `answers` file contents changed.
- `test_R10_resume_full_rebuild_when_no_marker` — pre-populate vm_dir but no markers, no disk; resume builds disk fresh.

**Commit:** `feat(cli,builders/alpine): resume install from state.seeded (R10)`

## Step 3 — Resume runtime relaunch from `installed` (R11, R12)

Continuing in the resume path: when `state.installed` is present, skip everything before the runtime launch. The disk is fully provisioned; we just need to relaunch with `runtime_args()` and wait for SSH.

This addresses both R11 (interrupted between install exit and runtime relaunch — no work re-done) and R12 (SSH-wait timed out after install — just retry the wait without reinstalling).

In code: factor `_create_alpine` into `_run_alpine_install(cfg, vm_dir)` and `_run_alpine_runtime(cfg, vm_dir)`. The resume path calls `_run_alpine_runtime` directly when `state.installed` exists.

Note the answers HTTP server (`serve_answers_once`) is only needed for `_run_alpine_install`. The runtime path skips it entirely.

**Tests** (`tests/test_create_alpine.py`) — test-first:

- `test_R11_resume_from_installed_skips_install_qemu` — pre-populate vm_dir with disk + `state.installed`, no `state.seeded` even (or both — doesn't matter); mock; assert install QEMU NOT launched, runtime QEMU IS launched, answers server NOT started.
- `test_R12_resume_from_installed_retries_ssh_wait` — pre-populate as R11; mock SSH wait to fail-then-succeed across two runs; assert second create succeeds without reinstall.
- `test_R11_resume_from_installed_refuses_seed_change` — pre-populate installed; rerun with new `--key`; refuse (with hint that the disk has the old key baked in).

**Commit:** `feat(cli): resume runtime relaunch from state.installed (R11, R12)`

## Step 4 — Cleanup on `state="created"` and `delete`

Once the create finishes (state=created), the marker files are no longer informative — `state="created"` implies both `seeded` and `installed`. Two options:

- Leave them on disk (slightly noisy `ls`).
- Remove them in the success path.

Pick remove: keeps the directory clean, makes the resume logic's invariant clearer (markers are only meaningful while state != created). `_create_alpine` removes them after writing `state="created"` to config.

`uqmm delete` already calls `shutil.rmtree(vm_dir)` (`cli.py:355`), so no marker-specific cleanup is needed there.

**Tests** (`tests/test_create_alpine.py`) — test-first:

- `test_R10_markers_removed_on_success` — full happy-path mock; assert markers gone in success state.
- `test_R10_markers_kept_on_failure` — mock SSH-wait to fail; assert installed marker still present (for resume).

**Commit:** `chore(cli): remove alpine checkpoint markers on success`

## Step 5 — Update spec doc

`docs/design/config.md § AlpineSeedBuilder` and the new `docs/design/cli.md § create state machine` section (added in phase 4): document the marker files, their lifecycle, and the resume decision table from this phase.

Add a note about the seed-after-install limitation: once setup-alpine has run, key/user/hostname are on disk; resume with new credentials requires `delete`.

**Commit:** (folded into step 3 or its own `docs:` commit) `docs: alpine install checkpoint markers`

## Step 6 — Phase close-out

- `uv run pytest` — full suite green.
- `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check` clean.
- Flip R10, R11, R12 in [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status) from `planned` → `fixed`.
- Re-evaluate the broader retry catalog now that phases 4 + 5 are both in:
  - R13 (cloud-image guest reboot during first boot) — partially addressed by C6 fast-fail; full fix would need replaying the boot, deferred.
  - R15 (regenerate seed for hostname/key) — addressed for `failed` state; for `created` state it remains deferred (= P12 update command).
- Subagent review: focus on the install/runtime split — confirm `serve_answers_once` is only called in the install path, not the runtime path. Confirm the markers' invariant (created ⇒ no markers; failed-with-installed ⇒ disk works without setup-alpine).

End-of-pass: write a short note in `docs/implementation/01-qol/RESULTS.md` (one paragraph per phase, what shipped, what didn't, and any new issues uncovered during implementation). Update the issues catalog with anything new.
