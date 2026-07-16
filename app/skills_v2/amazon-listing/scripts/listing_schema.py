"""Flat-file template structure: dialect detection, role resolution,
header/column parsing, and metadata (required fields, valid values).

Kept as a standalone module (like `marketplace_ids`) so `listing_bulk.py`
stays within the repo line cap and the "how is this template shaped"
logic lives in one place. Everything here is pure openpyxl-object /
string manipulation — no CLI, no I/O.

Amazon ships two flat-file dialects and this module hides the difference
behind logical roles (see `ROLE_MATCHERS`), so one JSON spec drives
either:

  legacy  (TemplateType=fptcustom) -- bare field API names: `item_sku`,
          `update_delete`, `parent_child`, `variation_theme`, ...
  unified (NGS "Beta Product Spreadsheet") -- decorated names:
          `contribution_sku#1.value`, `::record_action`,
          `parentage_level[marketplace_id=<id>]#1.value`, ...

`listing_bulk.py` imports these (aliased to `_`-prefixed internal names)
and re-exports the ones tests reach for.
"""

import re

# The Template sheet holds the flat file. Metadata lives in siblings.
TEMPLATE_SHEET = 'Template'
DEFN_SHEET = 'Data Definitions'
VALID_SHEET = 'Valid Values'
DROPDOWN_SHEET = 'Dropdown Lists'

# The operation ("record action") column and its tokens PER DIALECT. A
# blank cell means Create in both -- that is the default and cannot be a
# literal token, so callers pass operation='create' and we clear the cell.
OP_TOKENS = {
    'legacy': {
        'create': '',
        'update': 'Update',
        'partialupdate': 'partialupdate',
        'delete': 'delete',
    },
    'unified': {
        'create': '',
        'update': 'Create or Replace (Full Update)',
        'partialupdate': 'Edit (Partial Update)',
        'delete': 'Delete',
    },
}

# Logical field roles, each matched STRUCTURALLY against a header column's
# API name so one spec drives either dialect. `n` = full API name, `b` =
# base attribute (marketplace/language brackets, the `#N.sub` suffix and
# the unified `::` prefix stripped), `lf` = leaf (text after the last `.`).
# The FIRST header column that matches a role wins (columns are visited in
# sheet order). Anything not covered here is addressed by exact API name.
ROLE_MATCHERS = {
    'sku': lambda n, b, lf: n == 'item_sku' or b == 'contribution_sku',
    'operation': lambda n, b, lf: (
        n == 'update_delete' or b == 'record_action'
    ),
    'product_type': lambda n, b, lf: (
        n == 'feed_product_type' or b == 'product_type'
    ),
    'brand': lambda n, b, lf: (
        n == 'brand_name' or (b == 'brand' and lf == 'value')
    ),
    'parentage': lambda n, b, lf: (
        n == 'parent_child' or b == 'parentage_level'
    ),
    'parent_sku': lambda n, b, lf: n == 'parent_sku'
    or (b == 'child_parent_sku_relationship' and lf == 'parent_sku'),
    'variation_theme': lambda n, b, lf: n == 'variation_theme'
    or (b == 'variation_theme' and lf == 'name'),
    'product_id': lambda n, b, lf: (
        n == 'external_product_id' or n == 'amzn1.volt.ca.product_id_value'
    ),
    'product_id_type': lambda n, b, lf: (
        n == 'external_product_id_type' or n == 'amzn1.volt.ca.product_id_type'
    ),
}

# Cross-dialect attribute renames: a bare spec key on the left resolves to
# a column whose base attribute is on the right when the two dialects named
# the same concept differently (legacy `color_name` -> unified `color`).
FIELD_ALIASES = {
    'color_name': 'color',
    'size_name': 'size',
}


def base_attr(name):
    """The bare attribute token of a flat-file field API name.

    Strips the dialect decorations so both dialects reduce to a common
    key: the unified `::` action prefix, the marketplace/language brackets,
    and the `#N.subfield` suffix. Examples:
      `item_sku`                          -> `item_sku`
      `contribution_sku#1.value`          -> `contribution_sku`
      `::record_action`                   -> `record_action`
      `parentage_level[marketplace_id=X]#1.value` -> `parentage_level`
    """
    s = str(name).lstrip(':')
    for ch in ('[', '#'):
        i = s.find(ch)
        if i != -1:
            s = s[:i]
    return s


