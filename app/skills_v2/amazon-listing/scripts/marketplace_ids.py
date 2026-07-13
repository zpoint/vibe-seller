"""Amazon marketplace (country) -> marketplace id.

A marketplace id identifies a *marketplace* (a country storefront), NOT a
seller -- it is a public constant of the platform, identical for every
seller on that marketplace, and published in Amazon's SP-API "Marketplace
IDs" reference. Kept as a standalone data module so the full global set
lives in one place and `listing_bulk.py` stays within the line cap.

Verified 2026-07 against the Amazon SP-API docs and two independent
mirrors. `listing_bulk` also auto-detects the id straight from a
template's `purchasable_offer[marketplace_id=<id>]` columns, so a
marketplace Amazon adds *after* this table still works without an edit.
"""

import re

MARKETPLACE_IDS = {
    # North America
    'US': 'ATVPDKIKX0DER',  # amazon.com
    'CA': 'A2EUQ1WTGCTBG2',  # amazon.ca
    'MX': 'A1AM78C64UM0Y8',  # amazon.com.mx
    'BR': 'A2Q3Y263D00KWC',  # amazon.com.br
    # Europe
    'UK': 'A1F83G8C2ARO7P',  # amazon.co.uk
    'DE': 'A1PA6795UKMFR9',  # amazon.de
    'FR': 'A13V1IB3VIYZZH',  # amazon.fr
    'IT': 'APJ6JRA9NG5V4',  # amazon.it
    'ES': 'A1RKKUPIHCS9HS',  # amazon.es
    'NL': 'A1805IZSGTT6HS',  # amazon.nl
    'SE': 'A2NODRKZP88ZB9',  # amazon.se
    'PL': 'A1C3SOZRARQ6R3',  # amazon.pl
    'BE': 'AMEN7PMS3EDWL',  # amazon.com.be
    'TR': 'A33AVAJ2PDY3EV',  # amazon.com.tr
    'IE': 'A28R8C7NBKEWEA',  # amazon.ie
    # Middle East / Africa
    'AE': 'A2VIGQ35RCS4UG',  # amazon.ae
    'SA': 'A17E79C6D8DWNP',  # amazon.sa
    'EG': 'ARBP9OOSHTCHU',  # amazon.eg
    # Asia-Pacific
    'IN': 'A21TJRUUN4KGV',  # amazon.in
    'JP': 'A1VC38T7YXB528',  # amazon.co.jp
    'AU': 'A39IBJ37TRP1C6',  # amazon.com.au
    'SG': 'A19VAU5U5O7RUS',  # amazon.sg
}

# id -> country code, for reverse lookups / labelling.
COUNTRY_BY_ID = {v: k for k, v in MARKETPLACE_IDS.items()}

_MKT_ID_IN_COLUMN = re.compile(r'marketplace_id=([A-Za-z0-9]+)')
_FULFILL_GROUP = re.compile(r'fulfillment_availability#(\d+)\.')


def fulfillment_index(cols, mkt_id):
    """Which `fulfillment_availability#N` group belongs to a marketplace.

    Fulfillment columns are NOT marketplace-bracketed like
    `purchasable_offer[marketplace_id=...]`; each `fulfillment_availability#N`
    group instead sits just before that marketplace's offer block. So stock
    for marketplace X must use the N whose columns most-closely precede X's
    offer block -- else quantity lands on the wrong marketplace (an SA offer
    with AE stock never goes live). `cols` maps field name -> list of 1-based
    column indices. Returns N (int) or None.
    """
    offer = [
        c
        for k, v in cols.items()
        if f'marketplace_id={mkt_id}]' in k
        for c in v
    ]
    if not offer:
        return None
    target = min(offer)
    best = None  # (min_col, N) of the nearest group preceding the offer
    for k, v in cols.items():
        m = _FULFILL_GROUP.match(str(k))
        if m:
            col = min(v)
            if col < target and (best is None or col > best[0]):
                best = (col, int(m.group(1)))
    return best[1] if best else None


def ids_in_template(cols):
    """Marketplace ids named in a template's offer columns, in order.

    `purchasable_offer[marketplace_id=<id>]...` columns name exactly the
    marketplaces the template can list on -- ground truth independent of
    the table above, so a marketplace Amazon adds later still resolves.
    """
    ids = []
    for col in cols:
        m = _MKT_ID_IN_COLUMN.search(str(col))
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def resolve(marketplace, template_ids=None):
    """Country code ('SA') / raw id / template auto-detect -> marketplace id.

    Order, most explicit first: the country->id table, a raw id, then the
    template's own offer columns when they name exactly one marketplace
    (so it stays general for new marketplaces and for a caller who omits
    `marketplace`). Returns None when nothing is supplied and the template
    is multi-marketplace. Raises SystemExit on an unknown country code a
    single-marketplace template can't disambiguate.
    """
    template_ids = list(template_ids or [])
    single = template_ids[0] if len(set(template_ids)) == 1 else None
    if not marketplace:
        return single
    m = str(marketplace).strip()
    if m.upper() in MARKETPLACE_IDS:
        return MARKETPLACE_IDS[m.upper()]
    if m in COUNTRY_BY_ID or m in template_ids:  # already a raw id
        return m
    if single:  # unknown code, template offers exactly one marketplace
        return single
    codes = ', '.join(sorted(MARKETPLACE_IDS))
    raise SystemExit(
        f'error: unknown marketplace {marketplace!r} -- use a country code '
        f'({codes}) or a raw marketplace id'
    )
