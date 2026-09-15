"""The noon-listing NIS pre-flight (``scripts/validate_nis.py``).

Each check here corresponds to a way a live NIS import has already
reported ``Complete`` while creating nothing, or created a listing that
was only half-translated:

* a leftover template **sample row** — noon's Error File then reports
  only that row and hides every real row's ``content_error``;
* a **select value** outside the template's ``valid values`` sheet —
  the failure noon actually named (``Invalid value ... for select
  attribute``);
* an out-of-range **hs_code** (noon requires 6-14 chars);
* an English field whose **Arabic half** is empty, which imports clean
  and ships a half-translated listing.

All test data is fabricated — no real store, brand, SKU, or ASIN.
"""

import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.unit

openpyxl = pytest.importorskip('openpyxl')
from openpyxl import Workbook  # noqa: E402  (after importorskip guard)

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'app'
    / 'skills_v2'
    / 'noon-listing'
    / 'scripts'
    / 'validate_nis.py'
)
_spec = importlib.util.spec_from_file_location('validate_nis', _SKILL_PATH)
validate_nis = importlib.util.module_from_spec(_spec)
sys.modules['validate_nis'] = validate_nis
_spec.loader.exec_module(validate_nis)


HEADERS = [
    'family',
    'product_type',
    'product_subtype',
    'brand',
    'seller_sku',
    'widget_finish',
    'shipping_length_unit',
    'hs_code',
    'shipping_weight_unit',
    'product_title_en',
    'product_title_ar',
    'feature_bullet_1_en',
    'feature_bullet_1_ar',
]

# Mirrors the template's own sheet: only the attribute columns are
# authoritative. ``family`` / ``product_type`` are listed in the real
# template too but in a UI form noon does not require, which is why the
# validator exempts them.
VALID = {
    'family': ['widgets'],
    'product_type': ['Widget Parts'],
    'widget_finish': ['Matte', 'Gloss'],
    'shipping_length_unit': ['Centimeter', 'Inch', 'Meter'],
    'shipping_weight_unit': ['Gram', 'Kilogram'],
}

GOOD_ROW = {
    'family': 'Widgets',
    'product_type': 'Widget Parts',
    'product_subtype': 'Widget',
    'brand': 'acme',
    'seller_sku': 'WIDGET-006',
    'widget_finish': 'Matte',
    # short unit form; the sheet spells it out (noon accepts both)
    'shipping_length_unit': 'cm',
    'hs_code': '611595',
    'shipping_weight_unit': 'Gram',
    'product_title_en': 'acme Widget Six Pack, Cotton Blend, For Everyone',
    'product_title_ar': 'منتج تجريبي من أكمي، عبوة من ستة، مزيج قطني للجميع',
    'feature_bullet_1_en': '[Durable] Built to last through daily use.',
    'feature_bullet_1_ar': '[متين] مصنوع ليدوم طويلا مع الاستخدام اليومي.',
}


def _book_with(tmp_path, headers, rows, name='nis.xlsx'):
    """Write a NIS-shaped workbook with a caller-supplied header row."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'template_data'
    for col, field in enumerate(headers, start=1):
        ws.cell(row=validate_nis.HEADER_ROW, column=col).value = field
    for i, row in enumerate(rows):
        r = validate_nis.FIRST_DATA_ROW + i
        for col, field in enumerate(headers, start=1):
            ws.cell(row=r, column=col).value = row.get(field)
    vv = wb.create_sheet('valid values')
    for col, (field, vals) in enumerate(VALID.items(), start=1):
        vv.cell(row=1, column=col).value = field
        for i, v in enumerate(vals):
            vv.cell(row=2 + i, column=col).value = v
    path = tmp_path / name
    wb.save(path)
    return path


def _book(tmp_path, rows, name='nis.xlsx'):
    """Write a minimal NIS-shaped workbook and return its path."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'template_data'
    for col, field in enumerate(HEADERS, start=1):
        # Rows 1-8 are the template's meta block; row 9 is the header.
        ws.cell(row=validate_nis.HEADER_ROW, column=col).value = field
    for i, row in enumerate(rows):
        r = validate_nis.FIRST_DATA_ROW + i
        for col, field in enumerate(HEADERS, start=1):
            ws.cell(row=r, column=col).value = row.get(field)
    vv = wb.create_sheet('valid values')
    for col, (field, vals) in enumerate(VALID.items(), start=1):
        vv.cell(row=1, column=col).value = field
        for i, v in enumerate(vals):
            vv.cell(row=2 + i, column=col).value = v
    path = tmp_path / name
    wb.save(path)
    return path


