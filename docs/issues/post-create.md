# Post-create operations

Scenarios that arise after a successful `create` — `start`, `stop`, `delete`, `status`, `ssh`, `log`, and operations that should exist but don't.

Legend — **V**: verified against code (Y/P/N). **T**: testability (E/M/H). **D**: implementation difficulty (E/M/H).

| ID | Scenario | V | T | D |
|---|---|---|---|---|
| P1 | `start` refuses `unreachable` VMs — can't recover from a hung sshd without manual stop first | Y | E | E |
| P2 | `start` (no `--wait`) reports success even when QEMU dies on launch (pidfile written before health check) | Y | E | M |
| P3 | Cloud-image `sudo reboot` exits QEMU because runtime args still include `-no-reboot` | Y | E | E |
| P4 | `stop` escalation (30s graceful → 5s force-quit → SIGKILL) is silent — user can't tell whether shutdown was graceful | Y | M | E |
| P5 | `delete` on running VM is silent + no `--force` flag, doesn't report whether stop was graceful | Y | E | M |
| P6 | `ssh` immediately after non-`--wait` `start` is racey — `starting` rejects, `unreachable` execs anyway | Y | E | M |
| P7 | Host-key mismatch after recreate has no native repair (no `uqmm known-hosts forget`) | Y | E | M |
| P8 | `status running` doesn't mean cloud-init finished — just that SSH banner answered | Y | E | H |
| P9 | `log --follow` only tails install.log, never live serial; never auto-exits on VM stop | Y | E | H |
| P10 | One corrupt `config.json` crashes the entire `list` / `status` flow (uncaught `VMConfig.load`) | Y | E | E |
| P11 | VM dir without `config.json`: status/start/stop/ssh/log/delete/list each give different answers | Y | E | M |
| P12 | No `update` command for memory/vcpus/ssh-port post-create | Y | E | M |
| P13 | No disk-resize command; editing `disk_size_gb` in config does nothing | Y | E | M |
| P14 | No snapshot/rollback command | Y | E | H |
| P15 | No clone/export/import; cloud overlays not portable by directory copy alone | Y | E | H |

## Notes

- P3 is a one-liner: the `-no-reboot` flag in `_qemu_args` (`builders/cloudimg.py:182`) belongs in install-time args only, not runtime. `start` reuses the install args verbatim today.
- P8 (cloud-init readiness) is hard because the host can't directly observe guest state without a guest agent. `qemu-guest-agent` is enabled in the seed (`builders/cloudimg.py:42-44`) but uqmm doesn't yet talk to it.
- P12–P15 are missing-feature scenarios, not bugs. Listed so they surface in roadmap discussions.
- P10 is a quick, valuable fix — it currently means one bad config.json kills `uqmm list` for every VM.
