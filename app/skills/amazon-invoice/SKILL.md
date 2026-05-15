---
name: Amazon Invoice Generator
description: Generate tax invoices from Amazon Seller Central order data using browser extraction and PDF generation
requires: [amazon-shared]
---

# Amazon Invoice Generator

> **PREREQUISITE:** Read `../amazon-shared/SKILL.md` for marketplace
> TLD map, sign-in / Ziniao / OTP handling, and the hamburger-menu
> navigation pattern (used to reach Tax Document Library before
> running this generator).

Generate professional tax invoices for Amazon orders by extracting data from Seller Central and producing PDF invoices via ReportLab.

## Prerequisites

1. **Python deps are pre-installed** — the shared workspace venv already has `reportlab`. Just use `python` (it's on your PATH). If you get an import error, run: `uv pip install -r .claude/skills/amazon-invoice/requirements.txt`

2. **Resolve seller info** (company name, VAT/RFC numbers) — optional, try in order:
   1. **Store knowledge** — check the store profile directory (shown in your system context, e.g. `stores/<store-slug>/`) for `seller-info.md` or `notes.md` containing seller entity name and VAT numbers.
   2. **Order detail page** — Amazon Seller Central may show your seller/business info on the order page.
   3. **Ask the user** — if neither source has the info, ask once. If the user provides it, **save it** to the store directory's `seller-info.md` (e.g. `stores/<store-slug>/seller-info.md`) so future tasks can find it.
   4. **Proceed without** — the invoice script handles missing seller fields gracefully (omits the seller section). Do not block invoice generation over missing seller details.

**IMPORTANT**: Stay in your current working directory. Do NOT `cd` into the skill directory or any global path. Use relative paths for scripts and output.

## Determining the Country

You **always know the country** before generating an invoice. Get it from:

1. **Store metadata** — the `platform_countries` field in your store context tells you which countries this store operates in (e.g., `amazon: SA, AE`). If the task targets a specific country, use `task_country`.
2. **Current Seller Central session** — you know which country/marketplace you are browsing because you selected it (Seller Central lets you toggle between countries within the same session).

The `country` field (2-letter ISO code) is **required** in the JSON you pass to the script. The script uses it to look up the correct tax rate and tax rules.

### Supported Countries & Default Tax Rates

The script has built-in tax rules for all Amazon marketplace countries:

| Country | Code | Tax | Rate | Prices on Order Page |
|---------|------|-----|------|---------------------|
| Saudi Arabia | SA | VAT | 15% | Include tax |
| UAE | AE | VAT | 5% | Include tax |
| Egypt | EG | VAT | 14% | Include tax |
| South Africa | ZA | VAT | 15% | Include tax |
| Turkey | TR | KDV | 20% | Include tax |
| UK | UK/GB | VAT | 20% | Include tax |
| Germany | DE | MwSt | 19% | Include tax |
| France | FR | TVA | 20% | Include tax |
| Italy | IT | IVA | 22% | Include tax |
| Spain | ES | IVA | 21% | Include tax |
| Netherlands | NL | BTW | 21% | Include tax |
| Poland | PL | VAT | 23% | Include tax |
| Sweden | SE | Moms | 25% | Include tax |
| Belgium | BE | BTW | 21% | Include tax |
| Ireland | IE | VAT | 23% | Include tax |
| Japan | JP | CT | 10% | Include tax |
| India | IN | GST | 18% | Include tax |
| Singapore | SG | GST | 9% | Include tax |
| Australia | AU | GST | 10% | Include tax |
| US | US | — | 0% | No tax |
| Canada | CA | GST | 5% | Exclude tax |
| Mexico | MX | IVA | 16% | Exclude tax |
| Brazil | BR | ICMS | 17% | Include tax |

**"Include tax"** means the prices shown on the order detail page already contain tax. The script back-calculates: `tax = gross × rate / (1 + rate)`.

**"Exclude tax"** means prices are pre-tax. The script adds tax on top: `tax = subtotal × rate`.

## Workflow

For each order ID provided:

### Step 1: Navigate to Order Detail

Go to Seller Central → Orders → Manage Orders → search by Order ID → click into the order detail page.

Make sure you are on the correct country/marketplace within Seller Central before extracting data.

### Step 2: Extract Order Data

From the order detail page, extract the data into this JSON structure. Pass **raw strings** exactly as shown on the page (e.g., `"AED 1,234.56"`) — the script parses them automatically.

```json
{
  "country": "AE",
  "invoice_number": "<Order ID>",
  "date": "<order date, YYYY-MM-DD>",
  "bill_to": {
    "name": "<buyer name>",
    "entity": "<business name if present>",
    "vat": "<buyer VAT number if shown>",
    "rfc": "<buyer RFC if shown (Mexico)>",
    "trn": "<buyer TRN if shown (UAE/SA)>",
    "address": "<full billing address>"
  },
  "ship_to": "<full shipping address>",
  "items": [
    {
      "description": "<product title>",
      "quantity": 1,
      "amount": "<total amount for this line item, e.g. 'AED 299.00'>"
    }
  ],
  "subtotal": "<if shown on page, else omit>",
  "tax": "<if shown on page, else omit>",
  "shipping_total": "<if shown, else omit>",
  "promotion": "<if shown, else omit>",
  "refund": "<refund amount shown on the order page as an absolute value (e.g. a row labeled 'Refund: -AED 12.00' becomes '12.00'); omit if none>",
  "total": "<total as shown on page>",
  "amount_paid": "<if shown, else omit>",
  "currency": "AED",
  "seller_entity": "<from store knowledge, if available>",
  "seller_vat": "<from store knowledge, if available>",
  "seller_rfc": "<from store knowledge, if available for Mexico>",
  "store": "<store name>"
}
```

**Required fields**: `country`, `invoice_number`, `items` (with at least `description` and `amount` per item).

All other fields are optional — the script derives what's missing.

**Note on items**: The PDF shows a simplified 3-column table (Item, Quantity, Amount). The `amount` field should be the **total line item amount** (quantity × unit price) as shown on the order page.

### Step 3: Let the Script Handle Calculations

**You do NOT need to calculate tax yourself.** The `generate_invoice.py` script:

1. **Parses currency strings** — `"AED 1,234.56"` → `1234.56`
2. **Looks up tax rules by country code** — the `country` field you provide
3. **Prioritizes page values** — if the order page shows explicit subtotal + tax + total **and no refund is present**, the script uses those instead of calculating
4. **Handles refunds** — if a refund is provided, the script always recomputes from components (`items + shipping − promotion − refund`) because Amazon's order page shows subtotal **before** refund and total **after** refund, so those page values are mutually inconsistent when a refund exists. The derived gross matches the Item total line the customer actually paid.
5. **Falls back to country rules** — if page values are incomplete, calculates tax from item totals using the country's rate and inclusive/exclusive model

Just extract what you see on the page and pass it through. For each item, provide the **total line amount** (as shown on the order page) rather than unit price.

### Step 4: Add Seller Info (if available)

If seller info was resolved in the Prerequisites step, populate:
- `seller_entity`: company legal name
- `seller_vat`: VAT number for the relevant country
- `seller_rfc`: RFC for Mexico

If no seller info was found, omit these fields — the script handles missing seller data gracefully.

### Step 5: Generate PDF

```bash
echo '<json_string>' | python .claude/skills/amazon-invoice/generate_invoice.py \
  --output ./invoice_{order_id}.pdf
```

The script outputs the file path to stdout. Output goes to the current working directory (the task workspace).

### Step 6: Save Discovered Information

If you found seller entity details (company name, VAT/TRN numbers) during this task that are NOT already saved in the store's knowledge directory:
- Save them to `stores/<store-slug>/seller-info.md` using the Write tool
- This persists across tasks so you won't need to look it up again

### Step 7: Report Result

After generating, report the file path to the user. If multiple orders were requested, summarize all generated invoices.

## Batch Mode

If the user provides multiple order IDs (comma-separated, space-separated, or one per line), loop steps 1-6 for each order ID. Generate separate PDFs for each order.

## Error Handling

- If an order ID is not found in Seller Central, skip it and report the error
- If Seller Central requires login, wait for auto-fill (Ziniao) or ask the user
- If seller info is missing, skip it — the script handles missing fields gracefully
- The script exits with an error if `country` is missing — always provide it