def leaf(name):
    """Text after the last `.` of a field API name (the sub-field), e.g.
    `child_parent_sku_relationship[...]#1.parent_sku` -> `parent_sku`,
    `variation_theme#1.name` -> `name`. Empty when there is no `.`."""
    s = str(name)
    return s.rsplit('.', 1)[-1] if '.' in s else ''


def is_sku_field(name):
    """The identity (SKU) column, in either dialect: legacy `item_sku` or
    unified `contribution_sku#1.value`."""
    return name == 'item_sku' or base_attr(name) == 'contribution_sku'


def is_op_field(name):
    """The operation column, in either dialect: legacy `update_delete` or
    unified `::record_action`."""
    return name == 'update_delete' or base_attr(name) == 'record_action'


class Schema:
    """Resolves logical field roles to the actual header columns present,
    so parent/child/offer fields are addressed uniformly across the legacy
    (fptcustom) and unified (NGS Beta) template dialects.

    `cols` is the {field API name -> [1-based column indices]} map from
    `field_columns`. `roles` maps a logical role (see `ROLE_MATCHERS`) to
    the first header column that matched it. `dialect` is 'unified' when the
    operation column is `::record_action`, else 'legacy'.
    """

    def __init__(self, cols):
        self.cols = cols
        self.roles = {}
        for name in cols:
            b, lf = base_attr(name), leaf(name)
            for role, match in ROLE_MATCHERS.items():
                if role not in self.roles and match(name, b, lf):
                    self.roles[role] = name
        op = self.roles.get('operation', '')
        self.dialect = (
            'unified' if base_attr(op) == 'record_action' else 'legacy'
        )

    def field(self, role):
        """The header field API name bound to a role, or None."""
        return self.roles.get(role)

    def op_tokens(self):
        return OP_TOKENS[self.dialect]

    def resolve_field(self, name):
        """Map a spec `fields` key to the actual header column.

        A spec written with a bare/undecorated attribute name (e.g.
        `item_name`, `product_description`, `recommended_browse_nodes`)
        must reach the unified template's decorated column
        (`item_name[marketplace_id=X][language_tag=Y]#1.value`). Resolution
        order: an EXACT column always wins (so legacy specs and explicit
        decorated names are untouched); else the first column with the same
        base attribute, preferring a scalar `.value` leaf; else a known
        cross-dialect rename (`color_name`->`color`); else the name
        unchanged (the caller then warns it is not in this template).
        """
        if name in self.cols:
            return name
        base = base_attr(name)
        alias = FIELD_ALIASES.get(base)
        for target in (base, alias):
            if not target:
                continue
            cands = [c for c in self.cols if base_attr(c) == target]
            if cands:
                val = [c for c in cands if leaf(c) == 'value']
                return (val or cands)[0]
        return name


def our_price_col(cols, mkt_id):
    """The `our_price ... value_with_tax` offer column for a marketplace.

    Found structurally so it matches BOTH the legacy
    `purchasable_offer[marketplace_id=X]#1.our_price#1.schedule#1.value_with_tax`
    and the unified `...[marketplace_id=X][audience=ALL]#1.our_price...`
    column -- the `[audience=ALL]` insert is why a fixed template string
    can't be used. Returns the column name or None.
    """
    if not mkt_id:
        return None
    for name in cols:
        if (
            f'marketplace_id={mkt_id}]' in name
            and '.our_price' in name
            and name.endswith('value_with_tax')
        ):
            return name
    return None


def find_header_row(ws, max_scan=8):
    """Return the 1-based row index whose cells carry field API names.

    Identified structurally by the presence of the SKU column (`item_sku`
    legacy or `contribution_sku#1.value` unified), so it works regardless of
    console language (the label row above it is localised; this row is not)
    AND regardless of template dialect / header depth (legacy row 3, unified
    row 5).
    """
    for r in range(1, max_scan + 1):
        if any(is_sku_field(str(c.value)) for c in ws[r] if c.value):
            return r
    raise ValueError(
        'could not find the field-name row (no SKU column -- item_sku or '
        f'contribution_sku -- in the first {max_scan} rows) -- is this a '
        'listing flat-file template?'
    )


