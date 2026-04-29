# Issues

Catalogued user-experience scenarios where uqmm misbehaves, validated against the codebase. Each topic doc carries a table of scenarios with verification status, testability, and implementation difficulty.

## Topics

- [retry](retry.md) — `create` failures, partial state, idempotency, resumption (16 scenarios).
- [concurrency](concurrency.md) — lifecycle races, non-atomic writes, stale pidfiles, out-of-band file mutation (11 scenarios).
- [config](config.md) — argument validation, image resolution, the gap between asked-for and actual (15 scenarios).
- [post-create](post-create.md) — `start`/`stop`/`delete`/`status`/`ssh`/`log` quirks and missing operations (15 scenarios).

## Index

| ID | Topic | One-line summary |
|---|---|---|
| R1 | retry | Ctrl-C during image download leaves a `state=failed` config |
| R2 | retry | Hard-kill after disk created but before any config saved |
| R3 | retry | Cloud-image `qemu-img resize` failure leaves overlay, blocks retry |
| R4 | retry | Explicit `--ssh-port` collision discovered late |
| R5 | retry | Second `create foo` while first running — refused with "directory exists" |
| R6 | retry | Two simultaneous `create foo` race past existence check |
| R7 | retry | Concurrent auto-allocate creates pick the same port |
| R8 | retry | Manual `--ssh-port` invisible to concurrent auto-allocate |
| R9 | retry | CLI dies after QEMU launch, before config saved |
| R10 | retry | Alpine create times out before guest fetches `/answers` |
| R11 | retry | Alpine install done, interrupted before runtime relaunch |
| R12 | retry | Alpine install done, final SSH-wait timed out |
| R13 | retry | Cloud guest reboots once during first boot, surfaces as SSH timeout |
| R14 | retry | Re-running already-successful `create` errors instead of idempotent success |
| R15 | retry | No way to regenerate seed in place after early failure |
| R16 | retry | Stable cloud-init `instance-id` suppresses re-run on key/hostname change |
| C1 | concurrency | `stop` cannot act on in-progress create |
| C2 | concurrency | `delete` removes vm_dir while `create` is using it |
| C3 | concurrency | `stop` immediately after `start` becomes SIGKILL |
| C4 | concurrency | Two concurrent `start foo` calls — both launch QEMU |
| C5 | concurrency | Disk/seed deleted out-of-band — `start` launches blindly |
| C6 | concurrency | `wait_ready` doesn't race `proc.wait()` |
| C7 | concurrency | PID reuse after host reboot — `stop` SIGKILLs wrong process |
| C8 | concurrency | `config.json` writes non-atomic |
| C9 | concurrency | `qemu.pid` writes non-atomic |
| C10 | concurrency | Corrupt `config.json` allows port double-assignment |
| C11 | concurrency | Image cache wiped — asymmetric behavior across lifecycle commands |
| I1 | config | `--version 3.21.4` → uncaught ValueError, no "did you mean" |
| I2 | config | "current/latest" cloud images go stale forever in cache |
| I3 | config | Different image URLs alias to same cached basename |
| I4 | config | `--image` artifact type not validated against `--os` |
| I5 | config | Image lookup/download failures leak raw exceptions |
| I6 | config | `--disk-size-gb` lacks semantic checks |
| I7 | config | `--memory-mb` too low becomes vague 5-min SSH timeout |
| I8 | config | Alpine install silently bumps vcpus/memory ≥4/4096 |
| I9 | config | `--vcpus` accepts 0/negative/absurd values |
| I10 | config | Explicit `--ssh-port` not bind-probed |
| I11 | config | Auto-port TOCTOU race has no retry |
| I12 | config | Mistyped `--key` path → uncaught FileNotFoundError |
| I13 | config | Private key file accepted in place of public |
| I14 | config | Invalid `--user` passes through; SSH-banner check doesn't validate |
| I15 | config | VM names with `/` create nested dirs; no hostname-char check |
| P1 | post-create | `start` refuses `unreachable` VMs |
| P2 | post-create | `start` (no `--wait`) reports success even when QEMU dies on launch |
| P3 | post-create | Cloud-image `sudo reboot` exits QEMU because runtime args include `-no-reboot` |
| P4 | post-create | `stop` escalation is silent |
| P5 | post-create | `delete` on running VM is silent, no `--force` |
| P6 | post-create | `ssh` immediately after non-`--wait` `start` is racey |
| P7 | post-create | No native host-key repair (no `uqmm known-hosts forget`) |
| P8 | post-create | `status running` doesn't mean cloud-init finished |
| P9 | post-create | `log --follow` only tails install.log, never live serial |
| P10 | post-create | One corrupt `config.json` crashes entire `list`/`status` |
| P11 | post-create | VM dir without `config.json` — inconsistent behavior across commands |
| P12 | post-create | No `update` command for memory/vcpus/ssh-port |
| P13 | post-create | No disk-resize command |
| P14 | post-create | No snapshot/rollback |
| P15 | post-create | No clone/export/import |

