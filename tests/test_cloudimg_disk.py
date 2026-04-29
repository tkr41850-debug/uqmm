from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from uqmm.builders.cloudimg import prepare_disk


def test_prepare_disk_runs_create_then_resize(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    base.write_bytes(b"")
    out = tmp_path / "disk.qcow2"

    with patch("uqmm.builders.cloudimg.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        prepare_disk(base, out, size_gb=20)

    assert run.call_count == 2
    create_argv: list[str] = list(run.call_args_list[0].args[0])
    resize_argv: list[str] = list(run.call_args_list[1].args[0])

    # create: qemu-img create -f qcow2 -F qcow2 -b <base> <out>
    assert create_argv[0] == "qemu-img"
    assert create_argv[1] == "create"
    assert "-f" in create_argv and create_argv[create_argv.index("-f") + 1] == "qcow2"
    assert "-F" in create_argv and create_argv[create_argv.index("-F") + 1] == "qcow2"
    assert "-b" in create_argv and create_argv[create_argv.index("-b") + 1] == str(base)
    assert create_argv[-1] == str(out)

    # resize: qemu-img resize <out> 20G
    assert resize_argv[:2] == ["qemu-img", "resize"]
    assert resize_argv[-2] == str(out)
    assert resize_argv[-1] == "20G"


def test_prepare_disk_create_failure_includes_stderr(tmp_path: Path) -> None:
    import subprocess as sp

    base = tmp_path / "base.qcow2"
    base.write_bytes(b"")
    out = tmp_path / "disk.qcow2"

    err = sp.CalledProcessError(1, ["qemu-img", "create"], stderr=b"qemu-img: bad backing file")
    with (
        patch("uqmm.builders.cloudimg.subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="bad backing file"),
    ):
        prepare_disk(base, out, size_gb=20)


def test_prepare_disk_missing_base(tmp_path: Path) -> None:
    out = tmp_path / "disk.qcow2"
    with pytest.raises(FileNotFoundError):
        prepare_disk(tmp_path / "nope", out, size_gb=20)
