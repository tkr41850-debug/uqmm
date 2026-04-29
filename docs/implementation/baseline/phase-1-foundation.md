# Phase 1 — Foundation

No QEMU. End state: `uv run uqmm --help` runs, `uv run uqmm create --help` shows full flag surface, `pytest` passes with coverage of config, state, resolve, and CLI dispatch.

Anchors: [../../design/config.md § VMConfig](../../design/config.md), [../../design/cli.md](../../design/cli.md), [../../design/toolchain.md](../../design/toolchain.md).

## Deliverables

- `uqmm.config.VMConfig` — dataclass + `to_json` / `from_json` round-trip.
- `uqmm.state` — XDG path helpers + port allocator.
- `uqmm.resolve` — canonical image URLs per `(os, version)` + httpx download-to-cache.
- `uqmm.cli` — cyclopts `App` + stub commands + `main(argv)` entry point.

## Step 0 — Flatten layout + pyproject

Current state: `uv init` produced `uqmm/uqmm/{src,pyproject.toml,...}`. Move everything up one level so `pyproject.toml` is a sibling of `docs/`:

```
uqmm/                          ← repo and project root
  pyproject.toml
  .python-version
  README.md
  src/uqmm/__init__.py
  tests/
  docs/
```

Tighten `pyproject.toml` to match [../../design/toolchain.md § pyproject.toml skeleton](../../design/toolchain.md#pyprojecttoml-skeleton): runtime deps, dev group, ruff, basedpyright, pytest config. Console-script becomes `uqmm = "uqmm.cli:main"` (was `uqmm:main`).

Add `.gitignore` for `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, `dist/`, `*.egg-info/`.

`uv sync --group dev` to verify the toolchain installs cleanly.

**Commit:** `chore: flatten layout, wire deps + tooling config`

## Step 1 — `uqmm.config.VMConfig`

`src/uqmm/config.py`:

- Dataclass exactly as in [../../design/config.md](../../design/config.md). Add a `state: Literal["created", "failed"] = "created"` field for tracking provisioning outcome (see [cli.md § status discovery](../../design/cli.md#status-discovery) — runtime states are *derived*, not stored; only `failed` persists).
- `to_json(self) -> str` and `from_json(cls, s: str) -> Self` using stdlib `json` + `dataclasses.asdict`.
- `save(self, path: Path)` and `load(cls, path: Path)` thin wrappers.

**Tests** (`tests/test_config.py`) — test-first:

- Round-trip: dataclass → JSON → dataclass equals original (including all defaults).
- Hostname falls back to `name` via a property/helper (or `from_json` fills it in if `None`).
- `from_json` rejects unknown OS values.
- `from_json` rejects missing required fields with a clear error.

**Commit:** `feat(config): VMConfig dataclass + JSON serde`

## Step 2 — `uqmm.state`

`src/uqmm/state.py`:

- `data_root() -> Path` — `$XDG_DATA_HOME/uqmm` (default `~/.local/share/uqmm`).
- `cache_root() -> Path` — `$XDG_CACHE_HOME/uqmm` (default `~/.cache/uqmm`).
- `vm_dir(name) -> Path`, `image_cache_dir() -> Path`. Pure path computation — callers `mkdir(exist_ok=True)` when they need to create.
- `iter_vm_dirs() -> Iterator[Path]` — yields existing VM dirs.
- `pick_ssh_port(occupied: set[int], lo: int = 22000, hi: int = 23000) -> int` — for each candidate not in `occupied`, attempt `socket.socket().bind(("127.0.0.1", port))`; first success wins. Raise `RuntimeError` if exhausted.
- `read_occupied_ports() -> set[int]` — scan `iter_vm_dirs()` for `config.json`, collect their `ssh_port`.

**Tests** (`tests/test_state.py`) — test-first for the allocator:

- `pick_ssh_port({22000})` skips 22000 and returns 22001 (mock `socket.bind` to succeed).
- `pick_ssh_port` skips a port that `bind` rejects with `OSError`.
- Empty 22000–23000 raises a clear error.
- XDG paths honor env-var override; default to `~/.local/share/uqmm`.

**Commit:** `feat(state): XDG paths + port allocator`

## Step 3 — `uqmm.resolve`

`src/uqmm/resolve.py`:

- `canonical_url(os: str, version: str) -> str` — table lookup, raise on unknown combo. Initial entries:
  - `("alpine", "3.21")` → `https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-virt-3.21.0-x86_64.iso` (per [../../research/alpine-unattended.md](../../research/alpine-unattended.md))
  - `("debian", "13")` → `https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2`
  - `("ubuntu", "24.04")` → `https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img`
- `cache_path_for(url: str) -> Path` — `image_cache_dir() / basename(url)`.
- `fetch(url: str) -> Path` — sync via `httpx`. If `cache_path` exists, return it. Else stream to `cache_path.tmp`, atomic rename. Show a `rich` progress bar.
- `resolve_image(cfg: VMConfig) -> Path` — if `cfg.image` is `None` → `fetch(canonical_url(...))`; if path → return as-is (must exist); if URL → `fetch`.

**Tests** (`tests/test_resolve.py`):

- `canonical_url` returns expected URL for each known combo; raises for unknown.
- `fetch` cache-hit returns existing path without HTTP (mock httpx; assert no call).
- `fetch` cache-miss writes the file (mock httpx with a small streaming body).
- `resolve_image` accepts a local path that exists; rejects one that doesn't.

**Commit:** `feat(resolve): canonical URLs + httpx cache`

## Step 4 — `uqmm.cli` skeleton

`src/uqmm/cli.py`:

- `app = cyclopts.App(name="uqmm")`.
- Stub commands for `create`, `start`, `stop`, `delete`, `status`, `list`, `ssh`, `log`. Stubs accept their full flag set per [../../design/cli.md](../../design/cli.md) and either `print` a TODO line or raise `NotImplementedError("phase 2/3/4")` so a stub run is loud.
- `def main(argv: list[str] | None = None) -> int` returns the cyclopts app's exit code.
- `[project.scripts] uqmm = "uqmm.cli:main"` (already set in step 0).

**Tests** (`tests/test_cli.py`):

- `main(["--help"])` returns 0; stdout contains "uqmm".
- `main(["create", "--help"])` returns 0; stdout contains the `--os` flag.
- `main(["create"])` returns non-zero (missing required `name`).
- `main(["status"])` returns 0 and prints "no VMs" or similar (status with zero VMs is the only command that works for real this phase).

**Commit:** `feat(cli): cyclopts app skeleton with stub commands`

## Step 5 — Phase close-out

- `uv run pytest` (full suite). Should be ~15–25 unit tests, all green.
- `uv run basedpyright` clean (zero errors in strict mode).
- `uv run ruff check && uv run ruff format --check` clean.
- Subagent diff review of the phase as a whole (last few commits): "did anything in [../../design/config.md](../../design/config.md) or [../../design/cli.md](../../design/cli.md) get silently dropped or contradicted?"

No close-out commit unless the subagent surfaces a fix.