def data_start_row(ws, header_row):
    """The 1-based row where DATA must begin.

    Legacy: immediately below the field-name row (`header_row + 1`).
    Unified: the row-1 `settings=…&dataRow=N&…` blob names N, and Amazon
    treats the rows between the field-name row and N as a prefilled EXAMPLE
    region that it SKIPS on upload. Writing data into that gap silently
    drops those SKUs (verified live: children in rows 6-7 were skipped, so a
    6-child feed processed as 4/6). Honour `dataRow` so every row is read.
    """
    settings = ws.cell(row=1, column=1).value
    if settings:
        m = re.search(r'dataRow=(\d+)', str(settings))
        if m:
            n = int(m.group(1))
            if n > header_row:
                return n
    return header_row + 1


def field_columns(ws, header_row):
    """Map field API name -> list of 1-based column indices.

    Repeated fields (bullet_point1..N are distinct, but some templates
    reuse a bare name across marketplace-scoped offer blocks) map to
    multiple columns; the first is used for scalar writes.
    """
    cols = {}
    for cell in ws[header_row]:
        name = cell.value
        if name:
            cols.setdefault(str(name), []).append(cell.column)
    return cols


def load_required_fields(wb):
    """Field API names marked Required in the Data Definitions sheet.

    Header at Excel row 2, then per-field rows. The `Field Name` and
    `Required?` COLUMN POSITIONS differ by dialect (legacy has an extra
    `Definition and Use` column, so `Required?` is col 6; unified drops it,
    so it's col 5), so locate both by header name rather than a fixed
    index. Only an exact `Required` counts -- `Conditionally Required` and
    `Optional` are not hard requirements.
    """
    if DEFN_SHEET not in wb.sheetnames:
        return set()
    ws = wb[DEFN_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 3:
        return set()
    hdr = [str(c).strip().lower() if c is not None else '' for c in rows[1]]

    def col(*needles):
        for i, h in enumerate(hdr):
            if any(n in h for n in needles):
                return i
        return None

    fn_i, req_i = col('field name'), col('required')
    if fn_i is None or req_i is None:
        return set()
    required = set()
    for row in rows[2:]:
        if not row or len(row) <= max(fn_i, req_i):
            continue
        field_name, req = row[fn_i], row[req_i]
        if field_name and req and str(req).strip().lower() == 'required':
            required.add(str(field_name).strip())
    return required


def load_valid_values(wb):
    """Map field API name -> set of accepted enum tokens (lowercased).

    Read from 'Dropdown Lists', whose field-name header row (found by
    probing for the operation column of either dialect) holds field API
    names as column headers and the rows below hold the tokens for each.
    Returns {} silently if the sheet is absent or shaped unexpectedly --
    enum checks then degrade to warnings only.
    """
    if DROPDOWN_SHEET not in wb.sheetnames:
        return {}
    ws = wb[DROPDOWN_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 4:
        return {}
    # The field-name header inside Dropdown Lists is the row that
    # contains a known enum field; probe the first few rows for it.
    hdr_idx = None
    for i in range(min(5, len(rows))):
        if rows[i] and any(is_op_field(str(c)) for c in rows[i] if c):
            hdr_idx = i
            break
    if hdr_idx is None:
        return {}
    hdr = rows[hdr_idx]
    out = {}
    for ci, name in enumerate(hdr):
        if not name:
            continue
        vals = set()
        for ri in range(hdr_idx + 1, len(rows)):
            v = rows[ri][ci] if ci < len(rows[ri]) else None
            if v not in (None, ''):
                vals.add(str(v).strip().lower())
        if vals:
            out[str(name).strip()] = vals
    return out


def valid_value_case(wb):
    """Map field API name -> {lowercased token: template's exact-case token}.

    Amazon rejects a case-mismatched enum on some fields (verified live:
    `apparel_size_system` accepts `UAE/KSA` but rejects `uae/ksa`), so a
    spec value must be written in the template's own case. This mirrors
    `load_valid_values` but keeps the original casing as the value.
    """
    if DROPDOWN_SHEET not in wb.sheetnames:
        return {}
    ws = wb[DROPDOWN_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 4:
        return {}
    hdr_idx = None
    for i in range(min(5, len(rows))):
        if rows[i] and any(is_op_field(str(c)) for c in rows[i] if c):
            hdr_idx = i
            break
    if hdr_idx is None:
        return {}
    out = {}
    for ci, name in enumerate(rows[hdr_idx]):
        if not name:
            continue
        m = {}
        for ri in range(hdr_idx + 1, len(rows)):
            v = rows[ri][ci] if ci < len(rows[ri]) else None
            if v not in (None, ''):
                m[str(v).strip().lower()] = str(v).strip()
        if m:
            out[str(name).strip()] = m
    return out
