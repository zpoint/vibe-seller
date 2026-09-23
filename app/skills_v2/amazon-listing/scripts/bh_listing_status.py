# ruff: noqa: F821 — browser-harness globals (new_tab, js, wait_for_load)
"""Read what Manage Inventory says each SKU IS, on ONE marketplace.

The verification step of every listing task — "is this SKU live on the
target storefront, and on which ASIN?" — as one deterministic call, so
the agent never hand-scrapes it. Run through the STORE WRAPPER:

  SKUS=SKU-A,SKU-B,SKU-C SC_HOST=sellercentral.amazon.ae \
    browser-use < .claude/skills/amazon-listing/scripts/bh_listing_status.py

Prints exactly one ``RESULT {json}`` line:
  ok / marketplace / intended_marketplace / rows / missing / reason
where ``rows`` is ``{sku: {asin, asins, offer_cell, text}}``.

Why a helper: the page the skill used to name, ``skucentral?mSku=``,
renders an empty body in the NGS console — one run spent 19 calls on it
— and the fallback was scraping ``innerText`` by hand in whatever
language the session speaks. What is read here is STRUCTURE:

* a row is the page's own ``div[data-sku="<sku>"]`` container, so a
  SKU is matched exactly, never by searching page text;
* its ASIN is the ASIN-shaped text inside that row (a parent row
  carries the family's parent ASIN);
* ``offer_cell`` is whether the row has the featured-offer price cell —
  child/offer rows do, a variation-parent summary does not.

And the marketplace is PROVEN, not assumed: a ``.ae`` URL renders the
SA inventory while the session's switcher is still on SA, so the page's
``ue_mid`` must equal the marketplace you meant (``SC_HOST``'s domain,
or ``SC_MARKETPLACE=<id|CC>``) before a single row is reported. An
unreadable ``ue_mid`` is a refusal too — "probably the right one" is
how a family got verified on the wrong storefront.

A SKU in ``missing`` was not in Manage Inventory on that marketplace
once the page had rendered. Read-only: it never clicks or edits.
"""

import json
import os
import time
from urllib.parse import quote

HOST = os.environ['SC_HOST'].strip().rstrip('/')
SKUS = [s.strip() for s in os.environ['SKUS'].split(',') if s.strip()]
_WAIT = int(os.environ.get('INV_WAIT', '20'))

# Domain / country -> marketplace id. Public platform constants, inline
# because this script is fed to browser-use on stdin and cannot import
# its siblings (same table as bh_upload_flatfile).
_MARKETPLACES = {
    'com': 'ATVPDKIKX0DER',
    'ca': 'A2EUQ1WTGCTBG2',
    'com.mx': 'A1AM78C64UM0Y8',
    'co.uk': 'A1F83G8C2ARO7P',
    'de': 'A1PA6795UKMFR9',
    'fr': 'A13V1IB3VIYZZH',
    'it': 'APJ6JRA9NG5V4',
    'es': 'A1RKKUPIHCS9HS',
    'ae': 'A2VIGQ35RCS4UG',
    'sa': 'A17E79C6D8DWNP',
    'eg': 'ARBP9OOSHTCHU',
    'in': 'A21TJRUUN4KGV',
    'co.jp': 'A1VC38T7YXB528',
    'com.au': 'A39IBJ37TRP1C6',
    'sg': 'A19VAU5U5O7RUS',
}
_COUNTRY_TLD = {
    'US': 'com',
    'CA': 'ca',
    'MX': 'com.mx',
    'UK': 'co.uk',
    'GB': 'co.uk',
    'DE': 'de',
    'FR': 'fr',
    'IT': 'it',
    'ES': 'es',
    'AE': 'ae',
    'SA': 'sa',
    'EG': 'eg',
    'IN': 'in',
    'JP': 'co.jp',
    'AU': 'com.au',
    'SG': 'sg',
}

out = {'ok': False, 'rows': {}, 'missing': [], 'sc_host': HOST}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


