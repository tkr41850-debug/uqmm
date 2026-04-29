# Toolchain

Python version, libraries, and packaging choices for uqmm.

## Python version

**Floor: 3.13. Pin: 3.14.**

```toml
# pyproject.toml
requires-python = ">=3.13"
```

```
# .python-version
3.14
```

State (April 2026): 3.14.4 is current stable; 3.13 is in bugfix; 3.15 is in alpha ([Python release schedule](https://devguide.python.org/versions/), [PEP 745](https://peps.python.org/pep-0745/)).

3.14 is the right pin — all recommended libraries support it, and 3.14 adds asyncio call-graph introspection (`python -m asyncio ps/pstree`, `asyncio.print_call_graph()`) which directly helps debug "why is uqmm hung at the login prompt?" ([What's New 3.14](https://docs.python.org/3.14/whatsnew/3.14.html)).

`asyncio.TaskGroup` + `ExceptionGroup` (3.11+) are essential for coordinating QMP client + serial console + QEMU subprocess + ephemeral HTTP server cleanly. `except*` splits QMP errors from SSH errors when failures fan in.

Don't use the free-threaded build (`python3.14t`) — `cryptography` (asyncssh transitive dep) re-enables the GIL, and uqmm is I/O-bound asyncio anyway.

## Runtime dependencies

| Library | Version | License | Why |
|---|---|---|---|
| `qemu.qmp` | `>=0.0.6` | LGPL/GPL | Only credible QMP client; asyncio-native; published Mar 2026. |
| `asyncssh` | `>=2.22` | EPL/GPL | Async-native SSH; matches QMP's asyncio shape. Beats paramiko for asyncio-shaped tools. |
| `pexpect` | `>=4.9` | ISC | Use `pexpect.socket_pexpect.SocketSpawn` for serial socket driving (newer API than `fdpexpect`). Stable; no successor has displaced it. |
| `cyclopts` | `>=4.4` | Apache-2.0 | Type-hint-driven CLI; first-class `*args`/`**kwargs` passthrough for `uqmm ssh -- ...`. Cleaner than Typer's `context_settings={"allow_extra_args": True}` workaround. |
| `pycdlib` | `>=1.16` | LGPL | Pure-Python ISO writer; avoids the `xorriso` system dep for CIDATA seeds. |
| `httpx` | `>=0.27` | BSD-3 | Sync+async HTTP; range/resume support for cloud image downloads. |
| `rich` | `>=13` | MIT | Progress bars; already transitive via cyclopts. |
| `pyyaml` | `>=6.0.3` | MIT | cloud-init parses YAML 1.1; PyYAML matches. **Don't use ruamel.yaml** — it defaults to 1.2 and silently changes `yes`/`no` semantics. |

**Stdlib for** (no extra deps): HTTP server (one-shot answers serving via `http.server.ThreadingHTTPServer`), subprocess (`asyncio.create_subprocess_exec` for QEMU), logging, JSON (config.json).

**Shell out** (don't wrap in Python): `qemu-img` for offline qcow2 ops; live ops route through QMP (`block_resize`, `blockdev-snapshot-sync`).

## Dev dependencies

| Tool | Version | Why |
|---|---|---|
| `ruff` | `>=0.5` | Linter + formatter; replaces flake8/black/isort. |
| `basedpyright` | `>=1.20` | Type checker; stricter defaults than pyright, faster than mypy, actively maintained. |
| `pytest` | `>=8` | Test runner. |
| `pytest-asyncio` | `>=0.23` | Asyncio test fixtures. |
| `types-pyyaml` | latest | PyYAML stub package. |

## `pyproject.toml` skeleton

```toml
[project]
name = "uqmm"
version = "0.1.0"
description = "Headless QEMU machine manager"
readme = "README.md"
requires-python = ">=3.13"
license = "MIT"
dependencies = [
    "qemu.qmp>=0.0.6",
    "asyncssh>=2.22",
    "pexpect>=4.9",
    "cyclopts>=4.4",
    "pycdlib>=1.16",
    "httpx>=0.27",
    "rich>=13",
    "pyyaml>=6.0.3",
]

[project.scripts]
uqmm = "uqmm.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "ruff>=0.5",
    "basedpyright>=1.20",
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "types-pyyaml",
]

[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "RUF"]

[tool.basedpyright]
pythonVersion = "3.13"
typeCheckingMode = "strict"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Use `[dependency-groups]` (PEP 735), not `[project.optional-dependencies]` ([PEP 735](https://peps.python.org/pep-0735/), [uv deps](https://docs.astral.sh/uv/concepts/projects/dependencies/)). Optional-deps are public extras for `pip install uqmm[dev]`; dependency-groups are private to the project and what uv expects.

## uv setup

Bootstrap: `uv init --package --app uqmm` scaffolds `[project.scripts]` and a `src/` layout.

| Command | Purpose |
|---|---|
| `uv sync --group dev` | Install runtime + dev deps. |
| `uv run uqmm <args>` | Run the CLI in dev. |
| `uv run pytest` | Run tests. |
| `uv run basedpyright` | Type check. |
| `uv run ruff check` / `uv run ruff format` | Lint / format. |

Use uv-managed Python (default `python-preference = "managed"`); uv pulls from `python-build-standalone`. Don't force system Python outside CI.

`requires-python` and `.python-version` serve different roles — keep both. `requires-python` is the floor (what consumers need); `.python-version` is the pin (what `uv run` uses).

## Testing pattern

uqmm's `cli.py` exposes `main()` as a function that takes `argv` as a parameter:

```python
def main(argv: list[str] | None = None) -> int:
    """Entry point. argv defaults to sys.argv[1:] when called via console-script."""
    ...
```

The console-script entry (`uqmm = "uqmm.cli:main"`) calls `main()` with no args — falls through to `sys.argv`. Integration tests call `main(["create", "test-vm", "--os", "alpine", ...])` directly, mocking `subprocess`/`socket`/`pexpect` at the boundaries. No actual subprocess invocation needed.

See [cli.md](cli.md) for the full command surface.

## Gotchas

1. **Licensing.** `qemu.qmp` (LGPL/GPL), `asyncssh` (EPL/GPL), and `pycdlib` (LGPL) are copyleft. For uqmm distributed as a pure-Python MIT package via PyPI, all three are compatible — copyleft kicks in only for proprietary redistribution or static single-file binaries. Document in `THIRD_PARTY_LICENSES` when releasing.

2. **CIDATA volume label case.** Use `cidata` (lowercase) in `pycdlib`'s `vol_ident=`. Current cloud-init docs say uppercase `CIDATA` is required, but a 2025 Launchpad bug ([LP #2100232](https://bugs.launchpad.net/ubuntu/+source/ubuntu-raspi-settings/+bug/2100232)) deprecates non-`cidata` variants. Lowercase is the maximally compatible choice. The research docs (written earlier) say uppercase — this is the implementation override.

3. **YAML 1.1 vs 1.2.** Don't switch to `ruamel.yaml`. cloud-init parses YAML 1.1 where `yes`/`no`/`on`/`off` are booleans; ruamel defaults to 1.2 where they're strings. PyYAML matches cloud-init.

4. **qemu-img on a running VM is unsafe** — the qemu-img manual is explicit. Route live resize/snapshot through QMP (`block_resize`, `blockdev-snapshot-sync`, optionally wrapped in `transaction` for atomicity).

5. **asyncio subprocess pipe deadlock.** If you don't drain stderr, QEMU blocks once the kernel pipe buffer (~64 KB) fills. Always redirect stderr to a file or read it in a `TaskGroup` task. Don't use `-monitor stdio` — use a unix socket.

6. **pexpect must be sole consumer of the serial socket.** If you also `loop.add_reader` on the same FD, you'll get partial reads.

7. **stdlib `http.server` in async context.** Run it via `loop.run_in_executor(None, server.handle_request)` for one-shot answer-file delivery, or in a thread for the duration of the install. Don't try to mix it directly with the asyncio event loop.

8. **`pexpect` last released Nov 2023.** Stable, not abandoned, but no recent activity. No mature successor exists yet.

9. **`cyclopts` 3→4 migration was breaking.** Pin `>=4`; don't pick up old 3.x examples from blogs.

## Sources

- [Python 3.14 release notes](https://docs.python.org/3.14/whatsnew/3.14.html)
- [PEP 745 (3.14 release schedule)](https://peps.python.org/pep-0745/)
- [Python version status](https://devguide.python.org/versions/)
- [qemu.qmp on PyPI](https://pypi.org/project/qemu.qmp/)
- [asyncssh on PyPI](https://pypi.org/project/asyncssh/)
- [pexpect socket_pexpect](https://pexpect.readthedocs.io/en/latest/api/socket_pexpect.html)
- [cyclopts vs Typer](https://cyclopts.readthedocs.io/en/latest/vs_typer/README.html)
- [pycdlib on PyPI](https://pypi.org/project/pycdlib/)
- [HTTPX async](https://www.python-httpx.org/async/)
- [cloud-init format (YAML 1.1)](https://docs.cloud-init.io/en/24.2/explanation/format.html)
- [PEP 735 dependency-groups](https://peps.python.org/pep-0735/)
- [uv project config](https://docs.astral.sh/uv/concepts/projects/config/)
- [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)
- [Ruff FAQ](https://docs.astral.sh/ruff/faq/)
- [basedpyright](https://github.com/DetachHead/basedpyright)
