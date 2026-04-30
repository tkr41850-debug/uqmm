# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false

from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock

import pytest

from uqmm.alpine_drive import _PANIC_PATTERNS, drive_install


class FakeSpawn:
    """Minimal fake of pexpect.SocketSpawn for the drive script."""

    def __init__(self, expect_queue: Iterable[Any]) -> None:
        from collections import deque

        self.expect_queue: deque[Any] = deque(expect_queue)
        self.sent: list[str] = []
        self.closed = False

    def expect(self, pattern: object, timeout: float = -1) -> int:
        del timeout
        if not self.expect_queue:
            raise AssertionError(f"unexpected expect({pattern!r}); queue empty")
        head = self.expect_queue.popleft()
        if head == "TIMEOUT":
            from pexpect import TIMEOUT

            raise TIMEOUT(f"forced timeout on expect({pattern!r})")
        if isinstance(pattern, list):
            assert pattern == [head, *_PANIC_PATTERNS]
            return 0
        assert pattern == head
        return 0

    def sendline(self, line: str) -> None:
        self.sent.append(line)

    def close(self) -> None:
        self.closed = True


def test_drive_install_happy_path() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            r"\nUQMM_INSTALL_DONE",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")

    sent = spawn.sent
    assert sent[0] == "root"
    assert sent[1].startswith("stty cols")
    assert "udhcpc" in sent[2]
    assert sent[3].startswith("wget -O /tmp/answers http://10.0.2.2:9999/answers && ")
    assert "setup-alpine -ef /tmp/answers" in sent[3]
    assert "ERASE_DISKS=/dev/vda" in sent[3]
    assert "echo UQMM_INSTALL_DONE" in sent[3]
    assert sent[4] == "reboot"


def test_drive_install_raises_panic_when_kernel_panic_on_console() -> None:
    from uqmm.alpine_drive import PanicDetected

    spawn = MagicMock()
    # The first expect picks index 1 = "Kernel panic" alternative.
    spawn.expect = MagicMock(return_value=1)
    with pytest.raises(PanicDetected, match="Kernel panic"):
        drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")


def test_drive_install_raises_on_unexpected_timeout() -> None:
    from pexpect import TIMEOUT

    spawn = FakeSpawn(expect_queue=["login: ", "TIMEOUT"])  # times out at the shell prompt

    with pytest.raises(TIMEOUT):
        drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")


def test_drive_install_does_not_send_password_prompts() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            r"\nUQMM_INSTALL_DONE",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")
    assert spawn.sent == [
        "root",
        "stty cols 200",
        "ifconfig eth0 up && udhcpc -i eth0",
        "wget -O /tmp/answers http://10.0.2.2:9999/answers && export ERASE_DISKS=/dev/vda && setup-alpine -ef /tmp/answers && echo UQMM_INSTALL_DONE",  # noqa: E501
        "reboot",
    ]


def test_drive_install_uses_provided_url() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            r"\nUQMM_INSTALL_DONE",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:42424/answers")
    wget_line = spawn.sent[3]
    assert "http://10.0.2.2:42424/answers" in wget_line


def test_drive_install_does_not_close_spawn() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            r"\nUQMM_INSTALL_DONE",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")
    assert not spawn.closed


def test_drive_install_accepts_real_socket_spawn_protocol() -> None:
    mock = MagicMock()
    mock.expect = MagicMock(return_value=0)
    mock.sendline = MagicMock()
    drive_install(mock, answers_url="http://10.0.2.2:1/answers")
    assert mock.expect.call_count >= 6
    assert mock.sendline.call_count >= 5
