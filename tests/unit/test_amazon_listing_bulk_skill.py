"""Unit tests for the amazon-listing skill's scripts/listing_bulk.py.

Pins the invariants that make the flat-file listing tool trustworthy:

* **Locale-generality** — the tool keys every field by its field API
  name (the row containing `item_sku`), never by the localised label
  row above it. The synthetic template here puts **Chinese** labels in
  that row and asserts every operation still works.
* **The operation column** — `create` leaves `update_delete` blank,
  and `update` / `partialupdate` / `delete` write their tokens.
* **Parent-child shape** — a Parent row carries the variation theme and
  no offer; a Child row carries `parent_sku`, the differentiator, and
  the price; the Parent is not warned about child-level required fields.
* **Validation** — an out-of-enum value and a missing required field
  each raise a warning (never a hard failure), so an unseen-but-valid
  token from a new category still writes.

All test data is fabricated — no real store, brand, SKU, or ASIN.
"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.unit

openpyxl = pytest.importorskip('openpyxl')
from openpyxl.comments import Comment  # noqa: E402  (after importorskip guard)

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'app'
    # Live skill tree (config.SKILLS_SUBDIR); matches the ads-bulk test.
    # ``app/skills`` is the frozen 0.12.x copy and is not what ships.
    / 'skills_v2'
    / 'amazon-listing'
    / 'scripts'
    / 'listing_bulk.py'
)
_spec = importlib.util.spec_from_file_location('listing_bulk', _SKILL_PATH)
listing_bulk = importlib.util.module_from_spec(_spec)
sys.modules['listing_bulk'] = listing_bulk
_spec.loader.exec_module(listing_bulk)


# Field API names in column order. Localised labels sit one row above
# them (Chinese here) — the tool must ignore the labels entirely.
FIELDS = [
    'feed_product_type',
    'item_sku',
    'update_delete',
    'brand_name',
    'external_product_id',
    'external_product_id_type',
    'item_name',
    'product_description',
    'recommended_browse_nodes',
    'main_image_url',
    'parent_child',
    'relationship_type',
    'variation_theme',
    'parent_sku',
    'color_name',
    'batteries_required',
    'are_batteries_included',
    'fulfillment_availability#1.quantity',
    'purchasable_offer[marketplace_id=MKTSA]#1.our_price#1.schedule#1.value_with_tax',
]
ZH_LABELS = {
    'feed_product_type': '产品类型',
    'item_sku': '卖家SKU',
    'update_delete': '更新删除',
    'brand_name': '品牌名称',
    'item_name': '商品名称',
    'color_name': '颜色',
    'parent_child': '父子关系',
    'variation_theme': '变体主题',
}
REQUIRED = {
    'feed_product_type',
    'item_sku',
    'brand_name',
    'item_name',
    'product_description',
    'recommended_browse_nodes',
    'external_product_id',
    'external_product_id_type',
    'main_image_url',
    'batteries_required',
    'battery_type',  # battery_type: conditional
    'fulfillment_availability#1.quantity',
    'purchasable_offer[marketplace_id=MKTSA]#1.our_price#1.schedule#1.value_with_tax',
}
ENUMS = {
    'update_delete': ['Update', 'partialupdate', 'delete'],
    'parent_child': ['Parent', 'Child'],
    'relationship_type': ['Variation'],
    'variation_theme': ['Color', 'Size', 'color-size'],
    'external_product_id_type': ['asin', 'upc', 'ean', 'gtin'],
    'recommended_browse_nodes': ['111', '222', '333'],
}


def _make_template(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = listing_bulk.TEMPLATE_SHEET
    # Row 1: signature/group row (decorative). Row 2: localised labels.
    # Row 3: field API names. Row 4+: data (empty in a blank template).
    ws.append(['TemplateType=fptcustom', 'Version=test'])
    ws.append([ZH_LABELS.get(f, f) for f in FIELDS])
    ws.append(list(FIELDS))

    # Data Definitions: header at row 2, then per-field rows.
    dd = wb.create_sheet(listing_bulk.DEFN_SHEET)
    dd.append(['How to complete your inventory template'])
    dd.append([
        'Group Name',
        'Field Name',
        'Local Label Name',
        'Definition and Use',
        'Accepted Values',
        'Example',
        'Required?',
    ])
    for f in FIELDS + ['battery_type']:
        dd.append([
            '',
            f,
            ZH_LABELS.get(f, f),
            '',
            '',
            '',
            'Required' if f in REQUIRED else 'Optional',
        ])

    # Dropdown Lists: field names on row 3, tokens below each column.
    dl = wb.create_sheet(listing_bulk.DROPDOWN_SHEET)
    dl.append([])
    dl.append([])
    enum_fields = list(ENUMS)
    dl.append(enum_fields)
    for i in range(max(len(v) for v in ENUMS.values())):
        dl.append([
            ENUMS[f][i] if i < len(ENUMS[f]) else None for f in enum_fields
        ])
    wb.save(path)


@pytest.fixture
def template(tmp_path):
    p = tmp_path / 'template.xlsx'
    _make_template(str(p))
    return str(p)


def _run(args):
    ns = listing_bulk.argparse.Namespace
    parser_map = {
        'inspect': listing_bulk.cmd_inspect,
        'fill': listing_bulk.cmd_fill,
        'parse-feedback': listing_bulk.cmd_parse_feedback,
    }
    # Route through the real argv parser so the test exercises main().
    old = sys.argv
    sys.argv = ['listing_bulk.py'] + args
    try:
        listing_bulk.main()
    finally:
        sys.argv = old
    return ns, parser_map  # unused; kept for clarity


def _read_rows(path):
    wb = openpyxl.load_workbook(path)
    ws = wb[listing_bulk.TEMPLATE_SHEET]
    names = [c.value for c in ws[3]]
    idx = {n: i for i, n in enumerate(names)}
    rows = []
    for r in ws.iter_rows(min_row=4, values_only=True):
        if any(c is not None for c in r):
            rows.append({n: r[idx[n]] for n in names})
    return rows


def _spec(tmp_path, rows):
    p = tmp_path / 'spec.json'
    p.write_text(json.dumps(rows), encoding='utf-8')
    return str(p)


def test_header_found_by_field_name_not_localised_label(template):
    wb = openpyxl.load_workbook(template)
    ws = wb[listing_bulk.TEMPLATE_SHEET]
    # Header row is row 3 (field names), NOT row 2 (Chinese labels).
    assert listing_bulk._find_header_row(ws) == 3
    required = listing_bulk._load_required_fields(wb)
    assert 'item_sku' in required and 'brand_name' in required
    valid = listing_bulk._load_valid_values(wb)
    assert valid['parent_child'] == {'parent', 'child'}
    assert 'delete' in valid['update_delete']


def test_fill_operation_column(template, tmp_path):
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-1',
                'operation': 'create',
                'fields': {'item_name': 'ACME Thing'},
            },
            {
                'sku': 'W-2',
                'operation': 'update',
                'fields': {'item_name': 'ACME Thing 2'},
            },
            {
                'sku': 'W-3',
                'operation': 'partialupdate',
                'fields': {'item_name': 'ACME Thing 3'},
            },
            {'sku': 'W-4', 'operation': 'delete'},
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    rows = _read_rows(out)
    by_sku = {r['item_sku']: r for r in rows}
    assert by_sku['W-1']['update_delete'] in (None, '')  # create = blank
    assert by_sku['W-2']['update_delete'] == 'Update'
    assert by_sku['W-3']['update_delete'] == 'partialupdate'
    assert by_sku['W-4']['update_delete'] == 'delete'
    # brand default applied to every row.
    assert all(r['brand_name'] == 'ACME' for r in rows)


def test_parent_child_structure(template, tmp_path):
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'P-1',
                'operation': 'create',
                'parentage': 'Parent',
                'variation_theme': 'Color',
                'fields': {
                    'relationship_type': 'Variation',
                    'item_name': 'ACME Socks',
                    'product_description': 'desc',
                    'recommended_browse_nodes': '111',
                    'batteries_required': 'No',
                    'are_batteries_included': 'No',
                },
            },
            {
                'sku': 'P-1-WHT',
                'operation': 'create',
                'parentage': 'Child',
                'parent_sku': 'P-1',
                'variation_theme': 'Color',
                'fields': {
                    'relationship_type': 'Variation',
                    'color_name': 'White',
                    'item_name': 'ACME Socks White',
                    'product_description': 'desc',
                    'recommended_browse_nodes': '111',
                    'external_product_id': '00012345678905',
                    'external_product_id_type': 'upc',
                    'main_image_url': 'https://example.com/w.jpg',
                    'batteries_required': 'No',
                    'are_batteries_included': 'No',
                    'fulfillment_availability#1.quantity': '100',
                    'purchasable_offer[marketplace_id=MKTSA]#1.our_price'
                    '#1.schedule#1.value_with_tax': '29.00',
                },
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    rows = _read_rows(out)
    parent = next(r for r in rows if r['item_sku'] == 'P-1')
    child = next(r for r in rows if r['item_sku'] == 'P-1-WHT')
    assert parent['parent_child'] == 'Parent'
    assert parent['variation_theme'] == 'Color'
    assert parent['parent_sku'] in (None, '')  # parent has no parent
    price_col = (
        'purchasable_offer[marketplace_id=MKTSA]#1.our_price'
        '#1.schedule#1.value_with_tax'
    )
    assert parent[price_col] in (None, '')  # offer is child-level
    assert child['parent_child'] == 'Child'
    assert child['parent_sku'] == 'P-1'
    assert child['color_name'] == 'White'
    assert str(child[price_col]) == '29.00'


def test_parent_not_warned_for_child_level_required(template, tmp_path, capsys):
    """A Parent row omitting offer/price/image/product-id must NOT be
    flagged — those are child-level. A Child omitting them must be."""
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'P-1',
                'operation': 'create',
                'parentage': 'Parent',
                'variation_theme': 'Color',
                'fields': {
                    'relationship_type': 'Variation',
                    'item_name': 'ACME Socks',
                    'product_description': 'desc',
                    'recommended_browse_nodes': '111',
                    'batteries_required': 'No',
                    'are_batteries_included': 'No',
                },
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    err = capsys.readouterr().err
    # Parent: no complaint about child-level fields...
    assert 'external_product_id' not in err
    assert 'our_price' not in err
    assert 'main_image_url' not in err
    # ...and no complaint about battery_type (declared no batteries).
    assert 'battery_type' not in err


def test_out_of_enum_and_missing_required_warn_not_fail(
    template, tmp_path, capsys
):
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-1',
                'operation': 'create',
                'variation_theme': 'PurpleHaze',  # not in enum
                'fields': {'relationship_type': 'Variation'},
            },  # missing lots
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    err = capsys.readouterr().err
    assert 'PurpleHaze' in err  # enum warning
    assert 'not in template valid values' in err
    assert 'missing required field' in err  # required warning
    # File still written despite warnings.
    assert Path(out).exists()
    assert _read_rows(out)[0]['variation_theme'] == 'PurpleHaze'


def test_asin_folds_into_product_id(template, tmp_path):
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-1',
                'operation': 'update',
                'asin': 'B0ABCDE123',
                'fields': {'item_name': 'x'},
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    row = _read_rows(out)[0]
    assert row['external_product_id'] == 'B0ABCDE123'
    assert row['external_product_id_type'] == 'asin'


def test_fill_clears_preexisting_data_rows(template, tmp_path):
    """A reused template with a stale data row must not upload it —
    fill clears the data area before writing the spec rows."""
    wb = openpyxl.load_workbook(template)
    ws = wb[listing_bulk.TEMPLATE_SHEET]
    # Row 4 (first data row): a stale SKU from a prior run.
    ws.append(['socks', 'STALE-SKU'] + [''] * (len(FIELDS) - 2))
    wb.save(template)

    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-NEW',
                'operation': 'create',
                'fields': {'item_name': 'x'},
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    skus = [r['item_sku'] for r in _read_rows(out)]
    assert 'STALE-SKU' not in skus
    assert skus == ['W-NEW']


def test_parse_feedback(tmp_path, capsys):
    report = tmp_path / 'report.txt'
    report.write_text(
        'original-record-number\tsku\terror-type\terror-code\t'
        'error-message\n'
        '4\tW-1\tError\t8541\tMissing required field external_product_id\n'
        '5\tW-2\tWarning\t90220\tImage will be reviewed\n',
        encoding='utf-8',
    )
    with pytest.raises(SystemExit) as ei:  # exit 1 because an error
        _run(['parse-feedback', str(report)])
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert 'W-1' in out and 'external_product_id' in out
    assert '1 error(s), 1 warning(s)' in out


def test_fill_emits_tsv_upload_file(template, tmp_path):
    """fill writes a tab-delimited .txt sibling (the upload artefact) with
    a uniform column count. Uploading the .xlsm triggers Amazon's 90502
    FATAL; the .txt is what reaches content validation."""
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-1',
                'operation': 'create',
                'fields': {'item_name': 'ACME Thing'},
            },
            {
                'sku': 'W-2',
                'operation': 'create',
                'fields': {'item_name': 'ACME Thing 2'},
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    txt = tmp_path / 'out.txt'
    assert txt.exists()
    lines = [ln for ln in txt.read_text(encoding='utf-8').split('\n') if ln]
    grid = [ln.split('\t') for ln in lines]
    # Uniform width -> no ragged rows -> no column drift on Amazon's side.
    assert len({len(r) for r in grid}) == 1
    names = grid[2]  # row 3 = field API names
    assert 'item_sku' in names
    sku_col = names.index('item_sku')
    assert {'W-1', 'W-2'} <= {r[sku_col] for r in grid[3:]}


def test_enum_value_canonicalised_to_template_case(template, tmp_path):
    """A spec enum value in the wrong case is written in the template's
    exact case. Amazon is case-strict on some fields (verified live:
    apparel_size_system accepts UAE/KSA but rejects uae/ksa)."""
    spec = {
        'product_type': 'socks',
        'brand': 'ACME',
        'rows': [
            {
                'sku': 'W-1',
                'operation': 'create',
                'variation_theme': 'color',  # lowercase; template has 'Color'
                'fields': {'item_name': 'ACME Thing'},
            },
        ],
    }
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', template, '--spec', _spec(tmp_path, spec), '--out', out])
    rows = _read_rows(out)
    assert rows[0]['variation_theme'] == 'Color'


def test_parse_feedback_extracts_cell_comments(tmp_path):
    """parse-feedback's extractor surfaces the per-cell 批注 (the
    field-level verdict) from the report's Template tab, split into
    individual ERROR/WARNING lines with Excel's _x000d_ marker cleaned.
    This is the engine of the self-correct loop."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = listing_bulk.TEMPLATE_SHEET
    ws.append(['signature'])  # row 1
    ws.append(['Seller SKU', 'Colour'])  # row 2 localised labels
    ws.append(['item_sku', 'color_name'])  # row 3 field API names
    ws.append(['W-1', 'White'])  # row 4 data
    # Amazon stacks several messages in one comment, split by _x000d_.
    ws.cell(row=4, column=1).comment = Comment(
        'ERROR : missing standard_product_id_x000d_WARNING : missing material',
        'Amazon',
    )
    p = tmp_path / 'report.xlsx'
    wb.save(str(p))

    errs = list(listing_bulk._template_cell_errors(str(p)))
    msgs = [m for _, _, m in errs]
    assert all(sku == 'W-1' for sku, _, _ in errs)
    assert any(
        m.startswith('ERROR') and 'standard_product_id' in m for m in msgs
    )
    assert any(m.startswith('WARNING') and 'material' in m for m in msgs)
    # _x000d_ artifact must be cleaned (split into separate lines).
    assert not any('_x000d_' in m for m in msgs)


