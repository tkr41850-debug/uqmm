import asyncio
import socket
import threading

import pytest

from uqmm.ssh import wait_ready


def _serve_banner(port: int, banner: bytes) -> None:
    """One-shot SSH-like server that writes `banner` then closes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        conn, _ = srv.accept()
        with conn:
            conn.sendall(banner)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_wait_ready_succeeds_when_banner_correct() -> None:
    port = _free_port()
    t = threading.Thread(target=_serve_banner, args=(port, b"SSH-2.0-OpenSSH_9.6\r\n"))
    t.start()
    try:
        await wait_ready("127.0.0.1", port, timeout=5.0)
    finally:
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_wait_ready_times_out_when_nothing_listens() -> None:
    port = _free_port()
    # Don't start a server. Tiny timeout so the test stays fast.
    with pytest.raises(TimeoutError):
        await wait_ready("127.0.0.1", port, timeout=0.5)


@pytest.mark.asyncio
async def test_wait_ready_rejects_non_ssh_banner() -> None:
    port = _free_port()
    t = threading.Thread(target=_serve_banner, args=(port, b"HTTP/1.1 200 OK\r\n"))
    t.start()
    try:
        # Non-SSH banner: keep retrying until the deadline.
        with pytest.raises(TimeoutError):
            await wait_ready("127.0.0.1", port, timeout=0.5)
    finally:
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_wait_ready_retries_until_listener_appears() -> None:
    port = _free_port()

    async def delayed_serve() -> None:
        # Wait a bit, then start the listener so the first few attempts fail.
        await asyncio.sleep(0.4)
        await asyncio.to_thread(_serve_banner, port, b"SSH-2.0-OpenSSH_9.6\r\n")

    server_task = asyncio.create_task(delayed_serve())
    try:
        await wait_ready("127.0.0.1", port, timeout=5.0)
    finally:
        await server_task
