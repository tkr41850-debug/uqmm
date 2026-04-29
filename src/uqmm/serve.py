"""One-shot HTTP server for the Alpine answers file.

The Alpine pexpect driver types `wget http://10.0.2.2:<port>/answers` at the
live-ISO root prompt. SLiRP routes the guest's request to this server on the
host. After a single successful GET the server shuts down; the thread joins
cleanly so the create flow can move on.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def serve_answers_once(content: str) -> tuple[int, threading.Thread]:
    """Start a daemon thread serving `content` at GET /answers.

    Returns (port, thread). The thread exits after one successful GET to
    /answers; other paths return 404 and don't trigger shutdown. Caller
    should `thread.join(timeout=...)` after the install drives wget.
    """
    payload = content.encode("utf-8")
    # Bind 0.0.0.0 so SLiRP-NATted guest at 10.0.2.x can reach the host at 10.0.2.2.
    server = HTTPServer(("0.0.0.0", 0), _build_handler(payload))
    port = server.server_address[1]
    assert isinstance(port, int)

    def serve() -> None:
        # serve_forever exits when shutdown() is called from a handler.
        server.serve_forever()
        server.server_close()

    thread = threading.Thread(target=serve, daemon=True, name=f"uqmm-answers-{port}")
    thread.start()
    return port, thread


def _build_handler(payload: bytes) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/answers":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            _ = self.wfile.write(payload)
            # Trigger shutdown from a separate thread; serve_forever can't
            # call its own shutdown() from the request thread without
            # deadlocking.
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: object) -> None:
            # Suppress default stderr access log; the install flow is noisy
            # enough already.
            del format, args

    return _Handler


# Ensure socket import is referenced for tooling; reused if we add a probe later.
_ = socket