# --- offer routing to the target marketplace (multi-marketplace bug) ---

_SA = 'A17E79C6D8DWNP'
_AE = 'A2VIGQ35RCS4UG'
_SA_PRICE = f'purchasable_offer[marketplace_id={_SA}]#1.our_price#1.schedule#1.value_with_tax'
_AE_PRICE = f'purchasable_offer[marketplace_id={_AE}]#1.our_price#1.schedule#1.value_with_tax'


def _make_mkt_template(path):
    """A template with a SEPARATE offer-price column per marketplace."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = listing_bulk.TEMPLATE_SHEET
    fields = [
        'feed_product_type', 'item_sku', 'brand_name', 'update_delete',
        'item_name', 'parent_child', 'variation_theme', 'parent_sku',
        'color_name', 'fulfillment_availability#1.quantity',
        _SA_PRICE, _AE_PRICE,
    ]
    ws.append(['TemplateType=fptcustom'])
    ws.append(['label:' + f for f in fields])  # localised label row
    ws.append(list(fields))  # field API-name row (header)
    dd = wb.create_sheet(listing_bulk.DEFN_SHEET)
    dd.append(['x'])
    dd.append(['Group Name', 'Field Name', 'Local Label Name',
               'Definition and Use', 'Accepted Values', 'Example', 'Required?'])
    for f in fields:
        # The template marks the AE block required (the account is UAE-primary)
        # -- exactly the trap that made a run fill AE for an SA listing.
        dd.append(['', f, f, '', '', '', 'Required' if f == _AE_PRICE else 'Optional'])
    wb.save(path)


@pytest.fixture
def mkt_template(tmp_path):
    p = tmp_path / 'mkt.xlsx'
    _make_mkt_template(str(p))
    return str(p)


def test_fill_routes_bare_our_price_to_target_marketplace(mkt_template, tmp_path):
    spec = _spec(tmp_path, {
        'marketplace': 'SA', 'product_type': 'socks',
        'rows': [{'sku': 'K-WHT', 'parentage': 'Child', 'variation_theme': 'Color',
                  'fields': {'item_name': 'x', 'color_name': 'White',
                             'feed_product_type': 'socks', 'our_price': '19.99'}}],
    })
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', mkt_template, '--spec', spec, '--out', out])
    row = _read_rows(out)[0]
    assert row[_SA_PRICE] == '19.99'   # target marketplace got the price
    assert row[_AE_PRICE] in (None, '')  # the wrong block stayed empty


def test_fill_warns_when_target_marketplace_has_no_offer(mkt_template, tmp_path, capsys):
    # Agent hand-picked the AE column but is listing on SA. We DON'T silently
    # move it (that confused agents) -- leave it as written and WARN that SA
    # has no offer (=> Missing offer).
    spec = _spec(tmp_path, {
        'marketplace': 'SA', 'product_type': 'socks',
        'rows': [{'sku': 'K-WHT', 'parentage': 'Child', 'variation_theme': 'Color',
                  'fields': {'item_name': 'x', 'color_name': 'White',
                             'feed_product_type': 'socks', _AE_PRICE: '19.99'}}],
    })
    out = str(tmp_path / 'out.xlsx')
    _run(['fill', mkt_template, '--spec', spec, '--out', out])
    row = _read_rows(out)[0]
    assert row[_AE_PRICE] == '19.99'      # explicit column left as written
    assert row[_SA_PRICE] in (None, '')
    assert 'Missing offer' in capsys.readouterr().err


def test_fill_errors_when_price_set_without_marketplace(mkt_template, tmp_path):
    spec = _spec(tmp_path, {
        'product_type': 'socks',
        'rows': [{'sku': 'K-WHT', 'parentage': 'Child',
                  'fields': {'item_name': 'x', 'our_price': '19.99'}}],
    })
    out = str(tmp_path / 'out.xlsx')
    with pytest.raises(SystemExit) as e:
        _run(['fill', mkt_template, '--spec', spec, '--out', out])
    assert 'marketplace' in str(e.value)
