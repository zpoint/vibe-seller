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
    undeclared = []
    for i, spec_row in enumerate(rows):
        _, op_key = resolve_operation(spec_row.get('operation'), schema.dialect)
        if op_key != 'create':
            continue
        declared = spec_row.get(MINT_KEY, spec.get(MINT_KEY))
        if declared:
            continue
        fields = row_fields(spec_row, spec, schema)
        fields = {schema.resolve_field(k): v for k, v in fields.items()}
        pinned = str(fields.get(id_field) or '').strip() if id_field else ''
        if not pinned:
            undeclared.append(str(spec_row.get('sku') or f'row {i}'))
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
        'product_id_type "GTIN Exempt" is NOT a declaration -- it says '
        'the product has no barcode, not that it has no ASIN.'
    ).format(n=len(undeclared), skus=', '.join(undeclared), key=MINT_KEY)
