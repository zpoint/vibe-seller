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
import urllib.request

import pytest

from tests.e2e.fake_ads_console import CAMPAIGN_A, CAMPAIGN_B, serve

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
