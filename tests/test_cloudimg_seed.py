# pycdlib ships no type stubs; turn off the unknown-member noise for this
# read-back test module specifically.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import io
from pathlib import Path

import pycdlib

from uqmm.builders.cloudimg import build_seed_iso


def _read_back(iso_path: Path) -> dict[str, bytes]:
    """Return {filename: contents} for files at the ISO root."""
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(iso_path))
        out: dict[str, bytes] = {}
        for child in iso.list_children(iso_path="/"):
            if child is None or child.is_dir():
                continue
            ident = child.file_identifier().decode("ascii")
            iso_name = ident.split(";", 1)[0]
            buf = io.BytesIO()
            iso.get_file_from_iso_fp(buf, iso_path=f"/{ident}")
            out[iso_name] = buf.getvalue()
        return out
    finally:
        iso.close()


def test_seed_contains_both_files(tmp_path: Path) -> None:
    out = tmp_path / "seed.iso"
    build_seed_iso("#cloud-config\nfoo: bar\n", "instance-id: uqmm-1\n", out)
    assert out.exists()
    contents = _read_back(out)
    blob = b"".join(contents.values())
    # cloud-init reads Joliet which preserves the long names, but the ISO9660
    # layer only stores the 8.3 names. Either way both payloads are present.
    assert b"foo: bar" in blob
    assert b"instance-id: uqmm-1" in blob


def test_seed_joliet_exposes_long_names(tmp_path: Path) -> None:
    # cloud-init mounts the cidata fs via the OS (Joliet preferred); the
    # canonical filenames must appear in the Joliet directory.
    out = tmp_path / "seed.iso"
    build_seed_iso("#cloud-config\nfoo: bar\n", "instance-id: x\n", out)
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(out))
        joliet_names: set[str] = set()
        for child in iso.list_children(joliet_path="/"):
            if child is None or child.is_dir():
                continue
            ident = child.file_identifier().decode("utf-16-be", errors="replace")
            joliet_names.add(ident.split(";", 1)[0])
        assert "user-data" in joliet_names
        assert "meta-data" in joliet_names
    finally:
        iso.close()


def test_seed_volume_label_is_lowercase_cidata(tmp_path: Path) -> None:
    # Per docs/design/toolchain.md gotcha #2: lowercase `cidata` is the
    # forward-compatible volume identifier. Older cloud-init docs say
    # uppercase, but a 2025 deprecation makes lowercase the safe choice.
    out = tmp_path / "seed.iso"
    build_seed_iso("#cloud-config\n", "instance-id: x\n", out)
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(out))
        assert iso.pvd.volume_identifier.rstrip(b" \x00").decode("ascii").strip() == "cidata"
    finally:
        iso.close()
