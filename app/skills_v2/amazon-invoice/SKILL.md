---
name: amazon-invoice
description: Generates tax invoice PDFs for Amazon orders by reading the order detail page in Seller Central and rendering it with a bundled ReportLab script. Use when the user asks for an invoice for one or more Amazon order IDs.
requires: [amazon-shared]
---

# Amazon Invoice Generator

Load the `amazon-shared` skill first with the Skill tool — it has the
marketplace host map, the Ziniao sign-in challenge loop and the
navigation rules.

For each requested order: read the order detail page, pass the raw
values to `generate_invoice.py`, and get one PDF per order in the task
directory.

## Done when

- A non-empty `invoice_<order_id>.pdf` exists in the task directory for
  every requested order (or the order is reported as not found).
- Each PDF has the invoice number, seller and buyer blocks, a line-item
  table, and subtotal / tax / total that match the source order — not
  placeholder or zeroed values.

## Setup

- `reportlab` is pre-installed in the workspace venv; just run `python`.
  On an import error: `uv pip install -r .claude/skills/amazon-invoice/requirements.txt`.
- Stay in the task directory and use relative paths — the PDFs are task
  deliverables and belong there. If the user wants copies kept across
  runs, put them under `store-data/<slug>/invoices/<YYYY-MM>/`.

## Seller info (optional)

Look for the seller's legal name and VAT/TRN/RFC numbers in this order:

1. `stores/<slug>/seller-info.md` or `notes.md`.
2. The order detail page, if it shows them.
3. Ask the user once through `AskUserQuestion`. If they answer, save it
   to `stores/<slug>/seller-info.md` with `vibe_seller_write_workspace_file`
   (the built-in Write tool can't write through the `stores/` symlink).

If none of these has it, proceed without — the script omits the seller
block. Missing seller details never block an invoice.

## Country

The JSON needs a `country` code (2-letter; `UK` or `GB` both work); the
script uses it to pick the tax rate and whether page prices include tax.
Take it from `task_country` or the store's `platform_countries`, and make
sure Seller Central is on that marketplace before reading the order (UK
and the EU marketplaces share `sellercentral-europe.amazon.com`; switch
marketplace as `amazon-shared` describes). The per-country rules are in
[references/tax-rates.md](references/tax-rates.md).

## Workflow

### 1. Open the order

Navigate by direct URL: `<seller-central-base>/orders-v3/order/<orderId>`
(base from `knowledge/common/amazon-sites.md`). If that doesn't resolve,
fall back to Manage Orders (`/orders-v3`) and search by order ID.

### 2. Extract the order data

Read the values from the DOM and pass **raw strings** as shown on the
page (e.g. `"USD 1,234.56"`) — the script parses them.

```json
{
  "country": "US",
  "invoice_number": "<Order ID>",
  "date": "<order date, YYYY-MM-DD>",
  "bill_to": {
    "name": "<buyer name>",
    "entity": "<business name if present>",
    "vat": "<buyer VAT number if shown>",
    "rfc": "<buyer RFC if shown (Mexico)>",
    "trn": "<buyer TRN if shown, where applicable>",
    "address": "<full billing address>"
  },
  "ship_to": "<full shipping address>",
  "items": [
    {
      "description": "<product title>",
      "quantity": 1,
      "amount": "<total amount for this line item, e.g. 'USD 299.00'>"
    }
  ],
  "subtotal": "<if shown on page, else omit>",
  "tax": "<if shown on page, else omit>",
  "shipping_total": "<if shown, else omit>",
  "promotion": "<if shown, else omit>",
  "refund": "<refund amount shown on the order page as an absolute value (e.g. a row labeled 'Refund: -USD 12.00' becomes '12.00'); omit if none>",
  "total": "<total as shown on page>",
  "amount_paid": "<if shown, else omit>",
  "currency": "USD",
  "seller_entity": "<from store knowledge, if available>",
  "seller_vat": "<from store knowledge, if available>",
  "seller_rfc": "<from store knowledge, if available for Mexico>",
  "store": "<store name>"
}
```

Required: `country`, `invoice_number`, and `items` (each with at least
`description` and `amount`). Everything else is optional. The PDF has a
3-column item table (Item, Quantity, Amount), so `amount` is the
**line total** (quantity × unit price) as shown on the page.

### 3. Let the script do the arithmetic

`generate_invoice.py` parses currency strings, looks up the country's tax
rule, and:

- uses the page's explicit subtotal + tax + total when they are all
  present **and there is no refund**;
- with a refund, recomputes from components
  (`items + shipping − promotion − refund`), because Amazon shows the
  subtotal before the refund and the total after it, so the page values
  disagree;
- otherwise derives tax from the item totals using the country's rate
  (inclusive: `tax = gross × rate / (1 + rate)`; exclusive:
  `tax = subtotal × rate`).

Don't compute tax yourself; pass what you see.

### 4. Generate the PDF

```bash
echo '<json_string>' | python .claude/skills/amazon-invoice/generate_invoice.py \
  --output ./invoice_<order_id>.pdf
```

The script prints the output path; it exits with an error if `country`
is missing.

### 5. Report

Report each generated file path; for a batch, summarise all invoices and
list any order that was skipped and why.

## Batch mode

For multiple order IDs (comma-, space- or line-separated), repeat steps
1–4 per order, one PDF each. An order ID that Seller Central can't find
is skipped and reported, not fatal to the batch.