def _intended():
    want = (os.environ.get('SC_MARKETPLACE') or '').strip()
    if want:
        tld = _COUNTRY_TLD.get(want.upper())
        return _MARKETPLACES[tld] if tld else want.upper()
    tail = HOST.split('amazon.', 1)[-1] if 'amazon.' in HOST else ''
    return _MARKETPLACES.get(tail.lower())


# Every [data-sku] row on the page: exact SKU, the ASIN-shaped texts in
# it, whether it has the featured-offer price cell, and a text excerpt
# for a human (reported, never matched).
_ROWS_JS = r"""
  var out = {};
  document.querySelectorAll('[data-sku]').forEach(function (r) {
    var sku = r.getAttribute('data-sku');
    if (!sku || out[sku]) return;
    var asins = [];
    r.querySelectorAll('*').forEach(function (e) {
      // An ASIN is an Amazon identifier, identical in every console
      // language -- so it is read into `apiToken`, not matched as copy.
      var apiToken = (e.textContent || '').trim();
      if (e.children.length === 0 && /^B0[A-Z0-9]{8}$/.test(apiToken)
          && asins.indexOf(apiToken) < 0) asins.push(apiToken);
    });
    out[sku] = {
      asin: asins[0] || null,
      asins: asins,
      offer_cell: !!r.querySelector('[data-test-id="FeaturedOfferPrice"]'),
      text: (r.innerText || '').replace(/\s+/g, ' ').slice(0, 240)
    };
  });
  return JSON.stringify(out);
"""


def _search(term):
    """Load one inventory search and return its rows once they settle."""
    new_tab(
        f'https://{HOST}/myinventory/inventory?fulfilledBy=all&page=1'
        f'&pageSize=100&searchField=all&searchTerm={quote(term)}'
        '&sort=date_created_desc&status=all'
    )
    wait_for_load()
    rows, last, stable = {}, None, 0
    deadline = time.time() + _WAIT
    while time.time() < deadline:
        rows = json.loads(js(_ROWS_JS) or '{}')
        key = sorted(rows)
        stable = stable + 1 if key == last and rows else 0
        if stable >= 2:  # same rows on consecutive polls -> rendered
            break
        last = key
        time.sleep(2)
    return rows


def _common_prefix(items):
    if not items:
        return ''
    p = os.path.commonprefix(items)
    return p if len(p) >= 6 else ''


if not SKUS:
    _finish('SKUS is empty -- pass the exact SKUs, comma-separated')
out['intended_marketplace'] = want = _intended()
if not want:
    _finish(
        f'cannot tell which marketplace {HOST!r} means -- pass '
        'SC_MARKETPLACE=<id|CC>'
    )


def _proven_search(term):
    """One search, and the proof its page is the marketplace meant.

    Checked after EVERY page load, not once: each search is a fresh page,
    and a row is only as trustworthy as the ue_mid it was read under.
    """
    rows = _search(term)
    out['marketplace'] = live = js('return window.ue_mid || null')
    if not live:
        _finish(
            "cannot read this page's marketplace (window.ue_mid) -- "
            'refusing to report rows whose storefront is unproven'
        )
    if live != want:
        _finish(
            f'MARKETPLACE MISMATCH -- this session is on {live}, you asked '
            f'for {want}. The URL host does not decide which inventory '
            'renders; switch the account to the target marketplace first'
        )
    return rows


prefix = _common_prefix(SKUS)
found = {}
for term in [prefix] if prefix else SKUS:
    found.update(_proven_search(term))
# A variation parent is not always returned by a prefix search of its
# children; look each still-missing SKU up by its exact name, bounded.
if prefix:
    for sku in [s for s in SKUS if s not in found][:6]:
        found.update(_proven_search(sku))

out['rows'] = {s: found[s] for s in SKUS if s in found}
out['missing'] = [s for s in SKUS if s not in found]
out['ok'] = True
_finish()
