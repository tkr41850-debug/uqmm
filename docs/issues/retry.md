# Retry, idempotency, partial state

Scenarios where `uqmm create` interacts badly with prior state — failed runs, interruptions, repeats. Most originate from a single line: `cli.py:75-78` rejects any existing `vm_dir` regardless of state.

Legend — **V**: verified against code (Y/P/N). **T**: testability (E/M/H). **D**: implementation difficulty (E/M/H).

| ID | Scenario | V | T | D |
|---|---|---|---|---|
| R1 | Ctrl-C during image download leaves a `state=failed` config even though no boot happened | Y | E | M |
| R2 | Hard-kill (SIGKILL/host crash) after disk created but before any config saved — disk orphaned | Y | H | H |
| R3 | Cloud-image `qemu-img create` succeeds, `resize` fails — overlay stays, retry blocked | Y | E | E |
| R4 | Explicit `--ssh-port` collision discovered late, after disk + seed work | P | E | E |
| R5 | Second `create foo` while first is still running — refused with "directory exists" | Y | E | M |
| R6 | Two simultaneous `create foo` race past existence check — loser hits uncaught `FileExistsError` | Y | M | E |
| R7 | Two concurrent auto-allocate creates pick the same SSH port; loser fails hostfwd bind, manifests as SSH timeout | Y | H | H |
| R8 | Manual `--ssh-port` invisible to concurrent auto-allocate (port not saved until after success/failure) | Y | M | M |
| R9 | CLI dies after QEMU launch but before config saved or SSH wait returns — live guest, no state record | Y | H | H |
| R10 | Alpine create times out before guest fetches `/answers` — failed disk + answers retained, retry forced from scratch | Y | E | M |
| R11 | Alpine install done, interrupted *between* installer exit and runtime relaunch — installed disk lost on retry | Y | H | H |
| R12 | Alpine install done, final SSH-wait times out — spec forces delete + reinstall, even though OS is on disk | Y | M | M |
| R13 | Cloud guest reboots once during first boot — `-no-reboot` exits QEMU; surfaces as SSH timeout, partial overlay remains | P | H | M |
| R14 | Re-running already-successful `create` errors instead of reporting idempotent success | Y | E | M |
| R15 | User wants new `--key`/`--hostname` after early failure — no way to regenerate seed in place | Y | M | M |
| R16 | Stable cloud-init `instance-id` from VM name suppresses re-run on reboot — changing keys silently no-ops | P | M | H |

## Notes

- **The user-reported bug** lives in this category: any failed create blocks retry. Cluster R3 / R5 / R10–R14 captures the surface. R1 is the same root cause manifesting as "Ctrl-C during download → unrecoverable state".
- R7 is acknowledged in `state.py:69-73` as inherent to SLiRP (no fd-passing for hostfwd). Listed for completeness.
- R16 is a trap that lurks behind any fix that "lets the user retry with new keys" — even if `create` is re-runnable, a re-booted cloud-init with a stable `instance-id` will not re-apply user-data. Either rotate `instance-id` when seed inputs change, or document the limitation.
