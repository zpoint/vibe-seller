"""A tiny stand-in ad console a real agent can drive in CI.

Enough of an ads back-office to exercise the scoping contract end to end
without a live advertiser account: a revenue summary, a campaign list
with TWO campaigns, and a per-campaign keyword table. That is the whole
point — the test asks about only ONE of the two, so "did the scope stay
narrow?" has an observable answer.

Deliberately boring HTML. The agent's browser work is not what is under
test here; what is under test is whether the phase it declares matches
what the user asked for, and whether the console it produces is bounded
by that. A page that needs clever scraping would fail for reasons that
have nothing to do with the contract.

All figures are round and invented. Campaign ids follow the placeholder
shape used throughout the repo.
"""

from __future__ import annotations

import http.server
import threading

# Two campaigns. The tests ask about WIDGET-006 only; SPROCKET-009 is the
# control that must never appear in a scoped review.
CAMPAIGN_A = 'A1234567'
CAMPAIGN_A_NAME = 'widget-006 manual'
CAMPAIGN_B = 'A7654321'
CAMPAIGN_B_NAME = 'sprocket-009 manual'

_DASHBOARD = """<!doctype html><html><head><title>Ad Console</title></head>
<body>
<h1>Ad Console — acme</h1>
<h2>Last 30 days</h2>
<table id="summary" border="1">
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Spend</td><td>300.00</td></tr>
  <tr><td>Revenue</td><td>1200.00</td></tr>
  <tr><td>Orders</td><td>40</td></tr>
  <tr><td>ROAS</td><td>4.00</td></tr>
</table>
<p><a href="/campaigns">Campaigns</a></p>
</body></html>
"""

_CAMPAIGNS = f"""<!doctype html><html><head><title>Campaigns</title></head>
<body>
<h1>Campaigns</h1>
<p>Live 2 / Paused 0 / All 2</p>
<table id="campaigns" border="1">
  <tr><th>Campaign ID</th><th>Name</th><th>Spend</th><th>Revenue</th>
      <th>Orders</th><th>ROAS</th></tr>
  <tr><td>{CAMPAIGN_A}</td><td><a href="/campaign/{CAMPAIGN_A}">
      {CAMPAIGN_A_NAME}</a></td>
      <td>200.00</td><td>600.00</td><td>20</td><td>3.00</td></tr>
  <tr><td>{CAMPAIGN_B}</td><td><a href="/campaign/{CAMPAIGN_B}">
      {CAMPAIGN_B_NAME}</a></td>
      <td>100.00</td><td>600.00</td><td>20</td><td>6.00</td></tr>
</table>
</body></html>
"""


def _campaign_page(cid: str, name: str, rows: list[tuple]) -> str:
    body = '\n'.join(
        '<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows
    )
    return f"""<!doctype html><html><head><title>{name}</title></head>
<body>
<h1>{name}</h1>
<p>Campaign ID: {cid}</p>
<p>Status: Live &middot; Daily budget: 20.00</p>
<h2>Keywords</h2>
<table id="keywords" border="1">
  <tr><th>Keyword</th><th>Match</th><th>Bid</th><th>Clicks</th>
      <th>Spend</th><th>Orders</th><th>Sales</th><th>ROAS</th></tr>
  {body}
</table>
</body></html>
"""


_PAGES = {
    '/': _DASHBOARD,
    '/index.html': _DASHBOARD,
    '/campaigns': _CAMPAIGNS,
    f'/campaign/{CAMPAIGN_A}': _campaign_page(
        CAMPAIGN_A,
        CAMPAIGN_A_NAME,
        [
            ('widget red', 'Exact', '1.00', 60, '120.00', 12, '360.00', '3.00'),
            ('widget blue', 'Exact', '0.80', 40, '80.00', 8, '240.00', '3.00'),
        ],
    ),
    f'/campaign/{CAMPAIGN_B}': _campaign_page(
        CAMPAIGN_B,
        CAMPAIGN_B_NAME,
        [
            (
                'sprocket small',
                'Exact',
                '0.50',
                50,
                '100.00',
                20,
                '600.00',
                '6.00',
            ),
        ],
    ),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib interface)
        page = _PAGES.get(self.path.rstrip('/') or '/')
        if page is None:
            self.send_error(404)
            return
        payload = page.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass  # keep CI output readable


def serve() -> tuple[str, http.server.HTTPServer]:
    """Start the console on a free port. Returns ``(base_url, server)``."""
    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_address[1]}', server
