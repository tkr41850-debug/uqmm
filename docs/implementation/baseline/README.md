# Baseline implementation

Light-TDD plan to bring uqmm from green-field to a working `uqmm create` for both supported install paths.

The design docs ([../../design/](../../design/)) say *what* to build; these phase docs say *in what order*, *with what tests*, and *with what commit cadence*.

## Phases

1. [phase-1-foundation.md](phase-1-foundation.md) — `VMConfig`, XDG state, port allocator, image resolver, CLI skeleton. No QEMU.
2. [phase-2-cloud-image.md](phase-2-cloud-image.md) — `CloudImageBuilder` + QEMU + QMP + SSH readiness. End state: `uqmm create` works for Debian/Ubuntu.
3. [phase-3-alpine.md](phase-3-alpine.md) — `AlpineSeedBuilder` + serial pexpect + answers HTTP server + relaunch handling. End state: `uqmm create` works for Alpine.
4. [phase-4-lifecycle.md](phase-4-lifecycle.md) — `start`/`stop`/`delete`/`status`/`list`/`ssh`/`log` against persisted state.

Each phase ends with a runnable slice. Phase 2 alone gives a usable CLI for the cheaper install path.

## Light-TDD policy

Write tests first only when behavior is non-trivial:

- **Test-first** — port allocator collision handling, image resolver cache hit/miss, cloud-init seed rendering, pexpect state machine, status state machine, QMP event handling.
- **Test-after (or test-with)** — straight wiring (CLI command → builder → launcher), config dataclass round-trip, simple pure functions.
- **Smoke only** — true end-to-end (real QEMU subprocess) gated behind `UQMM_E2E=1`. Skipped in default test runs.

Use `def main(argv: list[str] | None = None) -> int` so integration tests call `main(["create", ...])` directly and mock at boundaries — `subprocess`, `socket`, `pexpect`, `httpx`. See [../../design/cli.md § Entry point](../../design/cli.md).

## Commit cadence

One commit per logical step inside a phase, not one per phase. A phase typically lands in 5–10 commits.

Conventional-commits prefixes: `feat:`, `feat(scope):`, `test:`, `chore:`, `docs:`, `refactor:`, `fix:`. Keep scope to a single module when possible (`feat(builders/cloudimg): ...`).

## Pre-commit gate

Before every commit:

1. **Format** — `uv run ruff format`
2. **Lint** — `uv run ruff check --fix`
3. **Type check** — `uv run basedpyright`
4. **Tests** — `uv run pytest -q` (only the affected path; full suite at end of phase)
5. **Subagent review** — spawn an `Explore` or `general-purpose` subagent with the staged diff and the relevant design doc, ask it for: bugs, missed edge cases, deviations from the design, dead code. Address findings or note why deferred. Cap response at ~300 words to stay context-efficient.

If any step fails, fix and re-stage. **Never bypass with `--no-verify`.** A pre-commit failure means the commit didn't happen — fix forward, don't amend after the fact.

End-of-phase additionally: full `uv run pytest` and a once-over of the design doc that the phase implements to confirm nothing was silently dropped.

## Deviations from design

If implementation reveals a flaw in the design doc, fix the design doc in the same commit (or a sibling `docs:` commit immediately before). Don't let code and design drift.
