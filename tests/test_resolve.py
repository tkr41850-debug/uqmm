from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from uqmm import resolve
from uqmm.config import VMConfig


def test_canonical_url_alpine() -> None:
    url = resolve.canonical_url("alpine", "3.21")
    assert url.startswith("https://dl-cdn.alpinelinux.org/")
    assert "alpine-virt-3.21" in url
    assert url.endswith(".iso")


def test_canonical_url_debian_13() -> None:
    url = resolve.canonical_url("debian", "13")
    assert "cloud.debian.org" in url
    assert "trixie" in url
    assert url.endswith("genericcloud-amd64.qcow2")


def test_canonical_url_ubuntu_2404() -> None:
    url = resolve.canonical_url("ubuntu", "24.04")
    assert "cloud-images.ubuntu.com" in url
    assert "noble" in url
    assert url.endswith("server-cloudimg-amd64.img")


def test_canonical_url_unknown_combo_raises() -> None:
    with pytest.raises(ValueError, match="no canonical image"):
        resolve.canonical_url("alpine", "2.7")
    with pytest.raises(ValueError, match="no canonical image"):
        resolve.canonical_url("debian", "10")


def test_cache_path_for_uses_basename(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = resolve.cache_path_for("https://example.com/path/to/image-1.2.qcow2")
    assert p == tmp_path / "uqmm" / "images" / "image-1.2.qcow2"


def test_fetch_cache_hit_skips_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = tmp_path / "uqmm" / "images"
    cache.mkdir(parents=True)
    existing = cache / "image.qcow2"
    existing.write_bytes(b"already here")

    with patch("uqmm.resolve.httpx.stream") as mock_stream:
        path = resolve.fetch("https://example.com/image.qcow2")

    assert path == existing
    assert path.read_bytes() == b"already here"
    mock_stream.assert_not_called()


def test_fetch_cache_miss_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    chunks = [b"hello ", b"world"]

    response = MagicMock()
    response.headers = {"content-length": str(sum(len(c) for c in chunks))}

    def iter_bytes(chunk_size: int | None = None) -> "object":
        del chunk_size
        return iter(chunks)

    response.iter_bytes = iter_bytes
    response.raise_for_status = MagicMock()

    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=response)
    stream_ctx.__exit__ = MagicMock(return_value=False)

    with patch("uqmm.resolve.httpx.stream", return_value=stream_ctx) as mock_stream:
        path = resolve.fetch("https://example.com/img.qcow2")

    mock_stream.assert_called_once()
    assert path.exists()
    assert path.read_bytes() == b"hello world"
    assert path == tmp_path / "uqmm" / "images" / "img.qcow2"


def test_fetch_atomic_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    response = MagicMock()
    response.headers = {}

    def boom(chunk_size: int | None = None):
        yield b"part"
        raise RuntimeError("network died")

    response.iter_bytes = boom
    response.raise_for_status = MagicMock()
    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=response)
    stream_ctx.__exit__ = MagicMock(return_value=False)

    with (
        patch("uqmm.resolve.httpx.stream", return_value=stream_ctx),
        pytest.raises(RuntimeError, match="network died"),
    ):
        resolve.fetch("https://example.com/broken.qcow2")

    final = tmp_path / "uqmm" / "images" / "broken.qcow2"
    assert not final.exists(), "partial download must not be promoted to cache path"


def test_resolve_image_explicit_local_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    local = tmp_path / "my.qcow2"
    local.write_bytes(b"data")
    cfg = VMConfig(name="vm1", os="debian", version="13", image=str(local))
    assert resolve.resolve_image(cfg) == local


def test_resolve_image_local_path_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cfg = VMConfig(name="vm1", os="debian", version="13", image=str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        resolve.resolve_image(cfg)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_resolve_image_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scheme: str) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    url = f"{scheme}://example.com/x.qcow2"
    cfg = VMConfig(name="vm1", os="debian", version="13", image=url)
    with patch("uqmm.resolve.fetch") as mock_fetch:
        mock_fetch.return_value = tmp_path / "fake"
        result = resolve.resolve_image(cfg)
    mock_fetch.assert_called_once_with(url)
    assert result == tmp_path / "fake"


def test_resolve_image_default_uses_canonical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cfg = VMConfig(name="vm1", os="debian", version="13")
    with patch("uqmm.resolve.fetch") as mock_fetch:
        mock_fetch.return_value = tmp_path / "fake"
        resolve.resolve_image(cfg)
    called_url = mock_fetch.call_args[0][0]
    assert "cloud.debian.org" in called_url
