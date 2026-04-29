import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uqmm.qemu import qmp


@pytest.mark.asyncio
async def test_connect_retries_until_socket_appears(tmp_path: Path) -> None:
    sock = tmp_path / "qmp.sock"
    fake_client = MagicMock()
    fake_client.connect = AsyncMock(side_effect=[FileNotFoundError, FileNotFoundError, None])

    with patch("uqmm.qemu.qmp.QMPClient", return_value=fake_client):
        client = await qmp.connect(sock, timeout=5.0)

    assert client is fake_client
    assert fake_client.connect.await_count == 3


@pytest.mark.asyncio
async def test_connect_times_out(tmp_path: Path) -> None:
    sock = tmp_path / "qmp.sock"
    fake_client = MagicMock()
    fake_client.connect = AsyncMock(side_effect=FileNotFoundError)

    with (
        patch("uqmm.qemu.qmp.QMPClient", return_value=fake_client),
        pytest.raises(TimeoutError),
    ):
        await qmp.connect(sock, timeout=0.2)


@pytest.mark.asyncio
async def test_system_powerdown_sends_correct_command() -> None:
    fake_client = MagicMock()
    fake_client.execute = AsyncMock(return_value={})

    await qmp.system_powerdown(fake_client)

    fake_client.execute.assert_awaited_once_with("system_powerdown")


@pytest.mark.asyncio
async def test_quit_sends_correct_command() -> None:
    fake_client = MagicMock()
    fake_client.execute = AsyncMock(return_value={})

    await qmp.quit(fake_client)

    fake_client.execute.assert_awaited_once_with("quit")


class _FakeEventListener:
    """Stand-in for qemu.qmp.EventListener: sync ctx mgr, async iterator."""

    def __init__(self, events: AsyncIterator[dict[str, object]]) -> None:
        self._events: AsyncIterator[dict[str, object]] = events

    def __enter__(self) -> "_FakeEventListener":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        return self._events


@pytest.mark.asyncio
async def test_wait_shutdown_returns_true_on_event() -> None:
    fake_client = MagicMock()

    async def fake_listen() -> AsyncIterator[dict[str, object]]:
        yield {"event": "RESET"}
        yield {"event": "SHUTDOWN"}

    fake_client.listener = MagicMock(return_value=_FakeEventListener(fake_listen()))

    result = await qmp.wait_shutdown(fake_client, timeout=5.0)
    assert result is True


@pytest.mark.asyncio
async def test_wait_shutdown_times_out() -> None:
    fake_client = MagicMock()

    async def slow_listen() -> AsyncIterator[dict[str, object]]:
        await asyncio.sleep(10)
        yield {"event": "SHUTDOWN"}

    fake_client.listener = MagicMock(return_value=_FakeEventListener(slow_listen()))

    result = await qmp.wait_shutdown(fake_client, timeout=0.1)
    assert result is False
