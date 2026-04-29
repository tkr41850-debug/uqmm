# Phase 3 — Input validation polish

CLI-boundary checks that turn unhelpful tracebacks into one-line errors. None of these change behavior for correct inputs — they fail bad inputs faster and more clearly.

End state: bad versions, vcpus, ports, key paths, and VM names produce friendly errors before any disk work begins.

Issues: **[I1](../../issues/config.md), [I9](../../issues/config.md), [I10](../../issues/config.md), [I12](../../issues/config.md), [I15](../../issues/config.md)**.

Anchors: [../../design/cli.md](../../design/cli.md), [../../design/config.md](../../design/config.md).

## Pre-commit gate (every step in this phase)

Before each `Commit:` below, run [the pre-commit gate from the README](README.md#pre-commit-gate): format → lint → type-check → tests → subagent diff review. The subagent prompt should reference both the issue ID being addressed and the design doc that names the affected area. Never `--no-verify`.

## Step 1 — "Did you mean" hint for unknown versions (I1)

`src/uqmm/resolve.py:49-56` — `canonical_url()` raises `ValueError("no canonical image URL for ...")` and that bubbles unchanged through `create()`.

Augment: when the (os, version) combo is missing, look up other versions for the same `os` in `_CANONICAL_URLS` and either:
- Suggest the closest by string-prefix match (e.g., `3.21.4` → suggest `3.21`).
- Otherwise list the known versions for that OS.

Wrap this raise at the CLI boundary in `cli.py:create()` so the user sees a one-line stderr message instead of a traceback. Use `difflib.get_close_matches` for the suggestion if simple prefix match doesn't fit cleanly.

**Tests** (`tests/test_resolve.py`, `tests/test_cli.py`) — test-first:

- `test_I1_canonical_url_suggests_close_match` — call with `("alpine", "3.21.4")`; assert `ValueError` message contains `3.21`.
- `test_I1_canonical_url_lists_known_versions` — call with `("alpine", "9999")`; assert message lists `3.21`.
- `test_I1_create_prints_clean_error` — `main(["create", "foo", "--os", "alpine", "--version", "3.21.4", ...])` returns non-zero, stderr is one line, mentions `3.21`.

**Commit:** `feat(resolve,cli): suggest close-match versions on lookup miss (I1)`

## Step 2 — vcpus/memory range check (I9)

`src/uqmm/cli.py:50-52` and `src/uqmm/config.py:25-27` — `vcpus` and `memory_mb` are bare `int` with no validation. `--vcpus 0` or `-1` reach QEMU and produce cryptic errors.

Add validation in `VMConfig.__post_init__` (or at CLI parse time, but config is the source of truth so put it there). Reject `vcpus < 1`, `memory_mb < 64`, and add a soft cap: `vcpus > 64` and `memory_mb > 1_048_576` raise as "almost certainly a typo". CLI catches `ValueError` from `VMConfig` construction.

Group memory check with vcpus while we're here — same shape, same fix.

**Tests** (`tests/test_config.py`, `tests/test_cli.py`) — test-first:

- `test_I9_vcpus_zero_rejected`, `test_I9_vcpus_negative_rejected`, `test_I9_vcpus_huge_rejected` — `VMConfig(vcpus=0|−1|999, ...)` raises `ValueError` with "vcpus" in the message.
- `test_I9_memory_too_low_rejected`, `test_I9_memory_huge_rejected` — same shape.
- `test_I9_create_prints_clean_error_for_vcpus_zero` — CLI test, non-zero exit, stderr names `vcpus`.

**Commit:** `feat(config,cli): validate vcpus and memory_mb ranges (I9)`

## Step 3 — Bind-probe explicit ports (I10)

`src/uqmm/cli.py:80-82` — when `--ssh-port` is supplied, the bind probe in `state.pick_ssh_port()` is skipped. QEMU then fails its hostfwd bind, which surfaces as a 5-min SSH timeout (or, with C6 from phase 2, an immediate error — but still without naming the port).

Extract the bind-probe into `state.is_port_bindable(port: int) -> bool` (refactor from `pick_ssh_port`'s inner loop). Call it from `cli.create()` for explicit ports, before any vm_dir mkdir. Failure → friendly error ("port 22500 is unavailable; another process is using it, or it is privileged"). Auto-allocation continues to use `pick_ssh_port` unchanged.

Also check the port is in a sane range (1024–65535) — `--ssh-port 22` should fail before bind for clarity.

**Tests** (`tests/test_state.py`, `tests/test_cli.py`) — test-first:

- `test_I10_is_port_bindable_returns_false_for_in_use` — bind a real socket on a free port, assert `is_port_bindable` returns False.
- `test_I10_create_rejects_unbindable_port` — `main(["create", "foo", "--ssh-port", <bound port>, ...])` returns non-zero with message naming the port. Make sure no vm_dir is created.
- `test_I10_create_rejects_privileged_port` — `--ssh-port 22`; rejected before bind.

**Commit:** `fix(state,cli): bind-probe explicit --ssh-port (I10)`

## Step 4 — Catch missing key files (I12)

`src/uqmm/cli.py:214-228` — `_load_keys()` calls `Path.read_text()` with no error handling. A typo in `--key` produces a raw `FileNotFoundError` traceback.

Wrap each `read_text()` in try/except `OSError`; collect bad paths, raise once at the end naming all of them. CLI converts to a friendly stderr message and exits 2 (matching other input errors at `cli.py:65`).

While we're here: also reject empty key files (a file with only blank lines after stripping), since that produces the same "no SSH key" error as missing the flag entirely but with a more confusing path.

**Tests** (`tests/test_cli.py`) — test-first:

- `test_I12_missing_key_file_clean_error` — `main(["create", "foo", ..., "--key", "/nonexistent.pub"])` returns 2, stderr names the path, no traceback.
- `test_I12_multiple_missing_keys_listed` — two `--key` flags, both missing; both names appear in the error.
- `test_I12_empty_key_file_rejected` — `--key /tmp/empty.pub`; rejected with a message about contents, not "no SSH key".

**Commit:** `fix(cli): handle missing/empty --key files cleanly (I12)`

## Step 5 — VM name validation (I15)

`src/uqmm/state.py:28-29` uses the raw `name` as a path segment; `src/uqmm/config.py:36-37` uses it as the default hostname. A name like `team/demo` creates `vms/team/demo/`, which `iter_vm_dirs()` won't list cleanly. Names with spaces or shell metacharacters break in subtler ways.

Add `state.validate_vm_name(name) -> None` raising `ValueError` for: empty string, length > 64, anything outside `[A-Za-z0-9._-]`, or starting with `.` or `-`. Same constraints as a safe Linux hostname plus path-segment safety. Call from `cli.create()` before any state work.

The hostname default falls out for free — if the name is hostname-safe, so is the default hostname.

**Tests** (`tests/test_state.py`, `tests/test_cli.py`) — test-first:

- `test_I15_validate_accepts_normal_names` — `web-1`, `db.test`, `vm_42` all pass.
- `test_I15_validate_rejects_slash`, `test_I15_validate_rejects_space`, `test_I15_validate_rejects_leading_dash`, `test_I15_validate_rejects_empty`, `test_I15_validate_rejects_too_long` — each raises with the offending input in the message.
- `test_I15_create_rejects_slash_name_before_mkdir` — assert no vm_dir created on rejection.

**Commit:** `feat(state,cli): validate VM names against safe-name rules (I15)`

## Step 6 — Phase close-out gate

Run all of the following in order; do not skip any. See [README § Per-phase gate](README.md#per-phase-gate-close-out) for the pattern.

1. **Full test suite** — `uv run pytest` (not `-q`).
2. **Type-check** — `uv run basedpyright` clean.
3. **Format + lint** — `uv run ruff check && uv run ruff format --check` clean.
4. **Phase-level subagent review** — diff the whole phase (`git diff <phase-start-commit>..HEAD`); ask: "are any other CLI inputs still reaching builders without validation? Are validators consistently raising at the CLI boundary (exit 2, friendly stderr) rather than mid-builder? Does the I15 name regex match the constraints assumed by `state.iter_vm_dirs` and the default-hostname path?" List any newly-found gaps; defer unless trivial, and add deferred items to [../../issues/](../../issues/).
5. **Catalog flip** — update [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status): I1, I9, I10, I12, I15 → `fixed`. Add any new issues uncovered in step 4.
6. **Spec sync** — confirm any new validation rules (e.g. VM name regex, vcpus range) are reflected in [../../design/config.md](../../design/config.md). Fix in a sibling `docs:` commit if drift exists.

No close-out commit unless step 4 surfaces a fix or step 6 produces a `docs:` change.