class TestCleanSheet:
    def test_valid_sheet_has_no_errors(self, tmp_path):
        errors, warnings = validate_nis.validate(_book(tmp_path, [GOOD_ROW]))
        assert errors == []
        assert warnings == []

    def test_exit_zero(self, tmp_path):
        path = _book(tmp_path, [GOOD_ROW])
        assert validate_nis.main([str(path)]) == 0


class TestSampleRow:
    def test_leftover_sample_row_is_an_error(self, tmp_path):
        sample = {
            'family': 'Widgets',
            'product_type': 'Widget Parts',
            'product_subtype': 'Widget',
        }
        errors, _ = validate_nis.validate(_book(tmp_path, [sample, GOOD_ROW]))
        assert any('template sample row' in e for e in errors)

    def test_a_fully_blank_row_is_not_flagged(self, tmp_path):
        errors, _ = validate_nis.validate(_book(tmp_path, [GOOD_ROW, {}]))
        assert errors == []

    def test_any_row_without_a_sku_is_an_error(self, tmp_path):
        # Not the sample row's shape (it carries content, not just the
        # category). Every per-row check keys off seller_sku, so without
        # this the row would skip validation and the file report OK.
        orphan = {
            'product_title_en': 'acme Widget Six Pack',
            'product_title_ar': 'منتج تجريبي من أكمي',
        }
        errors, _ = validate_nis.validate(_book(tmp_path, [GOOD_ROW, orphan]))
        assert any('no seller_sku' in e for e in errors)


class TestEmptySheet:
    """An empty data region must fail closed, not report OK."""

    def test_header_only_sheet_is_an_error(self, tmp_path):
        path = _book(tmp_path, [])
        errors, _ = validate_nis.validate(path)
        assert any('no data rows' in e for e in errors)

    def test_header_only_sheet_exits_non_zero(self, tmp_path):
        assert validate_nis.main([str(_book(tmp_path, []))]) == 1

    def test_only_blank_rows_is_an_error(self, tmp_path):
        errors, _ = validate_nis.validate(_book(tmp_path, [{}, {}]))
        assert any('no data rows' in e for e in errors)


class TestSelectValues:
    def test_value_outside_valid_values_is_an_error(self, tmp_path):
        bad = dict(GOOD_ROW, widget_finish='Sparkly')
        errors, _ = validate_nis.validate(_book(tmp_path, [bad]))
        assert any(
            "widget_finish='Sparkly' is not a valid value" in e for e in errors
        )

    def test_display_form_is_accepted(self, tmp_path):
        # ``Widgets`` vs the sheet's ``widgets``: noon accepts the UI
        # display form for category columns, proven by an import that
        # created SKUs while using it.
        errors, _ = validate_nis.validate(_book(tmp_path, [GOOD_ROW]))
        assert not any('family=' in e for e in errors)

    def test_display_form_columns_are_still_checked(self, tmp_path):
        # The tolerance is normalisation, NOT an exemption: an unknown
        # value in the same column must still fail. (Reverting to the
        # blanket skip makes this pass silently.)
        bad = dict(GOOD_ROW, family='NotAWidget')
        errors, _ = validate_nis.validate(_book(tmp_path, [bad]))
        assert any("family='NotAWidget'" in e for e in errors)

    def test_punctuation_and_spacing_differences_are_accepted(self, tmp_path):
        row = dict(GOOD_ROW, product_type='Widget-Parts')
        errors, _ = validate_nis.validate(_book(tmp_path, [row]))
        assert not any('product_type=' in e for e in errors)

    def test_short_unit_alias_is_accepted(self, tmp_path):
        errors, _ = validate_nis.validate(_book(tmp_path, [GOOD_ROW]))
        assert not any('shipping_length_unit' in e for e in errors)

    def test_near_miss_unit_is_rejected(self, tmp_path):
        row = dict(GOOD_ROW, shipping_length_unit='cmm')
        errors, _ = validate_nis.validate(_book(tmp_path, [row]))
        assert any("shipping_length_unit='cmm'" in e for e in errors)

    def test_weight_unit_warns_but_does_not_block(self, tmp_path):
        row = dict(GOOD_ROW, shipping_weight_unit='g')
        errors, warnings = validate_nis.validate(_book(tmp_path, [row]))
        assert errors == []
        assert any('shipping_weight_unit' in w for w in warnings)


