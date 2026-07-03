#!/usr/bin/env python3
"""Generate a tax invoice PDF from JSON input.

Reads order JSON from stdin, computes taxes/totals, outputs an A4 PDF.

The agent extracts RAW values from the Amazon order page (strings like
"USD 1,234.56") and passes them here.  This script handles:
  - Currency-string -> float parsing  ("USD 1,234.56" -> 1234.56)
  - Per-item amount calculation       (quantity x unit_price)
  - Tax derivation by country rules   (inclusive/exclusive per country)
  - Subtotal / total reconciliation
  - PDF rendering via ReportLab

Required input field: "country" (2-letter ISO code, e.g. "US").
The agent always knows the country from store metadata
(platform_countries) or the task_country parameter.

Usage:
    echo '<json>' | python generate_invoice.py --output invoice.pdf
"""

import argparse
import json
from pathlib import Path
import re
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Country tax rules ───────────────────────────────
#
# All Amazon marketplace countries as of 2026.
# 'rate'      — standard VAT/GST/IVA rate (decimal)
# 'inclusive'  — True if order-page prices include tax
# 'name'      — tax label shown on invoice
# 'currency'  — default currency code
#
# Sources:
#   globalvatcompliance.com/globalvatnews/world-countries-vat-rates-2020
#   vatupdate.com/2026/01/05/global-vat-rates-by-country-2026
#   taxsummaries.pwc.com/quick-charts/value-added-tax-vat-rates

COUNTRY_TAX = {
    # ── Middle East & Africa ────────────────────────
    'SA': {
        'rate': 0.15,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'SAR',
    },
    'AE': {
        'rate': 0.05,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'AED',
    },
    'EG': {
        'rate': 0.14,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'EGP',
    },
    'ZA': {
        'rate': 0.15,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'ZAR',
    },
    'TR': {
        'rate': 0.20,
        'inclusive': True,
        'name': 'KDV',
        'currency': 'TRY',
    },
    # ── Europe ──────────────────────────────────────
    'UK': {
        'rate': 0.20,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'GBP',
    },
    'GB': {  # alias
        'rate': 0.20,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'GBP',
    },
    'DE': {
        'rate': 0.19,
        'inclusive': True,
        'name': 'MwSt',
        'currency': 'EUR',
    },
    'FR': {
        'rate': 0.20,
        'inclusive': True,
        'name': 'TVA',
        'currency': 'EUR',
    },
    'IT': {
        'rate': 0.22,
        'inclusive': True,
        'name': 'IVA',
        'currency': 'EUR',
    },
    'ES': {
        'rate': 0.21,
        'inclusive': True,
        'name': 'IVA',
        'currency': 'EUR',
    },
    'NL': {
        'rate': 0.21,
        'inclusive': True,
        'name': 'BTW',
        'currency': 'EUR',
    },
    'PL': {
        'rate': 0.23,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'PLN',
    },
    'SE': {
        'rate': 0.25,
        'inclusive': True,
        'name': 'Moms',
        'currency': 'SEK',
    },
    'BE': {
        'rate': 0.21,
        'inclusive': True,
        'name': 'BTW',
        'currency': 'EUR',
    },
    'IE': {
        'rate': 0.23,
        'inclusive': True,
        'name': 'VAT',
        'currency': 'EUR',
    },
    # ── Asia-Pacific ────────────────────────────────
    'JP': {
        'rate': 0.10,
        'inclusive': True,
        'name': 'CT',
        'currency': 'JPY',
    },
    'IN': {
        'rate': 0.18,
        'inclusive': True,
        'name': 'GST',
        'currency': 'INR',
    },
    'SG': {
        'rate': 0.09,
        'inclusive': True,
        'name': 'GST',
        'currency': 'SGD',
    },
    'AU': {
        'rate': 0.10,
        'inclusive': True,
        'name': 'GST',
        'currency': 'AUD',
    },
    # ── Americas ────────────────────────────────────
    'US': {
        'rate': 0.00,
        'inclusive': False,
        'name': 'Tax',
        'currency': 'USD',
    },
    'CA': {
        'rate': 0.05,
        'inclusive': False,
        'name': 'GST',
        'currency': 'CAD',
    },
    'MX': {
        'rate': 0.16,
        'inclusive': False,
        'name': 'IVA',
        'currency': 'MXN',
    },
    'BR': {
        'rate': 0.17,
        'inclusive': True,
        'name': 'ICMS',
        'currency': 'BRL',
    },
}

