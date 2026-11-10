"""A tiny stand-in ad console a real agent can drive in CI.

Enough of an ads back-office to exercise the scoping contract end to end
without a live advertiser account: a revenue summary, a campaign list
with TWO campaigns, and a per-campaign keyword table. That is the whole
point — the test asks about only ONE of the two, so "did the scope stay
narrow?" has an observable answer.

Deliberately boring HTML. The agent's browser work is not what is under
test here; what is under test is whether the phase it declares matches
what the user asked for, and whether the report it produces is bounded
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
tell me the numbers" lookup became a full audit — 15 to
25 minutes on the CI models. These tests are about SCOPE (did the agent
stay on the asked-for campaign), and the request never asks for an Amazon
audit, so the fixture must not invite one.
``tests/unit/test_fake_ads_console.py`` pins both halves.
"""

from __future__ import annotations

import http.server
import threading
import time
import urllib.parse

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


def _edit_form(cid: str) -> str:
    """One form to change a keyword's bid or pause it — the write path."""
    return f"""<h2>Edit keyword</h2>
<form id="edit-keyword" method="post" action="/campaign/{cid}/keyword">
  <label>Keyword <input name="keyword"></label>
  <label>New bid <input name="bid"></label>
  <label>Action <select name="action">
    <option value="bid">Set bid</option>
    <option value="pause">Pause</option>
  </select></label>
  <button type="submit">Apply</button>
</form>"""


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
{_edit_form(cid)}
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

# A third campaign only reachable through "Load more" (``serve(paged=True)``):
# a list read without paging misses it, the way noon's lazy list did.
CAMPAIGN_C = 'A2468024'
CAMPAIGN_C_NAME = 'gizmo-012 auto'
# ``gizmo mini`` sits at ACOS 21%: the default rule (no lowering under
# 25%) holds its bid, a store standard of 20% lets it be lowered.
CAMPAIGN_C_DATA = (
    CAMPAIGN_C_NAME,
    [
        ('gizmo', 'Broad', '0.80', 50, '30.00', 3, '90.00', '3.00'),
        ('gizmo mini', 'Exact', '1.20', 25, '25.00', 4, '119.00', '4.76'),
    ],
    [
        ('gizmo case', 'gizmo', 50, '30.00', 3, '90.00'),
        ('mini gizmo', 'gizmo mini', 25, '25.00', 4, '119.00'),
    ],
)
_CAMPAIGNS_PAGED = _CAMPAIGNS.replace(
    'Live 2 / Paused 0 / All 2', 'Live 3 / Paused 0 / All 3'
).replace(
    '</table>', '</table>\n<p><a href="/campaigns?page=2">Load more</a></p>'
)
_CAMPAIGNS_PAGE_2 = f"""<!doctype html><html><head><title>Campaigns</title></head>
<body>
<h1>Campaigns (page 2)</h1>
<p>USD &middot; last 30 days</p>
<table id="campaigns" border="1">
  <tr><th>Campaign ID</th><th>Name</th><th>Spend</th><th>Revenue</th>
      <th>Orders</th><th>ROAS</th></tr>
  <tr><td>{CAMPAIGN_C}</td><td><a href="/campaign/{CAMPAIGN_C}">
      {CAMPAIGN_C_NAME}</a></td>
      <td>55.00</td><td>190.00</td><td>7</td><td>3.45</td></tr>
</table>
</body></html>
"""

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

    def __init__(self, *args, paged: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hits: list[str] = []
        self.times: list[float] = []
        self._lock = threading.Lock()
        self.paged = paged
        self.data = dict(CAMPAIGN_DATA)
        if paged:
            self.data[CAMPAIGN_C] = CAMPAIGN_C_DATA
        # Live state the write form changes: (campaign, keyword) -> bid,
        # or 'paused'. ``writes`` is every change applied, in order — the
        # ground truth an execution test compares the agent's log with.
        self.bids = {
            (cid, kw[0]): kw[2]
            for cid, (_n, kws, _t) in self.data.items()
            for kw in kws
        }
        self.writes: list[dict] = []

    def page(self, path: str) -> str | None:
        if path == '/campaigns':
            return _CAMPAIGNS_PAGED if self.paged else _CAMPAIGNS
        if path == '/campaigns?page=2':
            return _CAMPAIGNS_PAGE_2 if self.paged else None
        if path.startswith('/campaign/') and path[10:] in self.data:
            cid = path[10:]
            name, kws, terms = self.data[cid]
            with self._lock:
                live = [
                    (kw[0], kw[1], self.bids[(cid, kw[0])], *kw[3:])
                    for kw in kws
                ]
            return _campaign_page(cid, name, live, terms)
        return _PAGES.get(path)

    def apply(self, cid: str, keyword: str, action: str, bid: str) -> bool:
        key = (cid, keyword.strip())
        with self._lock:
            if key not in self.bids:
                return False
            value = 'paused' if action == 'pause' else bid.strip()
            self.writes.append({
                'campaign_id': cid,
                'keyword': key[1],
                'action': action,
                'from': self.bids[key],
                'to': value,
            })
            self.bids[key] = value
            return True

    @property
    def base(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}'

    def record(self, path: str) -> None:
        with self._lock:
            self.hits.append(path)
            self.times.append(time.time())

    def mark(self) -> int:
        """A position in the access log, for ``fetched_campaign(since=)``."""
        with self._lock:
            return len(self.hits)

    def mark_at(self, when: float) -> int:
        """The log position of the first request served at/after ``when``.

        Lets a test start its window at a moment it learns afterwards —
        e.g. when the agent declared its scope — rather than only at a
        point it marked live.
        """
        with self._lock:
            return next(
                (i for i, t in enumerate(self.times) if t >= when),
                len(self.times),
            )

    def fetched_campaign(self, cid: str, since: int = 0) -> bool:
        """Was this campaign's DETAIL page opened (after ``since``)?

        Only the detail page counts. The campaign list names both, so
        reading it is not a scope breach — going one click deeper is.
        ``since`` (from ``mark()``) scopes the question to one turn: a
        campaign excluded by a follow-up was fair game in the turn
        before it, when nothing had been excluded.
        """
        want = f'/campaign/{cid}'
        with self._lock:
            return any(h.rstrip('/') == want for h in self.hits[since:])

    def served(self) -> list[str]:
        with self._lock:
            return list(self.hits)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib interface)
        self.server.record(self.path)
        page = self.server.page(self.path.rstrip('/') or '/')
        if page is None:
            self.send_error(404)
            return
        self._html(page)

    def do_POST(self):  # noqa: N802 (stdlib interface)
        self.server.record(f'POST {self.path}')
        parts = self.path.strip('/').split('/')
        length = int(self.headers.get('Content-Length') or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        field = lambda k: (form.get(k) or [''])[0]  # noqa: E731
        ok = (
            len(parts) == 3
            and parts[0] == 'campaign'
            and parts[2] == 'keyword'
            and self.server.apply(
                parts[1], field('keyword'), field('action'), field('bid')
            )
        )
        if not ok:
            self.send_error(400, 'unknown campaign or keyword')
            return
        page = self.server.page(f'/campaign/{parts[1]}')
        self._html(page.replace('<h2>Keywords', '<p>Saved.</p>\n<h2>Keywords'))

    def _html(self, page: str) -> None:
        payload = page.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass  # keep CI output readable


def serve(paged: bool = False) -> ConsoleServer:
    """Start the console on a free port.

    ``paged`` adds a third campaign reachable only via "Load more".
    """
    server = ConsoleServer(('127.0.0.1', 0), _Handler, paged=paged)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
