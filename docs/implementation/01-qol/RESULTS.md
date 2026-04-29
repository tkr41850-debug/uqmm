# 01-qol pass — results

Five phases shipped end-to-end on `main`. All 164 tests pass; ruff and basedpyright are clean.

## Phase 1 — State I/O robustness (C8, C9, C10, P10)

Atomic `config.json` and `qemu.pid` writes using tmp+rename. Corrupt config no longer crashes `list`/`status` — it renders as `invalid-config` in the table and is skipped in port scanning. `read_occupied_ports` fails closed (raises) on corrupt configs to prevent silent double-assignment.

## Phase 2 — Launch and startup correctness (C5, C6, R3, I8, P1, P3)

`start` now validates that `disk.qcow2` and `seed.iso` exist before launching QEMU (C5). `_wait_ssh_or_exit` races SSH readiness against process exit so a dead QEMU fails fast instead of timing out (C6). `prepare_disk` writes to a `.tmp` sidecar and renames atomically so a failed resize doesn't leave a partial overlay (R3). Alpine install raises vcpus/memory to ≥4/≥4096 and prints a notice when it does so (I8). `start` now accepts `unreachable` VMs (P1). Cloud-image runtime args no longer include `-no-reboot` so `sudo reboot` works (P3).

## Phase 3 — Input validation polish (I1, I9, I10, I12, I15)

Unknown Alpine versions produce a "did you mean X.Y?" hint derived from the known-versions table (I1). `--vcpus` is range-checked (1-256) (I9). Explicit `--ssh-port` is bind-probed so the error surfaces immediately at create time rather than at QEMU launch (I10). Missing `--key` paths produce a clear error (I12). VM names are validated against `[A-Za-z0-9._-]+` with length cap and no leading `.`/`-` (I15).

## Phase 4 — Re-runnable create (R14, R5, R1)

`create` is now idempotent when re-run on a matching already-created VM (R14). A per-VM `create.lock` (fcntl flock) disambiguates concurrent vs stale in-progress state: a held lock means "another process is running create", a lockable lock means "stale crash — resume as failed" (R5, R6). Ctrl-C before any disk work is done no longer leaves `state=failed` — the marker is only written after real work starts (R1). The `_handle_existing_vm_dir` state machine routes: created+match → idempotent success; created+diff → refuse; creating/failed+lockable+disk-match → resume; creating/failed+lockable+disk-diff → refuse.

## Phase 5 — Alpine install resumability (R10, R11, R12)

Two marker files in `vm_dir/` checkpoint the Alpine install flow: `state.seeded` (disk blank + answers written) and `state.installed` (installer QEMU exited cleanly — OS on disk). Both survive failures and are removed only when `state=created` is saved. The resume routing table: `installed` present → skip install, relaunch runtime; `seeded` present → regenerate answers via `rebuild_seed()`, reuse disk, re-run install; no markers → full build. Changing keys/user/hostname when `installed` is present is refused with a hint that setup-alpine has baked the old credentials into the disk. `_create_alpine` was refactored into `_run_alpine_install` + `_run_alpine_runtime` to make the resume path call the right subset.

## Issues not addressed in this pass

- **R2, R9**: Hard-kill safety (crash before config saved or after QEMU launch). Needs durable creating-state + repair command.
- **R4, R7, R8, I11**: Port allocation TOCTOU. SLiRP doesn't support fd-passing; worst case is a QEMU launch failure on the retry.
- **R13**: Cloud-image guest reboot during first boot. C6 makes it fail fast; full fix would need replaying the boot sequence.
- **R15**: Seed regeneration for `created` VMs (= the `update` command, P12).
- **C1, C2**: The create lock doesn't block `stop`/`delete` during create. Needs a reader/writer or cooperative protocol.
- All `deferred` items in `docs/issues/README.md`.
