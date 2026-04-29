# Phase 1 — State I/O robustness

Make uqmm's two on-disk state files (`config.json`, `qemu.pid`) crash-safe and tolerant of corruption. Small, isolated, foundational — phase 4 leans on these.

End state: concurrent readers never see torn state; `uqmm list` survives a single corrupt config; the port allocator can't be tricked by an unreadable config into double-assignment.

Issues: **[C8](../../issues/concurrency.md), [C9](../../issues/concurrency.md), [C10](../../issues/concurrency.md), [P10](../../issues/post-create.md)**.

Anchors: [../../design/cli.md § Status discovery](../../design/cli.md#status-discovery), [../../design/cli.md § Port allocation](../../design/cli.md#port-allocation).

## Step 1 — Atomic config writes (C8)

`src/uqmm/config.py` — `VMConfig.save()` currently uses `Path.write_text()`, which truncates+writes in two syscalls. A reader hitting it mid-write sees an empty or partial file.

Replace with: write to `path.with_suffix(path.suffix + ".tmp")`, then `os.replace(tmp, path)`. `os.replace` is atomic on the same filesystem on POSIX.

**Tests** (`tests/test_config.py`) — test-first:

- `test_C8_save_atomic_via_tmp_rename` — patch `Path.write_text` to record call order; assert the temp path is written first, then renamed onto the final path. Final file has full content; temp is gone.
- `test_C8_save_does_not_truncate_on_failure` — pre-populate `config.json` with a known good blob; patch the temp-file write to raise; assert the final file still contains the good blob (not truncated).

**Commit:** `fix(config): atomic save via tmp + rename (C8)`

## Step 2 — Atomic pidfile writes (C9)

`src/uqmm/qemu/process.py` — same fix. `launch()` currently writes `qemu.pid` with `Path.write_text`. The bigger risk than torn JSON is that `discover.probe()` deletes the pidfile on parse failure (`discover.py:34-38`), so a mid-write read can lose a valid pidfile.

Tighten: same temp+rename. Also widen `probe()`'s parse-failure path to retry once after a 50ms sleep before unlinking — partial reads are typically resolved in microseconds.

**Tests** (`tests/test_qemu_process.py`, `tests/test_discover.py`) — test-first:

- `test_C9_pidfile_atomic_via_tmp_rename` — same shape as C8 test.
- `test_C9_probe_retries_partial_pidfile` — write an empty file, then patch `Path.read_text` to return empty once and the real PID on the second call; assert `probe()` does not unlink and returns the live state.

**Commit:** `fix(qemu/process,discover): atomic pidfile + retry-before-unlink (C9)`

## Step 3 — Fail closed on corrupt config in port allocator (C10)

`src/uqmm/state.py:46-59` — `read_occupied_ports()` swallows `ValueError`/`OSError` and skips the VM. That means a corrupt `config.json` makes the broken VM's port look free, and the next `create` can reassign it.

Two options:
1. Treat corrupt configs as a hard error — `read_occupied_ports()` raises, breaking allocation entirely until the user fixes it.
2. Treat them as occupying *all* ports they could plausibly be using — too pessimistic.

Pick option 1 with a useful message. Surfaces the corruption immediately rather than letting it metastasize into a port collision.

**Tests** (`tests/test_state.py`) — test-first:

- `test_C10_read_occupied_ports_raises_on_corrupt_config` — write a VM dir with `config.json` containing `{`; assert `read_occupied_ports()` raises with the VM name in the message.
- `test_C10_corrupt_config_blocks_create` — drive `main(["create", "bar", ...])` with a sibling corrupt config; assert non-zero exit and error message names the bad file.

**Commit:** `fix(state): fail closed on corrupt config in port allocator (C10)`

## Step 4 — Tolerate one corrupt config in `list` and `status` (P10)

`src/uqmm/cli.py:380-399` (`list_cmd`) and `discover.probe()` both call `VMConfig.load` uncaught. One bad config breaks every other VM's status display. That's worse UX than just skipping the broken VM in display contexts.

Wrap `VMConfig.load` in `list_cmd` and in any non-allocation use-site (`status_cmd`, `probe()` only when checking liveness, not for ssh_port lookup). Render broken VMs with status `invalid-config` and `?` for unknown fields.

The asymmetry with C10 is deliberate: allocator must fail closed; display can degrade gracefully.

**Tests** (`tests/test_cli.py`) — test-first:

- `test_P10_list_skips_corrupt_config_with_marker` — two VMs in a tmp data root, one with bad JSON; `main(["list"])` returns 0, output mentions both, the bad one shows `invalid-config`.
- `test_P10_status_named_corrupt_returns_invalid_config` — `main(["status", "broken"])` returns 0 (or a defined non-zero, choose one) and prints `invalid-config`.
- `test_P10_status_all_continues_past_corrupt` — `main(["status"])` doesn't crash on a broken sibling.

Add a new probe state `invalid-config` in `discover.py`'s `Status` literal; document it in [../../design/cli.md § status discovery](../../design/cli.md#status-discovery) in the same commit.

**Commit:** `fix(cli,discover): tolerate corrupt config.json in list/status (P10)`

## Step 5 — Phase close-out

- `uv run pytest` — full suite green.
- `uv run basedpyright`, `uv run ruff check`, `uv run ruff format --check` clean.
- Flip C8/C9/C10/P10 in [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status) from `planned` → `fixed`.
- Subagent review of the phase as a whole — check that the new `invalid-config` state is wired into every command that calls `probe()`.

No close-out commit unless the review surfaces a fix.
