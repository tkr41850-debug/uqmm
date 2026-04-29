# pexpect ships no type stubs; turn off the unknown-member noise here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import os
import socket
import threading
from pathlib import Path

import pytest

from uqmm.qemu.serial import open_serial


def _serve_unix(sock_path: Path, scripted_replies: list[bytes], ready: threading.Event) -> None:
    """Bind a unix server, accept one connection, follow a tiny scripted dialog."""
    if sock_path.exists():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        srv.listen(1)
        ready.set()
        conn, _addr = srv.accept()
        with conn:
            for reply in scripted_replies:
                conn.sendall(reply)
                # Drain whatever the client sends back; we don't inspect it
                # in these tests, just keep the channel flowing.
                conn.settimeout(2.0)
                try:
                    _ = conn.recv(4096)
                except (TimeoutError, OSError):
                    break
    finally:
        srv.close()
        if sock_path.exists():
            os.unlink(sock_path)


@pytest.mark.asyncio
async def test_open_serial_returns_spawn_with_log(tmp_path: Path) -> None:
    sock = tmp_path / "serial.sock"
    log = tmp_path / "install.log"

    ready = threading.Event()
    server = threading.Thread(
        target=_serve_unix,
        args=(sock, [b"localhost login: "], ready),
        daemon=True,
    )
    server.start()
    assert ready.wait(timeout=2.0)

    spawn = await open_serial(sock, log)
    try:
        spawn.expect("login: ", timeout=5)
        spawn.sendline("root")
    finally:
        spawn.close()
        server.join(timeout=3.0)

    assert log.exists()
    assert b"login:" in log.read_bytes()


@pytest.mark.asyncio
async def test_open_serial_retries_until_socket_appears(tmp_path: Path) -> None:
    sock = tmp_path / "serial.sock"
    log = tmp_path / "install.log"

    ready = threading.Event()
    # Delayed-start server so the connect-retry path is exercised.
    started = threading.Event()

    def delayed_serve() -> None:
        started.wait(timeout=2.0)
        _serve_unix(sock, [b"# "], ready)

    server = threading.Thread(target=delayed_serve, daemon=True)
    server.start()

    async def kick_off() -> None:
        # Let open_serial spin a few times before the socket exists.
        import asyncio

        await asyncio.sleep(0.3)
        started.set()

    import asyncio

    kicker = asyncio.create_task(kick_off())
    spawn = await open_serial(sock, log, timeout=5.0)
    try:
        spawn.expect("# ", timeout=5)
    finally:
        spawn.close()
        await kicker
        server.join(timeout=3.0)


@pytest.mark.asyncio
async def test_open_serial_times_out_when_socket_never_appears(tmp_path: Path) -> None:
    sock = tmp_path / "never-appears.sock"
    log = tmp_path / "install.log"
    with pytest.raises(TimeoutError):
        await open_serial(sock, log, timeout=0.3)
