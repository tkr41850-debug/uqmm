# Config / input validation

Scenarios where users get tripped up by argument handling, image resolution, or the gap between "what I asked for" and "what actually happened".

Legend — **V**: verified against code (Y/P/N). **T**: testability (E/M/H). **D**: implementation difficulty (E/M/H).

| ID | Scenario | V | T | D |
|---|---|---|---|---|
| I1 | `--version 3.21.4` → uncaught `ValueError`, no "did you mean 3.21?" hint | Y | E | E |
| I2 | "current/latest" cloud images go stale forever — cache reuses by basename, never revalidates | Y | E | M |
| I3 | Different image URLs aliasing to the same basename collide silently in cache | Y | E | M |
| I4 | `--image foo.iso --os debian` (or vice versa) — no artifact-type validation; fails late inside builder | Y | E | M |
| I5 | Image lookup/download failures leak raw exceptions (`FileNotFoundError`, `httpx`) instead of friendly errors | Y | E | M |
| I6 | `--disk-size-gb 0` / negative / smaller-than-base accepted; surfaces as cryptic `qemu-img` error | P | E | E |
| I7 | `--memory-mb 256` becomes a vague 5-min SSH timeout — no minimum-RAM guard | P | M | M |
| I8 | Alpine install silently bumps vcpus/memory ≥4/4096; user unaware their runtime values were never validated during install | Y | E | E |
| I9 | `--vcpus 0` / `-1` / `999` accepted with no range check | Y | E | E |
| I10 | Explicit `--ssh-port` not bind-probed (only auto-allocate runs the probe) | Y | E | E |
| I11 | Auto-port TOCTOU race — losing the bind manifests as SSH timeout, no automatic retry | Y | H | H |
| I12 | Mistyped `--key` path → uncaught `FileNotFoundError` traceback | Y | E | E |
| I13 | Private key file accepted in place of public — embedded into seed, SSH banner answers, "create succeeded" but auth fails | P | M | E |
| I14 | `--user root` / `--user "Jane Doe"` passed through; SSH-banner check doesn't verify the configured user works | P | M | E |
| I15 | VM names with `/` create nested dirs invisible to `iter_vm_dirs`; no hostname-char validation on the default hostname | Y | E | E |

## Notes

- I1 / I9 / I10 / I12 / I15 are a coherent cluster: trivial preflight checks at the CLI boundary that turn unhelpful tracebacks into one-line errors.
- I8 is documentation-shaped — Alpine install needs >=4/4096 to finish under TCG (see `builders/alpine.py:120-128`), but that override is invisible to users. Print a notice at install time.
- I2 / I3 are about cache provenance — the cache is keyed by URL basename, which is fine for canonical URLs but breaks for arbitrary `--image https://...` overrides. Acceptable for now; revisit if multi-mirror or rolling images become common.
- I13 / I14 are partial-verify because final success depends on guest behavior the host can't fully observe; mitigations are best-effort (regex check, login-attempt verification).
