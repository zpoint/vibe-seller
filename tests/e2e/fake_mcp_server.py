"""A stand-in EXTERNAL MCP server — what an admin adds as a custom MCP.

Serves one tool on every transport a custom server may use, so the
tests can add it the way a user adds a vendor's server and then check
what reached the agent:

* Streamable HTTP — ``POST /mcp`` (JSON answers), Bearer-token auth;
* legacy SSE — ``GET /sse`` + ``POST /messages?session=…``;
* stdio — ``python -m tests.e2e.fake_mcp_server --stdio``.

The tool, ``lookup_widget_code``, answers with :data:`ANSWER` — a value
that exists nowhere else, so a task result carrying it can only have
come through this server.

Started in-process by the tests (:func:`serve` on port 0) — the
probe, the workflow tests and the e2e all talk to the URL it returns.
Standalone for a manual run:

    python -m tests.e2e.fake_mcp_server --port 7792
"""

from __future__ import annotations

import argparse
import http.server
import json
import queue
import sys
import threading
from urllib.parse import parse_qs, urlparse
import uuid

#: The Bearer token the HTTP transports require.
TOKEN = 'e2e-mcp-token-0001'
SERVER_NAME = 'fake-widget-mcp'
TOOL_NAME = 'lookup_widget_code'
#: What the tool answers — appears in no prompt and no other fixture.
ANSWER = 'WGT-CODE-58213'

_TOOLS = [
    {
        'name': TOOL_NAME,
        'description': (
            'Look up the internal catalog code of a widget by its SKU. '
            'The code is only available from this tool.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'sku': {'type': 'string'}},
            'required': ['sku'],
        },
    }
]


def handle(msg: dict) -> dict | None:
    """The JSON-RPC answer to *msg* (``None`` for a notification)."""
    if 'id' not in msg:
        return None
    method = msg.get('method')
    if method == 'initialize':
        requested = (msg.get('params') or {}).get('protocolVersion')
        result: dict = {
            'protocolVersion': requested or '2025-06-18',
            'capabilities': {'tools': {}},
            'serverInfo': {'name': SERVER_NAME, 'version': '1.0'},
        }
    elif method == 'tools/list':
        result = {'tools': _TOOLS}
    elif method == 'tools/call':
        params = msg.get('params') or {}
        sku = (params.get('arguments') or {}).get('sku', '')
        result = {
            'content': [
                {'type': 'text', 'text': f'Widget {sku}: code {ANSWER}'}
            ]
        }
    elif method == 'ping':
        result = {}
    else:
        return {
            'jsonrpc': '2.0',
            'id': msg['id'],
            'error': {'code': -32601, 'message': f'Unknown method {method}'},
        }
    return {'jsonrpc': '2.0', 'id': msg['id'], 'result': result}


class FakeMcpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.sse_sessions: dict[str, queue.Queue] = {}

    def record(self, msg: dict) -> None:
        with self._lock:
            self.calls.append(msg)

    def received(self) -> list[dict]:
        """Every JSON-RPC message received, in order."""
        with self._lock:
            return list(self.calls)

    def tool_calls(self) -> list[dict]:
        """Every ``tools/call`` received, on any transport."""
        with self._lock:
            return [c for c in self.calls if c.get('method') == 'tools/call']


class _Handler(http.server.BaseHTTPRequestHandler):
    server: FakeMcpServer

    def log_message(self, *args):  # quiet
        pass

    def _authorized(self) -> bool:
        if self.headers.get('Authorization') == f'Bearer {TOKEN}':
            return True
        self._send(401, {'error': 'unauthorized'})
        return False

    def _send(self, status: int, body: dict | None, headers=None) -> None:
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(length) or b'{}')

    def do_POST(self):
        url = urlparse(self.path)
        if not self._authorized():
            return
        msg = self._body()
        self.server.record(msg)
        answer = handle(msg)
        if url.path == '/mcp':
            if answer is None:
                self._send(202, None)
            else:
                self._send(200, answer, {'Mcp-Session-Id': 'fake-session'})
            return
        if url.path == '/messages':
            session = parse_qs(url.query).get('session', [''])[0]
            q = self.server.sse_sessions.get(session)
            if q is None:
                self._send(404, {'error': 'unknown session'})
                return
            if answer is not None:
                q.put(answer)
            self._send(202, None)
            return
        self._send(404, {'error': 'not found'})

    def do_GET(self):
        if urlparse(self.path).path != '/sse':
            self._send(404, {'error': 'not found'})
            return
        if not self._authorized():
            return
        session = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        self.server.sse_sessions[session] = q
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self._event('endpoint', f'/messages?session={session}')
            while True:
                try:
                    answer = q.get(timeout=30)
                except queue.Empty:
                    break
                self._event('message', json.dumps(answer))
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.sse_sessions.pop(session, None)

    def do_DELETE(self):
        self._send(200, {})

    def _event(self, event: str, data: str) -> None:
        self.wfile.write(f'event: {event}\ndata: {data}\n\n'.encode())
        self.wfile.flush()


def serve(port: int = 0) -> FakeMcpServer:
    """Start the server on 127.0.0.1 (port 0 = any free port)."""
    server = FakeMcpServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def url_of(server: FakeMcpServer, path: str = '/mcp') -> str:
    return f'http://127.0.0.1:{server.server_address[1]}{path}'


def http_config(server: FakeMcpServer) -> dict:
    """The JSON a vendor would publish for this server (no ``type`` —
    like the real ones, which Claude Code would refuse as-is)."""
    return {
        'url': url_of(server),
        'headers': {'Authorization': f'Bearer {TOKEN}'},
    }


def stdio_config() -> dict:
    return {
        'command': sys.executable,
        'args': ['-m', 'tests.e2e.fake_mcp_server', '--stdio'],
    }


def _run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        answer = handle(msg)
        if answer is not None:
            sys.stdout.write(json.dumps(answer) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--port', type=int, default=7792)
    opts = parser.parse_args()
    if opts.stdio:
        _run_stdio()
    else:
        import time

        srv = serve(opts.port)
        print(f'fake MCP server on {url_of(srv)}', flush=True)
        while True:
            time.sleep(60)
