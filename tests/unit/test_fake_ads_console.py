"""The e2e ad console must not answer a bid question off the LIST page.

``tests/e2e/test_ad_declaration_scoping.py`` proves scope held by reading
the console's access log: campaign X's detail page was opened, campaign
Y's never was. That instrument is wired up to something only if answering
the request *requires* opening a campaign. If the list page ever grows a
bid column, both tests keep passing while asserting nothing — an agent
could answer from the list, touch no campaign page, and the wide test
would fail for having browsed shallowly rather than for having clipped
the account down to a subset.

That is not hypothetical: it is how the wide test failed twice in CI. It
asked for "bid recommendations", the list already carried per-campaign
spend/revenue/orders/ROAS, and so opening a campaign was optional work
the agent skipped — while reading BOTH campaigns off the list, which is
the opposite of the narrowing this test guards against.

So: bids — the thing the request asks for — live on the detail pages and
nowhere else. That is a fixture invariant, not a preference. It is
checked here rather than in the e2e tier so a regression surfaces in
seconds without a live model, and checked over HTTP rather than against
the page constants so it covers what the handler actually serves.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tests.e2e.fake_ads_console import (
    CAMPAIGN_A,
    CAMPAIGN_B,
    CAMPAIGN_C,
    CAMPAIGN_DATA,
    serve,
)

pytestmark = pytest.mark.unit

# Reachable WITHOUT opening a campaign: everything the agent can read
# before the click that the e2e assertion is watching for.
PRE_CLICK_PATHS = ('/', '/campaigns')
DETAIL_PATHS = (f'/campaign/{CAMPAIGN_A}', f'/campaign/{CAMPAIGN_B}')


@pytest.fixture(scope='module')
def console():
    server = serve()
    yield server
    server.shutdown()
    server.server_close()


def _cells(console, path: str) -> list[str]:
    """Header + data cell text — where a bid column would show up."""
    with urllib.request.urlopen(f'{console.base}{path}', timeout=5) as r:
        assert r.status == 200, f'{path} returned {r.status}'
        html = r.read().decode()
    return [
        re.sub(r'<[^>]+>', '', c).strip()
        for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', html, re.S)
    ]


@pytest.mark.parametrize('path', PRE_CLICK_PATHS)
def test_no_bid_data_before_the_click(console, path):
    """A bid column on a pre-click page would void the e2e assertion."""
    offenders = [
        c for c in _cells(console, path) if re.search(r'\bbids?\b', c, re.I)
    ]
    assert not offenders, (
        f'{path} exposes bid data ({offenders}), so a keyword-bid request '
        f'no longer requires opening a campaign — the access-log assertion '
        f'in tests/e2e/test_ad_declaration_scoping.py stops meaning '
        f'anything. Keep bids on the detail pages only.'
    )


@pytest.mark.parametrize('path', DETAIL_PATHS)
def test_the_detail_page_is_the_only_source_of_bids(console, path):
    """...and it must carry them, or the request is unanswerable."""
    assert 'Bid' in _cells(console, path), (
        f'{path} carries no bid column, so the wide e2e request cannot be '
        f'answered from anywhere and the agent can only fabricate one'
    )


def test_both_campaigns_are_listed_but_not_detailed(console):
    """The list names both — reading it is not a scope breach.

    The e2e contract draws its line at the detail page precisely because
    the list names every campaign. If a campaign stopped appearing on the
    list, a scoped review could not find it in order to exclude it.
    """
    listed = _cells(console, '/campaigns')
    for cid in (CAMPAIGN_A, CAMPAIGN_B):
        assert cid in listed, f'{cid} vanished from the campaign list'


@pytest.mark.parametrize('cid', [CAMPAIGN_A, CAMPAIGN_B])
def test_figures_a_real_console_could_show(cid):
    """CPC never exceeds the bid, and search-term spend reconciles to the
    keyword spend — the facts the ad completeness check holds a report
    to. Data that breaks them makes every honest report unacceptable:
    the scoped e2e test once timed out arguing with a 2.00 CPC on a 1.00
    bid and a campaign with no search terms at all."""
    _name, keywords, terms = CAMPAIGN_DATA[cid]
    for kw, _m, bid, clicks, spend, *_rest in keywords:
        cpc = float(spend) / clicks
        assert cpc <= float(bid), f'{kw}: CPC {cpc:.2f} above bid {bid}'
    kw_spend = sum(float(k[4]) for k in keywords)
    term_spend = sum(float(t[3]) for t in terms)
    assert abs(kw_spend - term_spend) < 0.005, (cid, kw_spend, term_spend)


def test_campaign_spend_matches_the_list_page(console):
    listed = _cells(console, '/campaigns')
    for cid, (_name, keywords, _terms) in CAMPAIGN_DATA.items():
        spend = f'{sum(float(k[4]) for k in keywords):.2f}'
        row = listed[listed.index(cid) :][:3]
        assert spend in row, f'{cid}: keyword spend {spend} vs list {row}'


@pytest.mark.parametrize('path', ['/', '/campaigns', *DETAIL_PATHS])
def test_no_marketplace_branding(console, path):
    """Branding the stub as an Amazon console turned a "just tell me the
    numbers" lookup into a 15-25 minute skill-driven audit in CI. The
    request never asks for one; the fixture must not invite it."""
    with urllib.request.urlopen(f'{console.base}{path}', timeout=5) as r:
        html = r.read().decode()
    for brand in ('Amazon', 'Sponsored Products', 'noon'):
        assert brand not in html, f'{path} brands itself: {brand}'


def test_fetched_campaign_since_scopes_to_a_turn(console):
    """A campaign opened BEFORE a mark does not count after it -- the
    narrow e2e scopes its exclusion to the turn that made it."""
    with urllib.request.urlopen(
        f'{console.base}/campaign/{CAMPAIGN_B}', timeout=5
    ) as r:
        assert r.status == 200
    mark = console.mark()
    assert console.fetched_campaign(CAMPAIGN_B)
    assert not console.fetched_campaign(CAMPAIGN_B, since=mark)


def test_mark_at_starts_a_window_at_a_moment(console):
    """The narrow e2e starts its exclusion window at the agent's
    declaration time, learned only after the run: a page read before
    that moment is outside the window, one read after is inside."""
    with urllib.request.urlopen(
        f'{console.base}/campaign/{CAMPAIGN_B}', timeout=5
    ) as r:
        assert r.status == 200
    moment = time.time()
    since = console.mark_at(moment)
    assert not console.fetched_campaign(CAMPAIGN_B, since=since)
    with urllib.request.urlopen(
        f'{console.base}/campaign/{CAMPAIGN_B}', timeout=5
    ) as r:
        assert r.status == 200
    assert console.fetched_campaign(CAMPAIGN_B, since=console.mark_at(moment))


def _post(console, path: str, **form) -> int:
    data = urllib.parse.urlencode(form).encode()
    try:
        with urllib.request.urlopen(console.base + path, data=data) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.fixture
def fresh():
    server = serve()
    yield server
    server.shutdown()


def test_the_edit_form_changes_live_state_and_is_logged(fresh):
    console = fresh
    assert (
        _post(
            console,
            f'/campaign/{CAMPAIGN_A}/keyword',
            keyword='widget red',
            action='bid',
            bid='1.30',
        )
        == 200
    )
    assert console.bids[(CAMPAIGN_A, 'widget red')] == '1.30'
    assert console.writes == [
        {
            'campaign_id': CAMPAIGN_A,
            'keyword': 'widget red',
            'action': 'bid',
            'from': '1.50',
            'to': '1.30',
        }
    ]
    assert '1.30' in _cells(console, f'/campaign/{CAMPAIGN_A}')


def test_an_unknown_keyword_is_refused_and_not_logged(fresh):
    console = fresh
    assert (
        _post(
            console,
            f'/campaign/{CAMPAIGN_A}/keyword',
            keyword='nope',
            action='bid',
            bid='9',
        )
        == 400
    )
    assert console.writes == []


def test_the_third_campaign_only_appears_after_load_more():
    paged = serve(paged=True)
    try:
        first = (
            urllib.request.urlopen(paged.base + '/campaigns').read().decode()
        )
        assert CAMPAIGN_C not in first and 'Load more' in first
        second = (
            urllib.request.urlopen(paged.base + '/campaigns?page=2')
            .read()
            .decode()
        )
        assert CAMPAIGN_C in second
    finally:
        paged.shutdown()
