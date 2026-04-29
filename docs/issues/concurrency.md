# Concurrency, lifecycle races, state drift

Scenarios where uqmm's on-disk state and reality disagree — concurrent commands, non-atomic writes, stale pidfiles, out-of-band file mutation.

Legend — **V**: verified against code (Y/P/N). **T**: testability (E/M/H). **D**: implementation difficulty (E/M/H).

| ID | Scenario | V | T | D |
|---|---|---|---|---|
| C1 | `stop` cannot act on in-progress create (no `config.json` yet → "no such VM") | Y | E | E |
| C2 | `delete` removes vm_dir while `create` is still using it — orphans live QEMU | Y | M | M |
| C3 | `stop` immediately after `start` becomes SIGKILL because QMP not yet listening | Y | M | E |
| C4 | Two concurrent `start foo` calls — both launch QEMU, last pidfile wins | Y | H | M |
| C5 | `disk.qcow2`/`seed.iso` deleted out-of-band — `start` launches blindly, prints "started" before failure surfaces | Y | E | E |
| C6 | `wait_ready` doesn't race `proc.wait()` — QEMU dies, CLI sits up to 300s before timing out | Y | E | E |
| C7 | PID reuse after host reboot — stale `qemu.pid` makes `stop` SIGKILL the wrong process | P | H | H |
| C8 | `config.json` writes non-atomic (`Path.write_text`) — torn JSON visible to readers | Y | M | E |
| C9 | `qemu.pid` writes non-atomic — `probe()` may delete a valid pidfile mid-write | Y | M | E |
| C10 | Corrupt `config.json` silently disappears from `read_occupied_ports()` set → port double-assignment | Y | E | E |
| C11 | Image cache wiped between operations — existing VMs still start, new canonical creates silently re-download, `--image local` fails. Asymmetric. | Y | E | E |

## Notes

- C8/C9/C10 are a coherent cluster: state files (config.json, qemu.pid) lack atomic write semantics and corrupt-state handling. A single small change (temp + rename) addresses C8/C9; a separate "fail closed when config is corrupt" choice addresses C10.
- C7 (PID reuse) is partially mitigated by the QMP+SSH-banner double-check in `probe()` — recycled PIDs can still produce a fake `starting` state, but rarely a fake `running`. Mitigations require recording more than a bare PID (e.g., process start time, a uqmm-tagged comm name).
- C2 is reachable via the same fix as R5 (a `creating` state or per-VM lock).
