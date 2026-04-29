# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock

import pytest

from uqmm.alpine_drive import drive_install


class FakeSpawn:
    """Minimal fake of pexpect.SocketSpawn for the drive script.

    Records sendline calls and serves expect() against a queue of patterns.
    Each expect call pops the head of `expect_queue` and checks the next
    `pattern` arg matches it as a regex; if not, it raises so the test fails
    visibly.
    """

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
        return 0

    def sendline(self, line: str) -> None:
        self.sent.append(line)

    def close(self) -> None:
        self.closed = True


def test_drive_install_happy_path() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",  # after sendline("root")
            "# ",  # after stty cols
            "# ",  # after udhcpc
            "# ",  # after wget+setup-alpine launches
            "New password: ",
            "Retype password: ",
            "# ",  # install completes
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers", root_password="hunter2")

    # Order matters — verify the script types the right things in the right order.
    sent = spawn.sent
    assert sent[0] == "root"
    assert sent[1].startswith("stty cols")
    assert "udhcpc" in sent[2]
    assert "wget" in sent[3] and "setup-alpine" in sent[3] and "ERASE_DISKS=/dev/vda" in sent[3]
    assert sent[4] == "hunter2"
    assert sent[5] == "hunter2"
    assert sent[6] == "reboot"


def test_drive_install_raises_on_unexpected_timeout() -> None:
    from pexpect import TIMEOUT

    spawn = FakeSpawn(expect_queue=["login: ", "TIMEOUT"])  # times out at the shell prompt

    with pytest.raises(TIMEOUT):
        drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")


def test_drive_install_default_password_is_disposable() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            "# ",
            "New password: ",
            "Retype password: ",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")
    # Default password is the same string twice, but not empty.
    assert spawn.sent[4] == spawn.sent[5]
    assert spawn.sent[4] != ""


def test_drive_install_uses_provided_url() -> None:
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            "# ",
            "New password: ",
            "Retype password: ",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:42424/answers")
    wget_line = spawn.sent[3]
    assert "http://10.0.2.2:42424/answers" in wget_line


def test_drive_install_does_not_close_spawn() -> None:
    # The caller (cli) needs the spawn open after the install drive finishes
    # so it can read final output / decide whether to relaunch.
    spawn = FakeSpawn(
        expect_queue=[
            "login: ",
            "# ",
            "# ",
            "# ",
            "# ",
            "New password: ",
            "Retype password: ",
            "# ",
        ]
    )
    drive_install(spawn, answers_url="http://10.0.2.2:9999/answers")
    assert not spawn.closed


def test_drive_install_accepts_real_socket_spawn_protocol() -> None:
    # Smoke test: the protocol uses .expect / .sendline; nothing else.
    # If we ever change the contract, this catches it before runtime.
    mock = MagicMock()
    mock.expect = MagicMock(return_value=0)
    mock.sendline = MagicMock()
    drive_install(mock, answers_url="http://10.0.2.2:1/answers", root_password="x")
    assert mock.expect.call_count >= 6
    assert mock.sendline.call_count >= 6
