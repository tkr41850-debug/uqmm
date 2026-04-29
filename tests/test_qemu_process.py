import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm.qemu.process import launch


@pytest.mark.asyncio
async def test_C9_pidfile_atomic_via_tmp_rename(tmp_path: Path) -> None:
    pidfile = tmp_path / "qemu.pid"
    stderr_log = tmp_path / "stderr.log"
    written_paths: list[str] = []
    original_write = Path.write_text

    def track_write(self: Path, text: str, *args: object, **kwargs: object) -> None:
        written_paths.append(str(self))
        original_write(self, text, *args, **kwargs)  # type: ignore[arg-type]

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.stderr = AsyncMock()
    fake_proc.stderr.readline = AsyncMock(return_value=b"")
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch(
            "uqmm.qemu.process.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ),
        patch.object(Path, "write_text", track_write),
    ):
        await launch(["qemu-system-x86_64"], pidfile=pidfile, stderr_log=stderr_log)

    # write_text called once on the .tmp file; os.replace does the rename
    assert len(written_paths) == 1
    assert written_paths[0].endswith(".tmp")
    assert pidfile.exists()
    assert not Path(written_paths[0]).exists()  # noqa: ASYNC240
    assert pidfile.read_text().strip() == "12345"


@pytest.mark.asyncio
async def test_launch_writes_pidfile(tmp_path: Path) -> None:
    pidfile = tmp_path / "qemu.pid"
    stderr_log = tmp_path / "stderr.log"

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.stderr = AsyncMock()
    fake_proc.stderr.readline = AsyncMock(return_value=b"")  # EOF immediately
    fake_proc.wait = AsyncMock(return_value=0)

    with patch(
        "uqmm.qemu.process.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as mock_spawn:
        proc = await launch(
            ["qemu-system-x86_64", "-nographic"], pidfile=pidfile, stderr_log=stderr_log
        )

    mock_spawn.assert_called_once()
    # First argv element is the command
    assert mock_spawn.call_args.args[0] == "qemu-system-x86_64"
    assert pidfile.exists()
    assert pidfile.read_text().strip() == "12345"
    assert proc is fake_proc


@pytest.mark.asyncio
async def test_launch_drains_stderr_to_log(tmp_path: Path) -> None:
    pidfile = tmp_path / "qemu.pid"
    stderr_log = tmp_path / "stderr.log"

    lines = iter([b"qemu warning: foo\n", b"qemu warning: bar\n", b""])  # last empty = EOF

    fake_proc = MagicMock()
    fake_proc.pid = 999
    fake_proc.stderr = AsyncMock()
    fake_proc.stderr.readline = AsyncMock(side_effect=lambda: next(lines))
    fake_proc.wait = AsyncMock(return_value=0)

    with patch(
        "uqmm.qemu.process.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ):
        await launch(["qemu-system-x86_64"], pidfile=pidfile, stderr_log=stderr_log)
        # Give the drain task a tick to flush.
        await asyncio.sleep(0.05)

    captured = stderr_log.read_bytes()
    assert b"qemu warning: foo" in captured
    assert b"qemu warning: bar" in captured
