"""A tiny fake back office for the live question-park e2e.

One order whose shipping page offers two options — the situation the
task prompt tells the agent to ask about — plus a status page for the
task that runs while the first one waits. Placeholders only; no
marketplace look.

    GET  /orders/ORD-1001/shipping         two options, a Select each
    POST /orders/ORD-1001/shipping/select  option=standard|express
                                           -> CONF-STD-<nonce> / CONF-EXP-<nonce>
    GET  /status                           STATUS-OK-<nonce>

``access_log`` is the ground truth the test asserts on.
"""

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import parse_qs
import uuid

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head><body>{body}</body></html>"""

_SHIPPING = """<h1>acme back office</h1>
<h2>Order ORD-1001 &middot; choose a shipping option</h2>
<table border="1" cellpadding="6">
<tr><th>Option</th><th>Delivery</th><th>Price</th><th></th></tr>
<tr><td>Standard</td><td>5 days</td><td>4.00</td><td>
<form method="post" action="/orders/ORD-1001/shipping/select">
<input type="hidden" name="option" value="standard">
<button type="submit" id="select-standard">Select Standard</button>
</form></td></tr>
<tr><td>Express</td><td>2 days</td><td>9.00</td><td>
<form method="post" action="/orders/ORD-1001/shipping/select">
<input type="hidden" name="option" value="express">
<button type="submit" id="select-express">Select Express</button>
</form></td></tr>
</table>"""


class FakeSellerShipping:
    def __init__(self):
        self.nonce = uuid.uuid4().hex[:6].upper()
        self.access_log: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f'http://127.0.0.1:{self._server.server_port}'

    def code_for(self, option: str) -> str:
        prefix = {'standard': 'CONF-STD', 'express': 'CONF-EXP'}[option]
        return f'{prefix}-{self.nonce}'

    @property
    def status_code(self) -> str:
        return f'STATUS-OK-{self.nonce}'

    def selections(self) -> list[dict]:
        """Every option actually chosen — a POST of the Select form. A
        GET of the same URL (a browser re-loading the confirmation page)
        chooses nothing."""
        return [
            e
            for e in self.access_log
            if e['method'] == 'POST' and e['path'].endswith('/select')
        ]

    def start(self) -> 'FakeSellerShipping':
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _record(self, body: str = '') -> None:
                fake.access_log.append({
                    'at': datetime.now(UTC).isoformat(),
                    'method': self.command,
                    'path': self.path,
                    'body': body,
                })

            def _send(self, status: int, title: str, body: str) -> None:
                data = _PAGE.format(title=title, body=body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._record()
                if self.path.startswith('/orders/ORD-1001/shipping'):
                    self._send(200, 'Order ORD-1001 shipping', _SHIPPING)
                elif self.path.startswith('/status'):
                    self._send(
                        200,
                        'Status',
                        f'<h1>System status</h1><p id="code">'
                        f'{fake.status_code}</p>',
                    )
                else:
                    self._send(404, 'Not found', '<h1>Not found</h1>')

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length).decode() if length else ''
                self._record(body)
                if self.path != '/orders/ORD-1001/shipping/select':
                    self._send(404, 'Not found', '<h1>Not found</h1>')
                    return
                option = (parse_qs(body).get('option') or [''])[0].lower()
                if option not in ('standard', 'express'):
                    self._send(400, 'Bad request', '<h1>Unknown option</h1>')
                    return
                self._send(
                    200,
                    'Shipping confirmed',
                    f'<h1>Shipping confirmed</h1><p>Option: {option}</p>'
                    f'<p id="confirmation">{fake.code_for(option)}</p>',
                )

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
