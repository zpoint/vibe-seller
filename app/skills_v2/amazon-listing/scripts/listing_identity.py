"""Identity guard: never mint an ASIN without saying so.

Standalone (like `marketplace_ids` / `listing_schema`) so
`listing_bulk.py` stays within the repo line cap. Pure spec
inspection -- no openpyxl, no I/O.
"""

from listing_schema import resolve_operation, row_fields  # noqa: E402

# A `create` row with no product id MINTS a brand-new ASIN. On a unified
# pan-regional account (MENA: SA / AE / EG) a seller SKU is ACCOUNT-scoped,
# so minting for a SKU that already sells on ANY of its marketplaces
# re-points that SKU at the new ASIN account-wide and ORPHANS the old one:
# its reviews, ratings and rank go with it, and Amazon never re-issues a
# retired ASIN. Observed live -- an AE relist submitted plain `create` rows
# for the SKUs already live on SA, and all three SA children silently
# changed ASIN (their FNSKUs, keyed to the SKU, did not).
#
# The template cannot know whether a SKU already exists, so the INTENT is
# declared instead of guessed: pin the existing ASIN, or say `mint_new_asin`.
MINT_KEY = 'mint_new_asin'


def mint_guard(rows, spec, schema):
    """Refuse an UNDECLARED new-ASIN mint -> fatal string or None.

    Fires per `create` row that carries no product id and no
    ``mint_new_asin`` declaration (row-level, else spec-level).
    """
    id_field = schema.field('product_id')
    type_field = schema.field('product_id_type')
    creates, undeclared, any_pinned = [], [], False
    for i, spec_row in enumerate(rows):
        _, op_key = resolve_operation(spec_row.get('operation'), schema.dialect)
        if op_key != 'create':
            continue
        fields = row_fields(spec_row, spec, schema)
        fields = {schema.resolve_field(k): v for k, v in fields.items()}
        pinned = str(fields.get(id_field) or '').strip() if id_field else ''
        # Only an ASIN *matches* an existing catalog record. A UPC / EAN /
        # GTIN in the same column is an identifier for a product Amazon may
        # never have seen, so the row still mints -- and "GTIN Exempt" says
        # only that there is no barcode. Treat every non-ASIN id as unpinned.
        kind = str(fields.get(type_field) or '').strip().lower()
        if pinned and kind != 'asin':
            pinned = ''
        any_pinned = any_pinned or bool(pinned)
        creates.append((
            str(spec_row.get('sku') or f'row {i}'),
            pinned,
            spec_row.get(MINT_KEY),
        ))
    # A spec-level declaration blankets EVERY row, so in a spec that also
    # carries pinned relists it is the cheapest way to wave a dropped pin
    # through -- the exact move this guard exists to catch. Where the two
    # kinds are mixed, each mint has to be owned on its own row. A spec
    # with no pins at all is an unambiguous new family; blanket is fine.
    blanket = spec.get(MINT_KEY)
    if blanket and any_pinned:
        mixed = [
            sku
            for sku, pinned, row_dec in creates
            if not pinned and not row_dec
        ]
        if mixed:
            return (
                'error: this spec declares "{key}": true at the TOP LEVEL '
                'while {n} other create row(s) pin an ASIN. A top-level '
                'declaration covers every row, so it would also wave '
                'through a pin you meant to set and lost -- which is the '
                'mistake this guard exists to catch. Put "{key}": true on '
                'the row(s) that really mint ({skus}) and remove it from '
                'the top level.'
            ).format(
                key=MINT_KEY,
                n=sum(1 for _, pinned, _ in creates if pinned),
                skus=', '.join(mixed),
            )
        blanket = None
    for sku, pinned, row_dec in creates:
        if pinned or row_dec or blanket:
            continue
        undeclared.append(sku)
    if not undeclared:
        return None
    return (
        'error: {n} create row(s) carry no ASIN, so Amazon will MINT a '
        'new one for each: {skus}. A seller SKU is ACCOUNT-scoped -- if '
        'any of these already sells on another marketplace of this '
        'account, the mint RE-POINTS the SKU to the new ASIN account-wide '
        'and ORPHANS the old one; its reviews, ratings and rank go with '
        'it and Amazon never re-issues that ASIN. Say which this is:\n'
        '  * RELISTING an existing product (other marketplace, same SKU) '
        "-> read each SKU's current ASIN off Manage Inventory or the "
        'All-Listings report and pin it on the row: "asin": "B0EXAMPLE1" '
        '(or external_product_id + external_product_id_type: asin).\n'
        '  * GENUINELY NEW to this account (no ASIN anywhere) -> declare '
        'it: "{key}": true on the spec (covers every row) or on the row.\n'
        'Only external_product_id_type=asin counts as a pin. A UPC / '
        'EAN / GTIN identifies the product, not an existing listing, so '
        'it still mints; and "GTIN Exempt" says only that there is no '
        'barcode, not that there is no ASIN.'
    ).format(n=len(undeclared), skus=', '.join(undeclared), key=MINT_KEY)