# Reverse lookup: currency -> country (for fallback only)
_CURRENCY_TO_COUNTRY = {}
for _cc, _info in COUNTRY_TAX.items():
    cur = _info['currency']
    if cur not in _CURRENCY_TO_COUNTRY:
        _CURRENCY_TO_COUNTRY[cur] = _cc


# ── Parsing helpers ─────────────────────────────────


def safe_float(value) -> float:
    """Parse a currency string to float.

    Handles formats like "USD 1,234.56", "$1,000.00", "-1.33", plain
    numbers, None, and 'null'.
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s or s.lower() == 'null':
        return 0.0
    # Strip everything except digits, dots, and minus signs
    cleaned = re.sub(r'[^\d.\-]', '', s)
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def resolve_country(data: dict) -> str:
    """Return the 2-letter country code from input data.

    The agent MUST provide 'country'.  Currency is a fallback only.
    """
    country = data.get('country', '').upper()
    if country and country in COUNTRY_TAX:
        return country

    # Fallback: try currency field
    currency = data.get('currency', '').upper()
    if currency and currency in _CURRENCY_TO_COUNTRY:
        return _CURRENCY_TO_COUNTRY[currency]

    return country  # return whatever was given, even if unknown


# ── Calculation engine ──────────────────────────────


def compute_financials(data: dict) -> dict:
    """Compute item amounts, subtotals, tax, and grand total.

    Mutates and returns *data* with all numeric fields set.

    Priority:
      1. If the page provided subtotal + tax + total and they look sane,
         use them as-is (the "authoritative page values" path).
      2. Otherwise, derive from item-level prices + country tax rules.
    """
    country = resolve_country(data)
    data['country'] = country
    tax_info = COUNTRY_TAX.get(country, COUNTRY_TAX['US'])
    data['tax_rate'] = tax_info['rate']

    # Default currency from country if not explicitly set
    if not data.get('currency'):
        data['currency'] = tax_info.get('currency', '')

    # ── Process items ───────────────────────────────
    items = data.get('items', [])
    items_total = 0.0
    for item in items:
        qty = safe_float(item.get('quantity', 1))
        if qty == 0:
            qty = 1
        # Use provided amount if available, else compute from unit_price
        if item.get('amount'):
            amount = safe_float(item['amount'])
        else:
            unit_price = safe_float(item.get('unit_price', 0))
            amount = qty * unit_price
        item['quantity'] = qty
        item['amount'] = amount
        items_total += amount

    shipping = abs(safe_float(data.get('shipping_total', 0)))
    promotion = abs(safe_float(data.get('promotion', 0)))
    refund = abs(safe_float(data.get('refund', 0)))
    data['shipping_total'] = shipping
    data['promotion'] = promotion
    data['refund'] = refund

    # ── Try page-provided totals first ──────────────
    page_subtotal = safe_float(data.get('subtotal', 0))
    page_tax = safe_float(data.get('tax', 0))
    page_total = safe_float(data.get('total', 0))
    page_paid = safe_float(data.get('amount_paid', 0))

    if refund == 0 and page_subtotal > 0 and page_tax > 0 and page_total > 0:
        # Page gave us all three and there's no refund — trust them.
        # When a refund is present, Amazon's order page shows subtotal
        # pre-refund and total post-refund, so page values disagree and
        # we must recompute from components instead.
        data['subtotal'] = page_subtotal
        data['tax'] = page_tax
        data['total'] = page_total
        data['amount_paid'] = page_paid if page_paid > 0 else page_total
        return data

    # ── Derive from items + tax rules ───────────────
    # gross = items + shipping - promotion - refund
    gross = items_total + shipping - promotion - refund

    # When refund > 0, ignore page_tax and page_total — on Amazon's
    # order page they reflect pre-refund amounts and would undo the
    # component-based recompute above.
    if page_tax > 0 and refund == 0:
        # Page gave explicit tax but not subtotal/total
        tax_amount = page_tax
        subtotal = gross - tax_amount
    elif tax_info['inclusive']:
        # Prices already include tax -> back-calculate
        rate = tax_info['rate']
        if rate > 0:
            tax_amount = round(gross * rate / (1 + rate), 2)
        else:
            tax_amount = 0.0
        subtotal = gross - tax_amount
    else:
        # Prices are pre-tax -> add tax on top
        rate = tax_info['rate']
        subtotal = gross
        tax_amount = round(subtotal * rate, 2)
        gross = subtotal + tax_amount

    if page_total > 0 and refund == 0:
        total = page_total
    else:
        total = gross

    data['subtotal'] = round(subtotal, 2)
    data['tax'] = round(tax_amount, 2)
    data['total'] = round(total, 2)
    data['amount_paid'] = page_paid if page_paid > 0 else round(total, 2)
    return data


def ensure_string(value) -> str:
    """Ensure a value is converted to string for PDF generation."""
    if value is None:
        return ''
    return str(value)


# ── PDF rendering ───────────────────────────────────


def build_pdf(data: dict, output_path: str) -> str:
    """Render the computed *data* dict to a PDF at *output_path*."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles matching reference format
    styles.add(
        ParagraphStyle(
            name='CompanyName',
            parent=styles['Heading1'],
            fontSize=16,
            alignment=0,  # Left alignment
        )
    )
    styles.add(
        ParagraphStyle(
            name='InvoiceTitle',
            parent=styles['Heading1'],
            fontSize=14,
            alignment=2,  # Right alignment
        )
    )
    styles.add(
        ParagraphStyle(
            name='InvoiceNumber',
            parent=styles['Normal'],
            fontSize=12,
            alignment=2,  # Right alignment
            spaceAfter=0.5 * cm,
        )
    )
    styles.add(
        ParagraphStyle(
            name='SectionTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name='SectionContent',
            parent=styles['Normal'],
            fontSize=10,
            leftIndent=0,
            spaceAfter=0.2 * cm,
        )
    )
    styles.add(
        ParagraphStyle(
            name='TableHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            alignment=1,  # Center alignment
        )
    )
    styles.add(
        ParagraphStyle(
            name='TableCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            wordWrap='CJK',
        )
    )
    styles.add(
        ParagraphStyle(
            name='AmountCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            alignment=2,  # Right alignment
        )
    )
    styles.add(
        ParagraphStyle(
            name='TotalsLabel',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            alignment=2,  # Right alignment
        )
    )
    styles.add(
        ParagraphStyle(
            name='Notes',
            parent=styles['Normal'],
            fontSize=9,
            spaceAfter=0.2 * cm,
        )
    )

    elements = []
    currency = data.get('currency', '')

    def fmt(val):
        """Format a number as currency string."""
        return f'{currency} {val:,.2f}'.strip()

    # ── Header ──────────────────────────────────────
    seller_name = data.get('seller_entity', '')
    header_data = [
        [
            Paragraph(ensure_string(seller_name), styles['CompanyName']),
            Paragraph('TAX INVOICE', styles['InvoiceTitle']),
        ],
        [
            '',
            Paragraph(
                ensure_string(data.get('invoice_number', '')),
                styles['InvoiceNumber'],
            ),
        ],
    ]

    header_table = Table(header_data, colWidths=[doc.width / 2, doc.width / 2])
    header_table.setStyle(
        TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ])
    )
    elements.append(header_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ── Bill-to / Ship-to ───────────────────────────
    bill_to = data.get('bill_to', {})
    if isinstance(bill_to, str):
        bill_to = {'name': bill_to}

    bill_to_name = bill_to.get('name', '')
    bill_to_entity = bill_to.get('entity', '')
    bill_to_vat = bill_to.get('vat', '')
    bill_to_rfc = bill_to.get('rfc', '')
    bill_to_trn = bill_to.get('trn', '')
    bill_to_address = bill_to.get('address', '')

    # Build Bill To content
    bill_to_content = f'Name: {bill_to_name}'
    if bill_to_entity:
        bill_to_content += f'<br/>Entity: {bill_to_entity}'
    if bill_to_address:
        addr = bill_to_address.replace('\n', '<br/>')
        bill_to_content += f'<br/>Address: {addr}'
    if bill_to_vat:
        bill_to_content += f'<br/>VAT: {bill_to_vat}'
    if bill_to_rfc:
        bill_to_content += f'<br/>RFC: {bill_to_rfc}'
    if bill_to_trn:
        bill_to_content += f'<br/>TRN: {bill_to_trn}'

    ship_to = str(data.get('ship_to', '')).replace('\n', '<br/>')
    date = data.get('date', '')
    store = data.get('store', '')

    address_data = [
        [
            Paragraph('<b>Bill To:</b>', styles['SectionTitle']),
            Paragraph('<b>Ship To:</b>', styles['SectionTitle']),
        ],
        [
            Paragraph(ensure_string(bill_to_content), styles['SectionContent']),
            Paragraph(ensure_string(ship_to), styles['SectionContent']),
        ],
        [
            '',
            Paragraph(
                f'<b>Date:</b> {ensure_string(date)}', styles['SectionContent']
            ),
        ],
    ]

    if store:
        address_data.append([
            '',
            Paragraph(
                f'<b>Store:</b> {ensure_string(store)}',
                styles['SectionContent'],
            ),
        ])

    address_table = Table(
        address_data, colWidths=[doc.width / 2, doc.width / 2]
    )
    address_table.setStyle(
        TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0.1 * cm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.1 * cm),
        ])
    )
    elements.append(address_table)
    elements.append(Spacer(1, 1 * cm))

    # ── Items table ─────────────────────────────────
    items = data.get('items', [])

    # Simplified 3-column table matching reference format
    table_data = [
        [
            Paragraph('Item', styles['TableHeader']),
            Paragraph('Quantity', styles['TableHeader']),
            Paragraph('Amount', styles['TableHeader']),
        ]
    ]

    for item in items:
        # Use raw amount string if available (from page), else format computed amount
        amt = item.get('amount', '')
        if amt == '' and item.get('unit_price', '') != '':
            amt = fmt(item.get('amount', 0))
        table_data.append([
            Paragraph(
                ensure_string(item.get('description', '')), styles['TableCell']
            ),
            Paragraph(
                ensure_string(item.get('quantity', '')), styles['TableCell']
            ),
            Paragraph(ensure_string(amt), styles['AmountCell']),
        ])

    # Proportional column widths (total = doc.width)
    col_widths = [doc.width * 0.6, doc.width * 0.15, doc.width * 0.25]

    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(
        TableStyle([
            # Header styling
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            # Cell styling
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),  # Left align item descriptions
            ('ALIGN', (1, 1), (1, -1), 'CENTER'),  # Center align quantities
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),  # Right align monetary values
            # Padding
            ('TOPPADDING', (0, 0), (-1, -1), 0.3 * cm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.3 * cm),
            ('LEFTPADDING', (0, 0), (-1, -1), 0.2 * cm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0.2 * cm),
        ])
    )
    elements.append(items_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ── Totals ──────────────────────────────────────
    rate = data.get('tax_rate', 0)
    tax_pct = f'{rate * 100:.0f}%' if rate else '0%'

    totals_data = []

    # Promotion first (matching reference format)
    promotion = data.get('promotion', '')
    if promotion:
        totals_data.append([
            '',
            Paragraph('Promotion:', styles['TotalsLabel']),
            Paragraph(fmt(-promotion), styles['AmountCell']),
        ])

    # Refund (subtracted from gross; matches Amazon's order-detail layout)
    refund = data.get('refund', '')
    if refund:
        totals_data.append([
            '',
            Paragraph('Refund:', styles['TotalsLabel']),
            Paragraph(fmt(-refund), styles['AmountCell']),
        ])

    # Shipping before subtotal (matching reference format)
    shipping = data.get('shipping_total', '')
    if shipping:
        totals_data.append([
            '',
            Paragraph('Shipping Total:', styles['TotalsLabel']),
            Paragraph(fmt(shipping), styles['AmountCell']),
        ])

    # Subtotal and Tax
    totals_data.extend([
        [
            '',
            Paragraph('Subtotal:', styles['TotalsLabel']),
            Paragraph(fmt(data.get('subtotal', 0)), styles['AmountCell']),
        ],
        [
            '',
            Paragraph(f'Tax ({tax_pct}):', styles['TotalsLabel']),
            Paragraph(fmt(data.get('tax', 0)), styles['AmountCell']),
        ],
    ])

    # Total and Amount Paid
    totals_data.extend([
        [
            '',
            Paragraph('Total:', styles['TotalsLabel']),
            Paragraph(fmt(data.get('total', 0)), styles['AmountCell']),
        ],
        [
            '',
            Paragraph('Amount Paid:', styles['TotalsLabel']),
            Paragraph(fmt(data.get('amount_paid', 0)), styles['AmountCell']),
        ],
    ])

    totals_table = Table(
        totals_data,
        colWidths=[doc.width * 0.6, doc.width * 0.2, doc.width * 0.2],
    )
    totals_table.setStyle(
        TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0.2 * cm),
            ('TOPPADDING', (0, 0), (-1, -1), 0.1 * cm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.1 * cm),
            (
                'LINEABOVE',
                (1, -1),
                (-1, -1),
                1,
                colors.black,
            ),  # Line above final row
        ])
    )
    elements.append(totals_table)
    elements.append(Spacer(1, 1 * cm))

    # ── Notes section ───────────────────────────────
    seller_vat = data.get('seller_vat', '')
    seller_rfc = data.get('seller_rfc', '')

    elements.append(Paragraph('<b>Notes:</b>', styles['SectionTitle']))
    if seller_name:
        elements.append(
            Paragraph(
                f'Seller Entity: {ensure_string(seller_name)}', styles['Notes']
            )
        )
    if seller_vat:
        elements.append(
            Paragraph(
                f'Seller VAT: {ensure_string(seller_vat)}', styles['Notes']
            )
        )
    if seller_rfc:
        elements.append(
            Paragraph(
                f'Seller RFC: {ensure_string(seller_rfc)}', styles['Notes']
            )
        )

    doc.build(elements)
    return str(out.resolve())


# ── CLI entry point ─────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description='Generate invoice PDF from JSON stdin',
    )
    parser.add_argument(
        '--output',
        '-o',
        required=True,
        help='Output PDF file path',
    )
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print('Error: no JSON input on stdin', file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'Error: invalid JSON: {e}', file=sys.stderr)
        sys.exit(1)

    # Validate required field
    if not data.get('country'):
        print(
            'Error: "country" field is required (2-letter ISO code, e.g. "US")',
            file=sys.stderr,
        )
        sys.exit(1)

    # Compute all financials (parse currency strings, calc tax, etc.)
    data = compute_financials(data)

    output_path = build_pdf(data, args.output)
    print(output_path)


if __name__ == '__main__':
    main()