class TestHsCode:
    @pytest.mark.parametrize('code', ['6115', '1' * 15])
    def test_out_of_range_is_an_error(self, tmp_path, code):
        errors, _ = validate_nis.validate(
            _book(tmp_path, [dict(GOOD_ROW, hs_code=code)])
        )
        assert any('hs_code' in e for e in errors)

    def test_blank_is_fine(self, tmp_path):
        errors, _ = validate_nis.validate(
            _book(tmp_path, [dict(GOOD_ROW, hs_code=None)])
        )
        assert errors == []


class TestLocaleParity:
    def test_missing_arabic_half_is_an_error(self, tmp_path):
        row = dict(GOOD_ROW, feature_bullet_1_ar=None)
        errors, _ = validate_nis.validate(_book(tmp_path, [row]))
        assert any(
            'feature_bullet_1_en is filled but feature_bullet_1_ar' in e
            for e in errors
        )

    def test_stub_translation_warns(self, tmp_path):
        row = dict(GOOD_ROW, product_title_ar='أكمي')
        errors, warnings = validate_nis.validate(_book(tmp_path, [row]))
        assert errors == []
        assert any('likely a stub' in w for w in warnings)

    def test_english_blank_needs_no_arabic(self, tmp_path):
        row = dict(GOOD_ROW, product_title_en=None, product_title_ar=None)
        errors, _ = validate_nis.validate(_book(tmp_path, [row]))
        assert errors == []


class TestNotANisSheet:
    def test_missing_seller_sku_column_is_reported(self, tmp_path):
        wb = Workbook()
        wb.active.title = 'template_data'
        wb.active.cell(row=validate_nis.HEADER_ROW, column=1).value = 'nope'
        path = tmp_path / 'other.xlsx'
        wb.save(path)
        errors, _ = validate_nis.validate(path)
        assert any('no seller_sku column' in e for e in errors)


class TestMarketScope:
    """Per-marketplace fields stay blank outside the task's scope.

    noon cannot clear them afterwards (a blank cell in an update means
    "no change"), so a value invented at upload time is permanent.
    """

    def test_out_of_scope_marketplace_is_an_error(self, tmp_path):
        headers = HEADERS + ['msrp_ae', 'msrp_eg']
        row = dict(GOOD_ROW, msrp_ae='100.00', msrp_eg='100.00')
        path = _book_with(tmp_path, headers, [row])
        errors, _ = validate_nis.validate(path, markets={'AE'})
        assert any('msrp_eg' in e for e in errors)
        assert not any('msrp_ae' in e for e in errors)

    def test_no_markets_given_disables_the_scope_check(self, tmp_path):
        headers = HEADERS + ['msrp_ae', 'msrp_eg']
        row = dict(GOOD_ROW, msrp_ae='100.00', msrp_eg='100.00')
        path = _book_with(tmp_path, headers, [row])
        errors, _ = validate_nis.validate(path, markets=None)
        assert not any('covers' in e for e in errors)

    def test_same_price_across_marketplaces_warns(self, tmp_path):
        headers = HEADERS + ['msrp_ae', 'msrp_sa']
        row = dict(GOOD_ROW, msrp_ae='100.00', msrp_sa='100.00')
        path = _book_with(tmp_path, headers, [row])
        _, warnings = validate_nis.validate(path, markets={'AE', 'SA'})
        assert any('repeated across' in w for w in warnings)

    def test_distinct_prices_do_not_warn(self, tmp_path):
        headers = HEADERS + ['msrp_ae', 'msrp_sa']
        row = dict(GOOD_ROW, msrp_ae='100.00', msrp_sa='120.00')
        path = _book_with(tmp_path, headers, [row])
        _, warnings = validate_nis.validate(path, markets={'AE', 'SA'})
        assert not any('repeated across' in w for w in warnings)
