import contextlib
import http.client
import socket

import pytest

from uqmm.serve import serve_answers_once


def _get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body
    finally:
        conn.close()


def test_serve_returns_content_to_first_get() -> None:
    payload = "KEYMAPOPTS=us\nHOSTNAMEOPTS=test\n"
    port, thread = serve_answers_once(payload)
    try:
        status, body = _get(port, "/answers")
        assert status == 200
        assert body.decode("utf-8") == payload
    finally:
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "server thread must exit after one request"


def test_serve_404s_for_other_paths() -> None:
    port, thread = serve_answers_once("ignored")
    try:
        status, _body = _get(port, "/nope")
        assert status == 404
    finally:
        # The 404 handler doesn't shut down the server — only /answers does.
        # Send a real GET to release the thread for cleanup.
        with contextlib.suppress(ConnectionRefusedError, OSError):
            _get(port, "/answers")
        thread.join(timeout=5.0)


def test_serve_picks_free_port() -> None:
    p1, t1 = serve_answers_once("a")
    p2, t2 = serve_answers_once("b")
    try:
        assert p1 != p2
    finally:
        for port, thread in ((p1, t1), (p2, t2)):
            with contextlib.suppress(ConnectionRefusedError, OSError):
                _get(port, "/answers")
            thread.join(timeout=5.0)


def test_serve_port_falls_in_ephemeral_range() -> None:
    port, thread = serve_answers_once("x")
    try:
        # Should be a real-world ephemeral port (not 0).
        assert 1024 < port < 65536
        # And be alive at that port until first valid request.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(("127.0.0.1", port))
    finally:
        with contextlib.suppress(ConnectionRefusedError, OSError):
            _get(port, "/answers")
        thread.join(timeout=5.0)


@pytest.mark.parametrize("payload", ["", "single-line\n", "two\nlines\n"])
def test_serve_payload_round_trip(payload: str) -> None:
    port, thread = serve_answers_once(payload)
    try:
        _, body = _get(port, "/answers")
        assert body.decode("utf-8") == payload
    finally:
        thread.join(timeout=5.0)
