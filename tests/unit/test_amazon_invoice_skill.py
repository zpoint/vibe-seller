"""Unit tests for the amazon-invoice skill's generate_invoice.py.

Covers the calculation engine (``compute_financials``) and PDF rendering
(``build_pdf``) for several scenarios the agent may hand the script:

* No refund — page-provided subtotal/tax/total are trusted as-is.
* Refund present — page subtotal/total are inconsistent (subtotal is
  pre-refund, total is post-refund on Amazon's order page), so the script
  recomputes gross from ``items + shipping - promotion - refund``.
* Inclusive (e.g. SA/UK) vs exclusive (e.g. MX) tax models with refund.
* Multiple items plus an order-level refund.
* PDF file generation (sanity check — file exists and is non-empty).

All test data is fabricated. No real buyer names, addresses, ASINs, order
IDs, or prices appear in this file.
"""

import importlib.util
from pathlib import Path
import sys

import pytest

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'app'
    / 'skills'
    / 'amazon-invoice'
    / 'generate_invoice.py'
)

_spec = importlib.util.spec_from_file_location(
    'amazon_invoice_generate', _SKILL_PATH
)
generate_invoice = importlib.util.module_from_spec(_spec)
sys.modules['amazon_invoice_generate'] = generate_invoice
_spec.loader.exec_module(generate_invoice)

compute_financials = generate_invoice.compute_financials
build_pdf = generate_invoice.build_pdf
safe_float = generate_invoice.safe_float


# ── safe_float ──────────────────────────────────────


@pytest.mark.unit
class TestSafeFloat:
    def test_plain_number(self):
        assert safe_float('12.34') == 12.34

    def test_currency_prefix_with_space(self):
        assert safe_float('SAR 1,234.56') == 1234.56

    def test_currency_prefix_with_dollar(self):
        assert safe_float('MX$99.50') == 99.50

    def test_negative_number(self):
        assert safe_float('-5.25') == -5.25

    def test_none_returns_zero(self):
        assert safe_float(None) == 0.0

    def test_empty_string_returns_zero(self):
        assert safe_float('') == 0.0

    def test_null_literal_returns_zero(self):
        assert safe_float('null') == 0.0


# ── compute_financials: refund handling ─────────────


@pytest.mark.unit
class TestComputeFinancialsRefund:
    """The critical path: a refund must be subtracted from the gross."""

    def test_inclusive_tax_country_with_refund_recomputes_gross(self):
        """SA (15% VAT-inclusive): gross ignores page subtotal/total and
        is derived from items + shipping − promotion − refund.

        Fabricated numbers (chosen to land on round values):
            items     = 10 * 20.00 = 200.00
            shipping  =              5.00
            promotion =              5.00
            refund    =            100.00
            gross     = 200 + 5 - 5 - 100 = 100.00
            tax       = round(100 * 0.15 / 1.15, 2) = 13.04
            subtotal  = 100 - 13.04 = 86.96
        """
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'items': [
                {
                    'description': 'Widget',
                    'quantity': 10,
                    'amount': 'SAR 200.00',
                }
            ],
            'shipping_total': 'SAR 5.00',
            'promotion': 'SAR 5.00',
            'refund': 'SAR 100.00',
            # These page values are intentionally WRONG (disagreeing
            # with both the pre-refund and post-refund figures) so that
            # a regression that silently trusts page_subtotal / page_tax
            # / page_total instead of recomputing from components would
            # fail the assertions below.
            'subtotal': 'SAR 200.00',
            'tax': 'SAR 50.00',
            'total': 'SAR 180.00',
        }

        result = compute_financials(data)

        assert result['refund'] == 100.00
        # Derived gross = 100.00 — must NOT equal page total (180.00).
        assert result['total'] == 100.00
        # Derived VAT-inclusive tax = 13.04 — must NOT equal page tax (50.00).
        assert result['tax'] == 13.04
        assert result['subtotal'] == 86.96
        assert result['amount_paid'] == 100.00

    def test_exclusive_tax_country_with_refund(self):
        """MX (16% IVA-exclusive): pre-tax gross shrinks by the refund,
        then tax is added on top.

        Fabricated numbers:
            items    = 1 * 200.00 = 200.00
            refund   = 50.00
            subtotal = 200 - 50 = 150.00
            tax      = 150 * 0.16 = 24.00
            total    = 150 + 24 = 174.00
        """
        data = {
            'country': 'MX',
            'currency': 'MXN',
            'items': [
                {
                    'description': 'Gadget',
                    'quantity': 1,
                    'amount': 'MX$200.00',
                }
            ],
            'refund': 'MX$50.00',
        }

        result = compute_financials(data)

        assert result['refund'] == 50.00
        assert result['subtotal'] == 150.00
        assert result['tax'] == 24.00
        assert result['total'] == 174.00

    def test_uk_inclusive_tax_with_refund(self):
        """UK (20% VAT-inclusive) — different country, different rate.

        Fabricated numbers:
            items  = 2 * 60.00 = 120.00
            refund = 60.00
            gross  = 60.00
            tax    = round(60 * 0.20 / 1.20, 2) = 10.00
            sub    = 60 - 10 = 50.00
        """
        data = {
            'country': 'UK',
            'currency': 'GBP',
            'items': [
                {
                    'description': 'Tool',
                    'quantity': 2,
                    'amount': 'GBP 120.00',
                }
            ],
            'refund': 'GBP 60.00',
        }

        result = compute_financials(data)

        assert result['refund'] == 60.00
        assert result['total'] == 60.00
        assert result['tax'] == 10.00
        assert result['subtotal'] == 50.00

    def test_multiple_items_with_order_level_refund(self):
        """Refund is at the order level, not per-item; items table is
        unchanged, but gross drops by the refund.

        Fabricated numbers (SA, 15% inclusive):
            item A = 2 * 50.00 = 100.00
            item B = 3 * 20.00 =  60.00
            shipping           =   5.00
            refund             =  30.00
            gross  = 100 + 60 + 5 - 30 = 135.00
            tax    = round(135 * 0.15 / 1.15, 2) = 17.61
            sub    = 135 - 17.61 = 117.39
        """
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'items': [
                {
                    'description': 'Product A',
                    'quantity': 2,
                    'amount': 'SAR 100.00',
                },
                {
                    'description': 'Product B',
                    'quantity': 3,
                    'amount': 'SAR 60.00',
                },
            ],
            'shipping_total': 'SAR 5.00',
            'refund': 'SAR 30.00',
        }

        result = compute_financials(data)

        assert len(result['items']) == 2
        assert result['refund'] == 30.00
        assert result['total'] == 135.00
        assert result['tax'] == 17.61
        assert result['subtotal'] == 117.39


