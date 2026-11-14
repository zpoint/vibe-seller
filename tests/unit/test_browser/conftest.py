"""Fixtures shared by the browser-backend unit tests."""

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest


class _VersionHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server's handler name
        if self.path != '/json/version':
            self.send_error(404)
            return
        port = self.server.server_address[1]
        body = json.dumps({
            'Browser': 'FakeChrome/1.0',
            'webSocketDebuggerUrl': f'ws://127.0.0.1:{port}/devtools/browser/x',
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def cdp_version_port() -> Iterator[int]:
    """A loopback port that answers ``GET /json/version`` like a browser.

    ``ZiniaoBackend.start`` probes its CDP proxy with a real HTTP request.
    The tests mock the proxy, so with a hard-coded port nothing listens
    there and the outcome was the HOST's choice: refused at once on most
    machines (the probe's warning branch, test passes), but hanging
    until the 5 s timeout on others (the fatal branch, test fails —
    measured on a WSL host, where an unbound loopback connect does not
    refuse). A listener that answers makes the probe deterministic.
    """
    server = ThreadingHTTPServer(('127.0.0.1', 0), _VersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
