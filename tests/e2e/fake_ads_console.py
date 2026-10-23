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

**Credible data, no marketplace branding.** The e2e store is an Amazon
US store (``StoreCreate`` defaults), so a model MAY bring in
``amazon-ads`` and its server-side completeness check; the figures must
then be ones a real console could show: every keyword's CPC (spend /
clicks) at or below its bid, a search-term table per campaign whose spend
adds up to the keyword spend, a currency, and an active-campaign count on
the list page. An earlier cut charged 2.00 a click on a 1.00 bid and had
no search terms; the check rightly refused every report built on it.

But the pages must not CLAIM to be an Amazon console. A later cut
labelled them "Sponsored Products · Amazon US marketplace", and agents
read that as a cue: one said "the console identifies as Sponsored
Products / Amazon US, so let me load the amazon-ads skill", and a "just
tell me the numbers" lookup became a full audit with a reviewer — 15 to
25 minutes on the CI models. These tests are about SCOPE (did the agent
stay on the asked-for campaign), and the request never asks for an Amazon
audit, so the fixture must not invite one.
``tests/unit/test_fake_ads_console.py`` pins both halves.
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
<p>All amounts in USD</p>
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
<p>USD &middot; last 30 days</p>
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


def _table(table_id: str, headers: tuple, rows: list[tuple]) -> str:
    head = ''.join(f'<th>{h}</th>' for h in headers)
    body = '\n'.join(
        '<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows
    )
    return (
        f'<table id="{table_id}" border="1">\n<tr>{head}</tr>\n{body}\n</table>'
    )


KEYWORD_HEADERS = (
    'Keyword',
    'Match',
    'Bid',
    'Clicks',
    'Spend',
    'Orders',
    'Sales',
    'ROAS',
)
SEARCH_TERM_HEADERS = (
    'Search term',
    'Matched keyword',
    'Clicks',
    'Spend',
    'Orders',
    'Sales',
)


def _campaign_page(
    cid: str, name: str, keywords: list[tuple], search_terms: list[tuple]
) -> str:
    return f"""<!doctype html><html><head><title>{name}</title></head>
<body>
<h1>{name}</h1>
<p>Campaign ID: {cid}</p>
<p>Status: Live &middot; Daily budget: 20.00 &middot; USD &middot; last 30
days</p>
<h2>Keywords</h2>
{_table('keywords', KEYWORD_HEADERS, keywords)}
<h2>Search terms</h2>
{_table('search-terms', SEARCH_TERM_HEADERS, search_terms)}
</body></html>
"""


# Per campaign: (keyword rows, search-term rows). Spend per campaign
# matches the list page (200.00 / 100.00); search-term spend sums to it.
CAMPAIGN_DATA = {
    CAMPAIGN_A: (
        CAMPAIGN_A_NAME,
        [
            (
                'widget red',
                'Exact',
                '1.50',
                100,
                '120.00',
                12,
                '360.00',
                '3.00',
            ),
            ('widget blue', 'Exact', '1.00', 100, '80.00', 8, '240.00', '3.00'),
        ],
        [
            ('red widget', 'widget red', 60, '72.00', 8, '240.00'),
            ('widget red large', 'widget red', 40, '48.00', 4, '120.00'),
            ('blue widget', 'widget blue', 100, '80.00', 8, '240.00'),
        ],
    ),
    CAMPAIGN_B: (
        CAMPAIGN_B_NAME,
        [
            (
                'sprocket small',
                'Exact',
                '1.00',
                125,
                '100.00',
                20,
                '600.00',
                '6.00',
            ),
        ],
        [('small sprocket', 'sprocket small', 125, '100.00', 20, '600.00')],
    ),
}

_PAGES = {
    '/': _DASHBOARD,
    '/index.html': _DASHBOARD,
    '/campaigns': _CAMPAIGNS,
    **{
        f'/campaign/{cid}': _campaign_page(cid, name, kws, terms)
        for cid, (name, kws, terms) in CAMPAIGN_DATA.items()
    },
}


class ConsoleServer(http.server.HTTPServer):
    """The console, plus a log of every page it actually served.

    This log is the ground truth for "did the scope hold?". The agent's
    report is prose: it can say it left a campaign alone while having
    read it, or say nothing at all about what it opened. What the server
    was asked for cannot be talked around — a review scoped to one
    campaign must never fetch the other one's page.

    Reads and writes come from different threads (the handler runs in
    the serving thread, assertions on the test's), hence the lock.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hits: list[str] = []
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}'

    def record(self, path: str) -> None:
        with self._lock:
            self.hits.append(path)

    def fetched_campaign(self, cid: str) -> bool:
        """Was this campaign's DETAIL page opened?

        Only the detail page counts. The campaign list names both, so
        reading it is not a scope breach — going one click deeper is.
        """
        want = f'/campaign/{cid}'
        with self._lock:
            return any(h.rstrip('/') == want for h in self.hits)

    def served(self) -> list[str]:
        with self._lock:
            return list(self.hits)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib interface)
        self.server.record(self.path)
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


def serve() -> ConsoleServer:
    """Start the console on a free port."""
    server = ConsoleServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