# ── compute_financials: regression (no refund) ──────


@pytest.mark.unit
class TestComputeFinancialsNoRefund:
    """Existing behavior must be preserved when refund is absent."""

    def test_page_values_trusted_when_all_three_present(self):
        """If the page already shows subtotal, tax, and total and there
        is no refund, the script must use them verbatim (no recompute).
        """
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'items': [
                {
                    'description': 'Item',
                    'quantity': 1,
                    'amount': 'SAR 115.00',
                }
            ],
            'subtotal': 'SAR 100.00',
            'tax': 'SAR 15.00',
            'total': 'SAR 115.00',
        }

        result = compute_financials(data)

        assert result['subtotal'] == 100.00
        assert result['tax'] == 15.00
        assert result['total'] == 115.00
        # refund defaults to 0 when absent
        assert result['refund'] == 0.0

    def test_no_refund_derivation_matches_legacy(self):
        """No refund, no page subtotal/tax — must match the legacy
        inclusive-tax back-calculation (SA 15%).
        """
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'items': [
                {
                    'description': 'Item',
                    'quantity': 1,
                    'amount': 'SAR 115.00',
                }
            ],
        }

        result = compute_financials(data)

        assert result['total'] == 115.00
        assert result['tax'] == 15.00
        assert result['subtotal'] == 100.00
        assert result['refund'] == 0.0


# ── build_pdf: rendering sanity ─────────────────────


@pytest.mark.unit
class TestBuildPdf:
    def test_generates_pdf_with_refund_row(self, tmp_path):
        """The PDF must generate successfully when a refund is present.

        We can't easily inspect the rendered rows without a PDF parser,
        but a non-empty file and no exception is a solid smoke check.
        """
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'invoice_number': 'TEST-ORDER-001',
            'date': '2026-04-20',
            'bill_to': {'name': 'Alice'},
            'ship_to': '123 Test Street, Test City',
            'items': [
                {
                    'description': 'Widget',
                    'quantity': 10,
                    'amount': 'SAR 200.00',
                }
            ],
            'shipping_total': 'SAR 5.00',
            'promotion': 'SAR 5.00',
            'refund': 'SAR 100.00',
        }
        data = compute_financials(data)

        out = tmp_path / 'invoice_with_refund.pdf'
        result_path = build_pdf(data, str(out))

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0

    def test_generates_pdf_without_refund(self, tmp_path):
        """No refund key must not break PDF generation (regression)."""
        data = {
            'country': 'SA',
            'currency': 'SAR',
            'invoice_number': 'TEST-ORDER-002',
            'date': '2026-04-20',
            'bill_to': {'name': 'Bob'},
            'ship_to': '456 Example Ave',
            'items': [
                {
                    'description': 'Sample',
                    'quantity': 1,
                    'amount': 'SAR 115.00',
                }
            ],
            'subtotal': 'SAR 100.00',
            'tax': 'SAR 15.00',
            'total': 'SAR 115.00',
        }
        data = compute_financials(data)

        out = tmp_path / 'invoice_no_refund.pdf'
        result_path = build_pdf(data, str(out))

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0
