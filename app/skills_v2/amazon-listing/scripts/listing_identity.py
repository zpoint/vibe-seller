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


def _op_key(spec_row):
    return (spec_row.get('operation') or 'create').strip().lower()


def _pinned_asin(spec_row, spec, schema):
    """The ASIN a row pins, or '' -- only product_id_type=asin counts."""
    id_field = schema.field('product_id')
    type_field = schema.field('product_id_type')
    if not id_field:
        return ''
    fields = row_fields(spec_row, spec, schema)
    fields = {schema.resolve_field(k): v for k, v in fields.items()}
    pinned = str(fields.get(id_field) or '').strip()
    kind = str(fields.get(type_field) or '').strip().lower()
    return pinned if kind == 'asin' else ''


# An edit of an existing SKU must also name the ASIN it edits. Observed
# live: a copy-only `Edit (Partial Update)` that carried no product id
# (title, bullets, material, a product-type re-file) came back clean, and
# within the hour Amazon had re-matched every variant SKU of the family to
# NEW ASINs on one marketplace -- FNSKU and created date unchanged, the old
# ASINs gone from the storefront, its sibling marketplace untouched. With
# no product id on the row Amazon re-runs catalog matching on the new
# attributes; a pinned ASIN tells it which record the edit belongs to.
PIN_OPS = ('partialupdate', 'update')


def pin_guard(rows, spec, schema):
    """Refuse an edit / full-update row without a pinned ASIN."""
    missing = [
        str(r.get('sku') or f'row {i}')
        for i, r in enumerate(rows)
        if _op_key(r) in PIN_OPS and not _pinned_asin(r, spec, schema)
    ]
    if not missing:
        return None
    if not schema.field('product_id'):
        return (
            'error: this template has no product id column, so the edit '
            'rows cannot pin their ASIN ({skus}). Download the category '
            'template that carries one.'
        ).format(skus=', '.join(missing))
    return (
        'error: {n} edit row(s) carry no ASIN: {skus}. Without the ASIN '
        'Amazon re-runs catalog matching on the new attributes and can '
        'move the SKU to a DIFFERENT ASIN (observed live on a copy-only '
        "partial update). Read each SKU's current ASIN (Listings API, "
        'Manage Inventory or the All-Listings report, on THIS marketplace) '
        'and put it on the row: "asin": "B0EXAMPLE1". A parent row pins '
        'the parent ASIN.'
    ).format(n=len(missing), skus=', '.join(missing))


def mint_guard(rows, spec, schema):
    """Refuse an UNDECLARED new-ASIN mint -> fatal string or None.

    Fires per `create` row that carries no product id and no
    ``mint_new_asin`` declaration (row-level, else spec-level).
    """
    id_field = schema.field('product_id')
    type_field = schema.field('product_id_type')
    creates, undeclared, any_pinned = [], [], False
    for i, spec_row in enumerate(rows):
        if _op_key(spec_row) != 'create':
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
        '(or row-level external_product_id + external_product_id_type: '
        'asin).\n'
        '  * GENUINELY NEW to this account (no ASIN anywhere) -> declare '
        'it: "{key}": true on the spec (covers every row) or on the row.\n'
        '  * NEW, but must MATCH another marketplace -> mint on ONE '
        'marketplace first, wait for its report and read what it minted, '
        'then pin THIS marketplace to those ASINs. Never mint on both.\n'
        'Only external_product_id_type=asin counts as a pin. A UPC / '
        'EAN / GTIN identifies the product, not an existing listing, so '
        'it still mints; and "GTIN Exempt" says only that there is no '
        'barcode, not that there is no ASIN.'
    ).format(n=len(undeclared), skus=', '.join(undeclared), key=MINT_KEY)


def ops_guard(rows, allowed):
    """Refuse a disabled operation, or one outside ``--ops``.

    ``allowed`` narrows the run to the operations the task asked for:
    an "edit the copy" job passes ``--ops partialupdate`` so no row of
    its spec can create a SKU or mint an ASIN by mistake.
    """
    allowed = {a.strip().lower() for a in allowed or ()}
    for i, r in enumerate(rows):
        _, key = resolve_operation(r.get('operation'), 'legacy', allowed)
        if allowed and key not in allowed:
            raise SystemExit(
                f"error: row {i} sku={r.get('sku')} has operation '{key}', "
                f'but this run allows only {sorted(allowed)} (--ops). Do '
                'not widen --ops to get past this: the task said which '
                'operations it may perform.'
            )


# Words of the relationship itself, never a SKU. Observed live: a spec
# copied from a Listings API dump put `child_relationship_type: variation`
# into the parent-SKU cell of every child -- the family would not rejoin.
_RELATIONSHIP_WORDS = {'variation', 'parent', 'child'}


def family_guard(rows, spec, schema):
    """Refuse a parent row naming a parent SKU, or a child naming a non-SKU.

    Where the spec carries parent rows, every child must name one of them.
    """
    seen = []
    for i, r in enumerate(rows):
        fields = row_fields(r, spec, schema)
        fields = {schema.resolve_field(k): v for k, v in fields.items()}
        level = str(fields.get(schema.field('parentage')) or '').strip()
        parent = str(fields.get(schema.field('parent_sku')) or '').strip()
        seen.append((str(r.get('sku') or f'row {i}'), level.lower(), parent))
    parents = {sku for sku, level, _ in seen if level == 'parent'}
    bad = []
    for sku, level, parent in seen:
        if level == 'parent' and parent:
            bad.append(f'{sku} is a parent but names parent SKU {parent!r}')
        elif (
            level == 'child'
            and parent
            and (
                parent.lower() in _RELATIONSHIP_WORDS
                or (parents and parent not in parents)
            )
        ):
            bad.append(
                f'{sku} names parent SKU {parent!r}, not one of the '
                f'parent rows {sorted(parents) or "(none in this spec)"}'
            )
    if not bad:
        return None
    return (
        'error: the parent-SKU column is wrong: ' + '; '.join(bad) + '. '
        "A parent row leaves it blank; a child names its parent row's "
        'SKU. "variation" is the relationship type, not a SKU.'
    )


def nested_guard(rows):
    """Refuse a field value that is an object: one cell holds one value."""
    bad = [
        f'{r.get("sku") or f"row {i}"}: {name}'
        for i, r in enumerate(rows)
        for name, value in (r.get('fields') or {}).items()
        if isinstance(value, dict)
    ]
    if not bad:
        return None
    return (
        'error: these fields carry an object, and a flat-file cell holds '
        'one value: ' + ', '.join(bad) + '. Split each into its own '
        'columns (e.g. item_package_dimensions -> ...#1.length.value, '
        '...#1.length.unit, ...); `inspect TEMPLATE --field <name>` '
        'lists them.'
    )


def row_guards(rows, spec, schema):
    """Every per-row shape check `fill` runs before writing a cell."""
    return (
        nested_guard(rows)
        or pin_guard(rows, spec, schema)
        or family_guard(rows, spec, schema)
    )
