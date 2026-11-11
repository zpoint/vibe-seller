# Business Reports, Tax Document Library, listing reports

## Contents

- [Business Reports](#business-reports)
- [Tax Document Library](#tax-document-library)
- [Listing reports (Custom Reports)](#listing-reports-custom-reports)

## Business Reports

**URL:** `https://sellercentral.amazon.{tld}/business-reports` (lands on
`#/dashboard`, the Sales Dashboard).

The left sidebar groups **Dashboards** (Sales Dashboard), **By Date** and
**By ASIN**. Report rows are anchors whose `#/report?id=…` hrefs are
language-stable — match by href, not the localized label:

| Report | href fragment |
|---|---|
| Sales and Traffic | `#/report?id=102:SalesTrafficTimeSeries` |
| Detail Page Sales and Traffic | `#/report?id=102:DetailSalesTrafficByTime` |
| Seller Performance | `#/report?id=102:SellerPerformanceForMerchants` |
| Detail Page Sales and Traffic By Parent Item | `#/report?id=102:DetailSalesTrafficByParentItem` |
| Detail Page Sales and Traffic By Child Item | `#/report?id=102:DetailSalesTrafficByChildItem` |

Data is pre-loaded; the single **Download** button (a `kat-button` in a
`kat-table-cell`) exports the current view as CSV instantly. Use the
download snippet from SKILL.md, matching on the button's label rather
than a date row.

## Tax Document Library

**URL:** `https://sellercentral.amazon.{tld}/tax/seller-fee-invoices`

- One tab, **Seller Fee Invoices**, showing the most recent 1,000 invoices
  grouped by month, newest first. **Load More** at the bottom fetches
  older ones.
- Some rows are marked **CSV**: a per-invoice breakdown of the individual
  transactions (Transaction ID, Order ID, Fee Type, Total Fees). They
  download through their `View` button like the PDFs.

Columns:

```
Invoice Type | Invoice File Type | Invoice Number | Payer Name | Payer Registration |
Supplier Name | Supplier Registration | Marketplace | Start Date | End Date | Action
```

| Invoice Type | Description |
|-------------|-------------|
| Fulfillment by Amazon Tax Invoice | FBA service fees invoice |
| Merchant VAT Credit Note | VAT credit adjustment |
| Merchant VAT Invoice | VAT invoice for seller fees |
| Product Ads VAT Invoice | Advertising VAT invoice |

Invoice number format: `{CC}-{ENTITY}-INV-{YEAR}-{SEQ}` (invoices) or
`{CC}-{ENTITY}-CN-{YEAR}-{SEQ}` (credit notes) — `{CC}` country code,
`{ENTITY}` Amazon entity code, `{SEQ}` sequence.

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/tax/seller-fee-invoices")
wait_for_load()
print(page_info())   # View buttons: id=view_invoice_button-announce
# Each View button has value=urn:alx:ver:{UUID}; clicking it form-POSTs to
# /tax/seller-fee-invoices and downloads the PDF. Match the row by its
# Start/End Date text (e.g. "Mar 01") before clicking its View button.
js("document.querySelector('#view_invoice_button-announce').click()")   # first row

# Older invoices: scroll to the bottom and click Load More.
js("window.scrollTo(0, document.body.scrollHeight)")
js("[...document.querySelectorAll('button, a')].find(e => e.textContent.trim() === 'Load More').click()")
PY
```

Each PDF is a formal tax invoice (number, dates, seller/supplier VAT
details, line items, VAT, totals). The form POST occasionally opens the PDF
in a new tab instead of downloading.

## Listing reports (Custom Reports)

**URL:** `https://sellercentral.amazon.{tld}/listing/reports`

Report types: **Active Listings Report**, **All Listings Report**. Table:
Report Type | Batch ID (timestamp) | Report Status | Action (Download).

1. Select the report type. The selector is a `kat-dropdown`: click it open
   with `click_at_xy`, read the `kat-option` boxes from the dropdown's
   `shadowRoot`, and `click_at_xy` the one you want (same method as the
   month dropdown in [payments-repository.md](payments-repository.md)).
   `selectOption(...)` and plain `<select>`/`[data-testid]` selectors don't
   change it.
2. Click **Request Report** (a `kat-button`; host → inner-button walk).
3. Poll (typically 1-5 min) until Report Status is ready, then download the
   new row with the download snippet, matching its request-time substring.

The file downloads with a fixed date-based name (e.g.
`All+Listings+Report_MM-DD-YYYY.txt`) that is identical across
marketplaces, so move it to a country-suffixed name
(`All_Listings_<CC>_<YYYY-MM-DD>.txt`) before requesting the next country.
The All Listings TSV is account-level on a unified account (the same rows
from every marketplace subdomain), so verify content rather than assuming
each country's file differs.