## Adoption status

Tracks which issues are actively planned for fixing and where. Updated as plans land and ship.

Statuses: **planned** — written into a phase doc; **in-progress** — actively being worked; **fixed** — landed on main; **deferred** — known but not on the near roadmap.

| ID | Status | Target | Notes |
|---|---|---|---|
| R3 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Resize-failure cleanup |
| C5 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Preflight artifact check on `start` |
| C6 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Race `proc.wait()` against SSH wait |
| C8 | planned | [01-qol/phase-1](../implementation/01-qol/phase-1-state-io.md) | Atomic config writes |
| C9 | planned | [01-qol/phase-1](../implementation/01-qol/phase-1-state-io.md) | Atomic pidfile writes |
| C10 | planned | [01-qol/phase-1](../implementation/01-qol/phase-1-state-io.md) | Fail closed on corrupt config |
| I1 | planned | [01-qol/phase-3](../implementation/01-qol/phase-3-input-validation.md) | "did you mean" version hint |
| I8 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Notice when Alpine install resources are bumped |
| I9 | planned | [01-qol/phase-3](../implementation/01-qol/phase-3-input-validation.md) | vcpus range check |
| I10 | planned | [01-qol/phase-3](../implementation/01-qol/phase-3-input-validation.md) | Bind-probe explicit ports |
| I12 | planned | [01-qol/phase-3](../implementation/01-qol/phase-3-input-validation.md) | Catch missing key files |
| I15 | planned | [01-qol/phase-3](../implementation/01-qol/phase-3-input-validation.md) | VM-name validation |
| P1 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Allow `start` on `unreachable` |
| P3 | planned | [01-qol/phase-2](../implementation/01-qol/phase-2-launch.md) | Drop `-no-reboot` from runtime args |
| P10 | planned | [01-qol/phase-1](../implementation/01-qol/phase-1-state-io.md) | Tolerate one corrupt config in `list`/`status` |
| R14 | planned | [01-qol/phase-4](../implementation/01-qol/phase-4-rerunnable-create.md) | Idempotent success when config matches |
| R5 | planned | [01-qol/phase-4](../implementation/01-qol/phase-4-rerunnable-create.md) | `creating` state + state-aware retry |
| R1 | planned | [01-qol/phase-4](../implementation/01-qol/phase-4-rerunnable-create.md) | Don't mark failed if no work done |
| R10 | planned | [01-qol/phase-5](../implementation/01-qol/phase-5-alpine-resume.md) | Resume Alpine install on retry |
| R11 | planned | [01-qol/phase-5](../implementation/01-qol/phase-5-alpine-resume.md) | "installed" checkpoint marker |
| R12 | planned | [01-qol/phase-5](../implementation/01-qol/phase-5-alpine-resume.md) | Re-attempt runtime relaunch + SSH wait |
| R2, R9 | deferred | — | Hard-crash safety; needs durable creating-state + repair command |
| R4, R7, R8, I11 | deferred | — | Port allocation TOCTOU; revisit if multi-VM workflows hit it often |
| R6 | deferred | — | Folded into R5 phase indirectly (lock catches the race) |
| R13 | deferred | — | Surfaces as SSH timeout; C6 will at least make it fast-fail |
| R15, R16 | deferred | — | Seed regeneration + instance-id rotation; revisit after R14/R5 land |
| C1, C2 | deferred | — | Folded into R5 phase indirectly (creating state + lock) |
| C3, C4, C7, C11 | deferred | — | Lifecycle race polish |
| I2, I3, I4, I5, I6, I7, I13, I14 | deferred | — | Validation polish; nice-to-have once core retry UX lands |
| P2, P4, P5, P6, P7, P8, P9, P11 | deferred | — | UX polish; not on near roadmap |
| P12, P13, P14, P15 | deferred | — | Missing features; track in product roadmap, not bug list |
