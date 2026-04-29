"""Image source resolution: canonical URLs + httpx download-to-cache.

See docs/research/cloud-image.md and docs/research/alpine-unattended.md
for the URL patterns. This module is the single place those patterns
live in code.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from uqmm.config import VMConfig
from uqmm.state import image_cache_dir

# Per docs/research/cloud-image.md and docs/research/alpine-unattended.md.
# Alpine pins to a specific patch version because the release dir layout has no
# "latest" symlink — bump when a newer 3.21.x lands. Cloud images use upstream
# `latest/` / `current/` indirection so they don't need bumping.
_CANONICAL_URLS: dict[tuple[str, str], str] = {
    ("alpine", "3.21"): (
        "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-virt-3.21.0-x86_64.iso"
    ),
    ("debian", "13"): (
        "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
    ),
    ("debian", "12"): (
        "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
    ),
    ("ubuntu", "24.04"): (
        "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    ),
    ("ubuntu", "22.04"): (
        "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
    ),
}


def canonical_url(os: str, version: str) -> str:
    try:
        return _CANONICAL_URLS[(os, version)]
    except KeyError:
        import difflib

        known_for_os = [v for (o, v) in _CANONICAL_URLS if o == os]
        close = difflib.get_close_matches(version, known_for_os, n=3, cutoff=0.4)
        suggestion = (
            f"; did you mean: {', '.join(close)}"
            if close
            else f"; known: {', '.join(known_for_os) or 'none'}"
        )
        raise ValueError(f"no canonical image URL for ({os!r}, {version!r}){suggestion}") from None


def cache_path_for(url: str) -> Path:
    name = Path(urlsplit(url).path).name
    if not name:
        raise ValueError(f"cannot derive cache filename from {url!r}")
    return image_cache_dir() / name


def fetch(url: str) -> Path:
    """Download `url` to the image cache, atomically. Idempotent."""
    final = cache_path_for(url)
    if final.exists():
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_suffix(final.suffix + ".part")
    try:
        # Connect must be bounded so a dead host doesn't hang forever; reads
        # are unbounded because cloud images can take minutes on a slow link.
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total_str = resp.headers.get("content-length")
            total = int(total_str) if total_str else None
            columns = (
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            )
            with Progress(*columns, transient=True) as progress:
                task = progress.add_task(f"download {final.name}", total=total)
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        _ = fh.write(chunk)
                        progress.update(task, advance=len(chunk))
        tmp.replace(final)
    except BaseException:
        # Don't leave a partial file at the destination path on any failure.
        tmp.unlink(missing_ok=True)
        raise
    return final


def resolve_image(cfg: VMConfig) -> Path:
    """Return a local Path to the image, fetching/canonicalizing as needed."""
    if cfg.image is None:
        return fetch(canonical_url(cfg.os, cfg.version))
    if cfg.image.startswith(("http://", "https://")):
        return fetch(cfg.image)
    p = Path(cfg.image)
    if not p.exists():
        raise FileNotFoundError(f"image path does not exist: {p}")
    return p
