# TCG + SLiRP: constraints and gotchas

Targeting QEMU TCG (no KVM) with SLiRP usermode networking (no TUN/TAP, no root). These constraints shape every uqmm decision.

## TCG tuning

- `-accel tcg,thread=multi` — MTTCG, one TCG thread per vCPU. Default when supported, but pass explicitly for clarity. Forcing `-icount` falls back to single-threaded.
- `tb-size=N` (MiB) — translation block cache size. Default is fine; only tune with profiling evidence.
- **Skip `-icount`**, **skip `one-insn-per-tb=on`** — both kill performance.

## CPU features under TCG

`-cpu max` works under TCG and emulates (verified against current `target/i386/cpu.c`):

- SSE / SSE2 / SSE3 / SSSE3 / SSE4.x
- AES, SHA-NI, F16C, FMA
- AVX, AVX2
- BMI1 / BMI2, ADX, RDRAND, RDSEED

**NOT emulated** under TCG:

- AVX-512 (any flavor)
- AVX10
- AMX
- XSAVEC / XSAVES

So `+avx` is meaningful (and is in the working baseline command); `+avx512f` will fail.

`-cpu host` is **KVM-only** — do not use it under TCG.

## Performance expectations

Roughly **5×–20× slower** wall-clock vs KVM for OS install workloads, sometimes worse for CPU-bound stages. The 2016 MTTCG benchmark on `kvm-unit-tests` showed `-smp 4` going from 1055s (single-threaded) to 304s (MTTCG) — about 3.5× from MTTCG alone, but still nowhere near KVM ([qemu-devel thread](https://lists.nongnu.org/archive/html/qemu-devel/2016-08/msg02286.html)).

**Plan for installs that take 10–60 minutes** where they'd take 2–5 minutes under KVM. MTTCG can also regress some workloads (cache-thrashing serial code), so test before assuming `thread=multi` always wins.

## SLiRP usermode networking

### Guest → host

- Guest reaches host as `10.0.2.2` (per [QEMU Network emulation docs](https://www.qemu.org/docs/master/system/devices/net.html)).
- SLiRP translates guest connections to `10.0.2.2` into host-local connections, so host services on `127.0.0.1` are typically reachable.
- **If you hit issues, bind to `0.0.0.0`** to be safe.
- DNS resolver lives at `10.0.2.3` (separate from `10.0.2.2`); SLiRP forwards to host's resolver.

### hostfwd

Per the manpage: `hostfwd=[tcp|udp|unix]:[host]:port-:gport`.

- Supports **TCP, UDP, and Unix sockets** (not "TCP only" as commonly stated).
- `guestfwd` is TCP-only.
- Caveat ([GitLab #1593](https://gitlab.com/qemu-project/qemu/-/issues/1593)): older builds bind `INADDR_ANY` even when you specify `127.0.0.1` — check your version.
- Caveat ([GitLab #2876](https://gitlab.com/qemu-project/qemu/-/issues/2876)): hostfwd is IPv4-only.

### ICMP

- Ping works to `10.0.2.2`.
- Outbound ping only works if host has `net.ipv4.ping_group_range` configured.
- **Treat ICMP as broken in tooling.** Don't depend on ping for liveness checks; use TCP probes.

### Throughput

Roughly 1–2 Gbit/s under TCG, but with high CPU cost. Fine for ISO downloads and SSH; poor for sustained heavy I/O.

## HTTP autoinstall serving

Pattern for serving Ubuntu autoinstall data or Alpine answer files to the guest:

```sh
python3 -m http.server 8000 --bind 0.0.0.0
```

In your seed directory, place `user-data`, `meta-data`, `network-config` (Ubuntu) or `answers` (Alpine).

Guest cmdline:

- **Ubuntu**: `ds=nocloud-net;s=http://10.0.2.2:8000/` (semicolon escaped as `\;` in GRUB).
- **Alpine**: `setup-alpine -ef http://10.0.2.2:8000/answers` (called from apkovl autorun script).

Reliable. No DNS tricks — address by IP.

## Recommended QEMU args under our constraints

```sh
qemu-system-x86_64 \
    -accel tcg,thread=multi \
    -cpu max,+avx \
    -smp 4 -m 4G \
    -netdev user,id=mynet0,hostfwd=tcp::5901-:22 \
    -device virtio-net-pci,netdev=mynet0 \
    -display none \
    -serial unix:/tmp/serial.sock,server=on,wait=off \
    -qmp unix:/tmp/qmp.sock,server=on,wait=off \
    -no-reboot
```

Plus per-OS install args (drives, kernel/initrd, append) from the OS-specific docs.

## Sources

- [QEMU MTTCG design doc](https://www.qemu.org/docs/master/devel/multi-thread-tcg.html)
- [QEMU Network emulation docs](https://www.qemu.org/docs/master/system/devices/net.html)
- [QEMU x86 CPU source (TCG features)](https://gitlab.com/qemu-project/qemu/-/raw/master/target/i386/cpu.c)
- [GitLab #1593 — hostfwd bind-address bug](https://gitlab.com/qemu-project/qemu/-/issues/1593)
- [GitLab #2876 — hostfwd IPv4-only](https://gitlab.com/qemu-project/qemu/-/issues/2876)
- [GitLab #2878 — AVX-512 not in TCG](https://gitlab.com/qemu-project/qemu/-/issues/2878)
- [MTTCG benchmark thread (qemu-devel 2016)](https://lists.nongnu.org/archive/html/qemu-devel/2016-08/msg02286.html)
- [Landley QEMU networking presentation](https://landley.net/aboriginal/presentation.html)
