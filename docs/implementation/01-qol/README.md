# 01-qol — Quality-of-life pass

Light-TDD plan to address the catalogued issues from [../../issues/](../../issues/) — first the cheap, isolated wins, then the larger retry-UX overhaul that motivates this whole pass.

The design docs ([../../design/](../../design/)) describe what uqmm *is*; these phases say *what to fix*, *with what tests*, and *with what commit cadence*.

## Phases

1. [phase-1-state-io.md](phase-1-state-io.md) — atomic config + pidfile writes; tolerate corrupt config in listings. Issues: **C8, C9, C10, P10**.
2. [phase-2-launch.md](phase-2-launch.md) — preflight artifact check; race process exit against SSH wait; drop `-no-reboot` at runtime; Alpine install resource-bump notice; resize-failure cleanup; allow `start` on `unreachable`. Issues: **C5, C6, P3, I8, R3, P1**.
3. [phase-3-input-validation.md](phase-3-input-validation.md) — version "did you mean"; vcpus range check; bind-probe explicit ports; catch missing key files; VM-name validation. Issues: **I1, I9, I10, I12, I15**.
4. [phase-4-rerunnable-create.md](phase-4-rerunnable-create.md) — `state="creating"`; idempotent success when config matches; don't mark failed if no work done; resume from a `failed` directory. Issues: **R14, R5, R1**. Addresses the user-reported bug.
5. [phase-5-alpine-resume.md](phase-5-alpine-resume.md) — Alpine install resumability via per-step checkpoint markers. Issues: **R10, R11, R12**.

Phases 1–3 are independent and can be parallelized if desired. Phases 4–5 depend on phase 1's atomic writes (the `state` field becomes load-bearing). Phase 5 builds on phase 4's recovery surface.

## Light-TDD policy

Same as [../baseline/README.md § Light-TDD policy](../baseline/README.md#light-tdd-policy). Summary: tests-first when behavior is non-trivial; test-with for thin wiring; smoke-only for end-to-end paths. Mock at boundaries (`subprocess`, `socket`, `pexpect`, `httpx`).

For this pass specifically:

- Each issue listed in a phase has at least one regression test that would have caught the original behavior. Test name should reference the issue ID (e.g., `test_C8_config_save_atomic`).
- Where the fix is a one-line guard, the test asserts the guard runs (e.g., the bind-probe is called for explicit ports). Where the fix is structural (e.g., a new state), the test asserts a full round-trip.

## Commit cadence

One commit per fix. Group by issue ID, not by phase. A phase typically lands in 4–8 commits.

Conventional-commits prefixes from baseline: `fix:`, `feat:`, `feat(scope):`, `test:`, `chore:`, `docs:`, `refactor:`. Reference the issue ID in the commit body when the connection isn't obvious from the title.

## Pre-commit gate

Same as [../baseline/README.md § Pre-commit gate](../baseline/README.md#pre-commit-gate): format, lint, type-check, test, subagent diff review. Never `--no-verify`.

End of each phase: full `uv run pytest` + flip the affected issues from `planned` → `fixed` in [../../issues/README.md § Adoption status](../../issues/README.md#adoption-status).

## Out of scope

Issues marked **deferred** in the adoption table are explicitly out of scope for this pass. They're tracked in case priorities change, but no plan covers them.
