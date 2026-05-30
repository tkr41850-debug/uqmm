import asyncio
import os
import socket
import threading

import pytest

from uqmm.ssh import wait_ready, wait_ready_or_pid_stop


def _serve_banner(srv: socket.socket, banner: bytes) -> None:
    """Accept one connection on `srv`, write `banner`, then return.

    Caller owns `srv` (bind/listen and final close). If the caller closes
    `srv` while `accept()` is blocked — the failure-path cleanup when the
    client never connects — the syscall raises OSError and we exit cleanly
    so the worker thread can be joined.
    """
    try:
        conn, _ = srv.accept()
    except OSError:
        return
    with conn:
        conn.sendall(banner)


def _bound_listener() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_wait_ready_succeeds_when_banner_correct() -> None:
    srv = _bound_listener()
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=_serve_banner, args=(srv, b"SSH-2.0-OpenSSH_9.6\r\n"), daemon=True
    )
    t.start()
    try:
        await wait_ready("127.0.0.1", port, timeout=5.0)
    finally:
        srv.close()
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_wait_ready_times_out_when_nothing_listens() -> None:
    port = _free_port()
    # Don't start a server. Tiny timeout so the test stays fast.
    with pytest.raises(TimeoutError):
        await wait_ready("127.0.0.1", port, timeout=0.5)


@pytest.mark.asyncio
async def test_wait_ready_rejects_non_ssh_banner() -> None:
    # Pre-bind so wait_ready's first connect can never lose a race against
    # the worker's bind/listen — otherwise the worker stays blocked in
    # accept() and (formerly, as a non-daemon thread) hung pytest at exit.
    srv = _bound_listener()
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=_serve_banner, args=(srv, b"HTTP/1.1 200 OK\r\n"), daemon=True
    )
    t.start()
    try:
        with pytest.raises(TimeoutError):
            await wait_ready("127.0.0.1", port, timeout=0.5)
    finally:
        srv.close()
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_wait_ready_retries_until_listener_appears() -> None:
    port = _free_port()

    async def delayed_serve() -> None:
        # Bind late on purpose — the first connect attempts must see
        # ECONNREFUSED so the retry path in wait_ready is exercised.
        await asyncio.sleep(0.4)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            await asyncio.to_thread(_serve_banner, srv, b"SSH-2.0-OpenSSH_9.6\r\n")
        finally:
            srv.close()

    server_task = asyncio.create_task(delayed_serve())
    try:
        await wait_ready("127.0.0.1", port, timeout=5.0)
    finally:
        await server_task


@pytest.mark.asyncio
async def test_wait_ready_none_timeout_succeeds() -> None:
    """wait_ready with timeout=None succeeds when a banner arrives."""
    srv = _bound_listener()
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=_serve_banner, args=(srv, b"SSH-2.0-OpenSSH_9.6\r\n"), daemon=True
    )
    t.start()
    try:
        await wait_ready("127.0.0.1", port, timeout=None)
    finally:
        srv.close()
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_wait_ready_or_pid_stop_raises_on_dead_pid(tmp_path: pytest.TempPathFactory) -> None:
    """wait_ready_or_pid_stop raises RuntimeError when the PID is dead."""
    pidfile = tmp_path / "qemu.pid"
    pidfile.write_text("99999999\n")
    with pytest.raises(RuntimeError, match="QEMU process died"):
        await wait_ready_or_pid_stop("127.0.0.1", 0, pidfile)


@pytest.mark.asyncio
async def test_wait_ready_or_pid_stop_succeeds(tmp_path: pytest.TempPathFactory) -> None:
    """wait_ready_or_pid_stop returns when SSH banner appears before PID dies."""
    pidfile = tmp_path / "qemu.pid"
    pidfile.write_text(f"{os.getpid()}\n")
    srv = _bound_listener()
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=_serve_banner, args=(srv, b"SSH-2.0-OpenSSH_9.6\r\n"), daemon=True
    )
    t.start()
    try:
        await wait_ready_or_pid_stop("127.0.0.1", port, pidfile)
    finally:
        srv.close()
        t.join(timeout=2.0)
