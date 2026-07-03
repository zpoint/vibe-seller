---
name: amazon-reports
description: "Amazon platform only. MUST load BEFORE taking any action when the task involves Amazon Seller Central report pages (Tax Document Library, Business Reports, Fulfillment, Payments CSV, Advertising Reports, etc.). Contains URLs, hover navigation, CSV structures, and wait times."
requires: [amazon-shared]
---

# Amazon Seller Central — Reports Export Guide

> **PREREQUISITE:** Read `../amazon-shared/SKILL.md` for marketplace
> TLD map, the hamburger-menu hover pattern (referenced below),
> sign-in / Ziniao / OTP handling, and the capture rule.

This skill documents how to navigate to and export every report type
available in Amazon Seller Central and Amazon Advertising, including
CSV/PDF structures for building analysis scripts.

## Critical: Hamburger Menu Navigation

Many reports are accessed via the **hamburger menu** (top-left corner).
This menu uses a **hover-to-reveal** pattern — you must dispatch a
`mouseover`, not a `click`, on a category to reveal its submenu items.
(See `../amazon-shared/SKILL.md § 4` for the canonical version.)

```bash
browser-use <<'PY'
# 1. Open the hamburger menu (opens the full sidebar overlay).
#    The button is a role=button inside the navigation-hamburger-menu
#    shadow DOM — reach it through its shadow host.
js("document.querySelector('navigation-hamburger-menu').shadowRoot.querySelector('[role=button]').click()")
wait_for_load()

# 2. Reveal the submenu by dispatching mouseover on the "Reports"
#    category (aria-expanded flips false -> true; flyout appears).
js("""
var cat = [...document.querySelectorAll('[aria-expanded=false]')]
  .find(e => e.textContent.trim() === 'Reports');
cat.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
""")
print(page_info())

# 3. Click the submenu item once it renders (e.g. "Tax Document Library").
js("""
[...document.querySelectorAll('a, [role=button]')]
  .find(e => e.textContent.trim() === 'Tax Document Library').click();
""")
PY
```

**Do NOT click the category name** — clicking navigates away instead of
revealing the submenu. Always dispatch `mouseover` first, then
`print(page_info())` to see the submenu items, then click the target.
If a stable selector isn't obvious, fall back to `capture_screenshot()`
then `click_at_xy(x, y)`.

## General Rule: Download Existing Reports First

**Before generating any report, always check if a matching report already
exists.** All report pages maintain a download history table showing
previously generated reports with their date ranges and Download buttons.

This applies to ALL report types:

- **Fulfillment reports** (`/reportcentral/`): Click the report in the
  left sidebar → "Download" tab → history table with columns:
  Report Type | Date Range Covered | Date Requested | File Format |
  Report Status (Download button). If a row matches the requested date
  range, click Download directly.

- **Advertising reports** (`advertising.amazon.{tld}/reports`): The list
  page shows existing reports with date ranges in the right scroll panel.
  Click the report name to reach the detail page → history table with
  Download link. If a completed run matches the date range, download it.

- **Payments reports** (`/payments/reports-repository`): Check the report
  list for existing reports covering the target period.

Only request/generate a new report if no existing one covers the needed
date range.

## Report Types Overview

### A. Seller Central Reports (via hamburger menu → Reports)

| # | Report | Direct URL Path | Export Type | Wait Time |
|---|--------|----------------|-------------|-----------|
| 1 | Business Reports | `/business-reports` | CSV download | Instant |
| 2 | Custom Analytics | (new feature) | Varies | Varies |
| 3 | Fulfillment | `/reportcentral/WelcomePage` | Request → Download | 1-5 min |
| 4 | Advertising Reports | Redirects to Ad console | CSV download | Hours/Days |
| 5 | Return Reports | `/reportcentral/WelcomePage` | Request → Download | 1-5 min |
| 6 | Custom Reports | `/listing/reports` | Request → Download | 1-5 min |
| 7 | Inventory Reports | `/inventory-reports` | Request → Download | 1-5 min |
| 8 | Tax Document Library | `/tax/seller-fee-invoices` | View (PDF) | Instant |
| 9 | Manage Taxes | `/tax/tax-settings` | Settings page | N/A |
| 10 | Selling Economics & Fees | `/selling-economics-and-fees` | Dashboard | Varies |

### B. Payments Reports

| Report | Direct URL Path | Export Type | Wait Time |
|--------|----------------|-------------|-----------|
| Reports Repository | `/payments/reports-repository` | Request → CSV | 1-3 min |

### C. Advertising Reports (separate console)

| Report | URL Pattern | Export Type | Wait Time |
|--------|-------------|-------------|-----------|
| Sponsored Ads Reports | `advertising.amazon.{tld}/reports` | CSV download | 5 min – 24 hours |

---

## 1. Business Reports

**Navigation**: hamburger menu → hover Reports → Business Reports
**Direct URL**: `https://sellercentral.amazon.{tld}/business-reports`
**Lands on**: `#/dashboard` (Sales Dashboard)

### Sub-sections (left sidebar)

- **Dashboards** → Sales Dashboard
- **By Date** → Sales and Traffic, Detail Page Sales and Traffic, Seller Performance
- **By ASIN** → Detail Page Sales and Traffic, By Parent Item, By Child Item

### Export Flow

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/business-reports")
wait_for_load()
print(page_info())   # locate the "Download" button (inside a kat-table-cell shadow DOM)
# Trigger the CSV download — immediate. Reach the button through its
# shadow host; if no stable selector, screenshot then click_at_xy.
js("document.querySelector('kat-table-cell button, [data-testid=download]').click()")
PY
```

### Wait Time

**Instant** — page data is pre-loaded, Download button exports current view.

---

## 2. Fulfillment Reports

**Navigation**: hamburger menu → hover Reports → Fulfillment
**Direct URL**: `https://sellercentral.amazon.{tld}/reportcentral/WelcomePage`

### Sub-reports (left sidebar, elements have `id=report-nav-link-url`)

| Category | Reports |
|----------|---------|
| Inventory | Inventory ledger report, Stranded Inventory, Reserved Inventory, Amazon Fulfilled Inventory, Manage FBA Inventory |
| Shipping | Inbound Performance, Outlet Deals |
| Orders | Amazon Fulfilled Shipments, Amazon Fulfilled Shipments – Tax Invoicing, All Orders |
| Fees | Fee Preview, Monthly Storage Fees, Aged Inventory Surcharge report |
| Returns | Reimbursements, FBA customer returns, Replacements, Removal Order Detail, Removal Shipment Detail |
| Other | EPR Category Reports |

### Export Flow

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/reportcentral/WelcomePage")
wait_for_load()
print(page_info())   # locate the report link in the sidebar (id=report-nav-link-url)
js("document.querySelector('#report-nav-link-url').click()")   # open specific report
wait_for_load()
print(page_info())   # locate "Request Report" / date range / "Generate Report"
js("document.querySelector('[data-testid=request-report], #request-report').click()")
PY
# Wait 1-5 minutes. Reload the page to check status.
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/reportcentral/WelcomePage")
wait_for_load()
print(page_info())   # locate the "Download" button when status shows ready
js("document.querySelector('[data-testid=download], .download-btn').click()")
PY
```

### Wait Time

**1-5 minutes** for most reports. Refresh the page to check status.

### Monthly Storage Fees — publication latency

**Observed behavior:** requesting Monthly Storage Fees for the previous
month early in the new month returns `No Data Available` on the
detail page even though the request itself is accepted. The same
month's range re-requested about a week later returns a Download
button within ~30s. Older months (M-2, M-3) request successfully
right away.

So Storage Fees for month M is **not** available on day 7 of M+1
but **is** available by day 13-14 of M+1. The exact day Amazon
publishes is not documented on the page; the page header for this
report says only "Report data 24 hours old" (about freshness of
already-published values, not about when they appear).

**How to handle a `No Data Available` Storage Fees response when
the requested month is the previous calendar month:**

1. Don't keep retrying within the same task — Amazon won't surface
   the data sooner because of repeat requests.
2. Produce every other expected file (Transaction CSV, FBA
   Customer Returns CSV, Sponsored Products advertised-product
   XLSX) — those publish on a different cadence and ARE available
   today.
3. A scheduled re-run after roughly day 13-14 of the following
   month will capture the storage CSV.

**Task-status decision for this scenario — MANDATORY exception
to the system-prompt's "MUST call vibe_seller_set_task_error when
primary objective could not be fully completed" rule:**

If the ONLY missing deliverable is `storage.csv` (any country)
and the cause is this Amazon publication-latency window
(today is in month M+1, Amazon's detail page says
`No Data Available`), then:

- Call `vibe_seller_set_task_result(<full report including a
  "Storage CSV pending Amazon publication" note>)` with the
  partial summary.
- **DO NOT call `vibe_seller_set_task_error`.** The task lands
  COMPLETED — the next scheduled run picks up storage when
  Amazon publishes it. Calling `set_task_error` here causes the
  task to land FAILED for what is a known, expected,
  upstream-paced data state, and that triggers false "task
  failed" alerts downstream.

This is a NARROW exception. It applies only when:
(a) every other deliverable for the task ran successfully, AND
(b) the only gap is `storage.csv`, AND
(c) the agent saw `No Data Available` on the report-history
    detail page for that exact month.

If ANY other deliverable also failed, fall back to the normal
"call BOTH set_task_result and set_task_error" pattern from the
system prompt — the storage-latency exception does not absorb
unrelated failures.

If `No Data Available` shows for an **older** month (≥ ~30 days
old), that's unexpected — investigate normally; do not wave away,
and do call `set_task_error`.

### FBA Customer Returns — Exact dates flow (MANDATORY for month ranges)

**Direct URL**: `https://sellercentral.amazon.{tld}/reportcentral/CUSTOMER_RETURNS/1`

The date control is NOT a From/To pair like other reports. It is a
**preset dropdown** (initial title `last day (yesterday)`) whose
options are last 1/3/7/14/30/90/180 days — **plus an `Exact dates`
option (`kat-option value=-1`)** at the bottom. Presets are anchored
to *today*, so "last 30 days" does NOT equal a calendar month: on
June 3 it covers May 4 → June 3, silently dropping May 1–3 and
mixing in June rows. **Never use a preset when the task asks for a
calendar month.**

Correct flow:

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/reportcentral/CUSTOMER_RETURNS/1")
wait_for_load()
print(page_info())   # locate preset dropdown (title="last day (yesterday)")
js("document.querySelector('[title=\"last day (yesterday)\"]').click()")
print(page_info())   # options list; "Exact dates" is value=-1
js("document.querySelector('[value=\"-1\"]').click()")
print(page_info())   # TWO plain inputs appear:
                     #   aria-label="Start date", aria-label="End date"
                     #   placeholder DD/MM/YYYY
fill_input("input[aria-label='Start date']", "01/01/2026")
fill_input("input[aria-label='End date']", "31/01/2026")
js("document.querySelector('[data-testid=request-csv], button.request-csv').click()")  # "Request .csv Download"
PY
# ~30s; first row of the history table shows the range + Download
browser-use <<'PY'
print(page_info())
js("document.querySelector('[data-testid=download], .download-btn').click()")
PY
```

**Anti-pattern — do NOT do this:** after clicking `Exact dates`,
do not try to set the `kat-date-picker` values via `js()` / shadow-DOM
JS. Setting `.value` on kat-date-picker silently fails (one observed
run burned ~100 steps this way and fell back to a wrong preset). After
clicking the option, just re-run `print(page_info())` — the Start/End
fields surface as ordinary `<input>` elements and
`fill_input("input[aria-label='Start date']", "...")` works directly.
(Use `fill_input` with a selector here, not `type_text` — `type_text`
just types into whatever is focused and can't target a specific field.)

**UTC edge rows are normal:** the date filter is marketplace-local,
but the CSV's `return-date` column is UTC. A row dated the last day
of the *previous* month at ~21:00–23:59 UTC is the 1st of the
requested month in local time — keep it, don't re-request.

The download lands as a numeric report id (e.g. `51901234567.csv`)
in the store downloads dir; rename to `return.csv` when the task
asks for that exact name.

---

## 3. Tax Document Library

**Navigation**: hamburger menu → hover Reports → Tax Document Library
**Direct URL**: `https://sellercentral.amazon.{tld}/tax/seller-fee-invoices`

### Page Structure

- **Tab**: Seller Fee Invoices (default and only tab)
- Displays most recent 1,000 invoices. Click "Load More" at bottom for older.
- **Invoices grouped by month** (newest first)

### Table Columns

```
Invoice Type | Invoice File Type | Invoice Number | Payer Name | Payer Registration |
Supplier Name | Supplier Registration | Marketplace | Start Date | End Date | Action
```

### Invoice Types

| Invoice Type | Description |
|-------------|-------------|
| Fulfillment by Amazon Tax Invoice | FBA service fees invoice |
| Merchant VAT Credit Note | VAT credit adjustment |
| Merchant VAT Invoice | VAT invoice for seller fees |
| Product Ads VAT Invoice | Advertising VAT invoice |

### Invoice Number Format

`{CC}-{ENTITY}-INV-{YEAR}-{SEQ}` (invoices) or `{CC}-{ENTITY}-CN-{YEAR}-{SEQ}` (credit notes)

Where `{CC}` = country code (e.g. `US`), `{ENTITY}` = Amazon entity code, `{SEQ}` = sequence number.

### Export Flow

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/tax/seller-fee-invoices")
wait_for_load()
print(page_info())   # locate View buttons (id=view_invoice_button-announce)

# Each "View" button has a value=urn:alx:ver:{UUID} attribute.
# Clicking triggers a form POST that downloads a PDF. To pick a
# specific invoice, match its row; the first View button is:
js("document.querySelector('#view_invoice_button-announce').click()")

# For a specific month, scan page_info() output for date strings
# e.g. "Mar 01" or "Mar 31" for March invoices.

# For older invoices beyond the most recent 1,000, scroll to the
# bottom and click "Load More":
js("window.scrollTo(0, document.body.scrollHeight)")
print(page_info())   # locate "Load More"
js("[...document.querySelectorAll('button, a')].find(e => e.textContent.trim() === 'Load More').click()")
PY
```

### Wait Time

**Instant** — invoices are already generated, View triggers immediate PDF download.

### PDF Content (for analysis)

Each PDF is a formal tax invoice containing: invoice number, dates, seller
and supplier VAT details, line items with amounts, VAT calculations, totals.
These are individual documents — not bulk-exportable as CSV.

---

## 4. Custom Reports (Listing Reports)

**Navigation**: hamburger menu → hover Reports → Custom Reports
**Direct URL**: `https://sellercentral.amazon.{tld}/listing/reports`

### Report Types (dropdown values)

- Active Listings Report
- All Listings Report

### Table Columns

```
Report Type | Batch ID (timestamp) | Report Status | Action (Download)
```

### Export Flow

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/listing/reports")
wait_for_load()
print(page_info())   # locate "Select Report Type" dropdown and "Request Report"

# Select type
js("document.querySelector('[data-testid=report-type-select], select').click()")
print(page_info())   # locate options
js("[...document.querySelectorAll('kat-option, option')].find(o => o.textContent.trim() === 'All Listings Report').click()")

# Request
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Request Report').click()")
PY
# Wait 1-5 minutes, then check for Download
browser-use <<'PY'
print(page_info())   # table shows the new row with status
js("document.querySelector('[data-testid=download], .download-btn').click()")
PY
```

### Wait Time

**1-5 minutes**. Refresh page to check if Report Status = ready.

---

## 5. Payments Reports Repository

**Navigation**: hamburger menu → hover Payments → Payments, then tab "Reports Repository".
Or navigate directly.
**Direct URL**: `https://sellercentral.amazon.{tld}/payments/reports-repository`

### Page Tabs

| Tab | Description |
|-----|-------------|
| Statement View | Settlement period summary: Net Proceeds, Sales, Refunds, Expenses, Balance |
| Transaction View | Individual transactions within a settlement |
| All Statements | Historical settlement statements |
| Disbursements | Bank disbursement details |
| Advertising Invoice History | Ad billing invoices |
| Reports Repository | **Custom date-range CSV reports** (selected by default via direct URL) |

### Report Type Dropdown Values

| Value | Label | Description |
|-------|-------|-------------|
| `SELLER_TRANSACTION_DATE_RANGE` | Transaction | Per-order/per-event transaction details |
| `SELLER_SUMMARY_DATE_RANGE` | Summary | Aggregated summary for the period |

### Export Flow

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/payments/reports-repository")
wait_for_load()
print(page_info())

# 1. Report Type dropdown (id=katal-id-* with title=Transaction)
js("document.querySelector('[title=Transaction]').click()")
print(page_info())   # locate kat-option elements
js("[...document.querySelectorAll('kat-option')].find(o => o.textContent.trim() === 'Transaction').click()")  # Transaction or Summary

# 2. Date Range — set start and end dates
print(page_info())   # locate date range inputs
fill_input("input[aria-label='Start date']", "01/01/2026")
fill_input("input[aria-label='End date']", "03/31/2026")

# 3. Request Report
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Request Report').click()")
PY
# 4. Wait ~1-3 minutes, then scroll down to find the report in the table
browser-use <<'PY'
js("window.scrollTo(0, document.body.scrollHeight)")
print(page_info())   # locate "Download CSV" button (inside kat-table-cell shadow DOM)
# Status must show "Ready" (with check_circle icon)
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Download CSV').click()")
PY
```

### Wait Time

**1-3 minutes** for report generation. The table row shows a status icon:
- Spinner = generating
- Green check (`kat-icon name=check_circle`) + "Ready" = downloadable

### Download Mechanism

The "Download CSV" button triggers an XHR GET to:
```
/payments/reports/api/download-report?reportId={UUID}
```
This returns the CSV file. The browser handles it as a download.

### Transaction Report CSV Structure

The Transaction report has **7 header lines** (definitions) followed by a
**22-column CSV** with `~17,000+` rows per quarter.

```
Header lines (skip first 7 lines when parsing):
  Line 1: "Includes Amazon Marketplace, Fulfillment by Amazon (FBA), and Amazon Webstore transactions"
  Line 2: "All amounts in {currency}, unless specified"  (e.g. AED, USD, EUR)
  Lines 3-7: Column definitions
  Line 8: Column headers
  Line 9+: Data rows
```

#### Columns (22 total)

```csv
"date/time","settlement id","type","order id","sku","description","quantity",
"marketplace","fulfillment","order city","order state","order postal",
"product sales","shipping credits","gift wrap credits","promotional rebates",
"sales tax collected","Marketplace Facilitator Tax","selling fees","fba fees",
"other transaction fees","other","total"
```

#### Column Details

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| date/time | datetime | `15 Mar 2026 3:22:08 PM UTC` | Transaction timestamp |
| settlement id | string | `18472930156` | Settlement period ID |
| type | enum | `Order` | See transaction types below |
| order id | string | `123-1234567-1234567` | Amazon order ID |
| sku | string | `PROD-001` | Your SKU |
| description | string | Product title | Full product name |
| quantity | int | `2` | Units in transaction |
| marketplace | string | `amazon.<tld>` | Marketplace domain |
| fulfillment | string | `Amazon` | `Amazon` (FBA) or `Seller` |
| order city | string | City name (may be in the local language) | Buyer's city |
| order state | string | State/region | Buyer's state |
| order postal | string | Postal code | May be empty |
| product sales | decimal | `89.00` | Product revenue |
| shipping credits | decimal | `5.00` | Shipping collected |
| gift wrap credits | decimal | `0` | Gift wrap revenue |
| promotional rebates | decimal | `-4.45` | Promotions (negative) |
| sales tax collected | decimal | `4.45` | Sales tax (5% VAT) |
| Marketplace Facilitator Tax | decimal | `0` | Amazon-collected tax |
| selling fees | decimal | `-13.35` | Referral fees (negative) |
| fba fees | decimal | `-18.50` | FBA fees (negative) |
| other transaction fees | decimal | `0` | Misc fees |
| other | decimal | `0` | Non-order amounts |
| total | decimal | `62.15` | Net amount |

#### Transaction Types (column "type")

| Type | Count (typical quarter) | Description |
|------|------------------------|-------------|
| `Order` | ~17,000 | Product sale |
| `Refund` | ~400 | Customer refund |
| `Adjustment` | ~120 | Amazon adjustments |
| `Service Fee` | ~28 | Subscription/service charges |
| `Transfer` | ~30 | Bank disbursements |
| `FBA Inventory Fee` | ~6 | Storage/removal fees |

#### Analysis Script Hint

```python
import pandas as pd

df = pd.read_csv(
    'transaction_report.csv', skiprows=7, dtype={'order postal': str}
)
# Filter by SKU
sku_data = df[df['sku'] == 'YOUR-SKU']
# Profit per SKU
profit = df.groupby('sku')['total'].sum().sort_values()
# Monthly revenue
df['month'] = pd.to_datetime(
    df['date/time'], format='%d %b %Y %I:%M:%S %p %Z'
).dt.to_period('M')
monthly = df[df['type'] == 'Order'].groupby('month')['product sales'].sum()
```

---

## 6. Advertising Reports (Amazon Ads Console)

**Navigation**: hamburger menu → hover Reports → Advertising Reports
(redirects to `advertising.amazon.{tld}/reports`)
**Direct URL**: `https://advertising.amazon.{tld}/reports`

### Page Structure

The Ads console is a **separate application** with its own left sidebar:
Campaigns, Recommendations, Brand Stores, Creative tools, Insights & planning,
Measurement & reporting → Sponsored ads reports

### Report Categories and Types (Complete)

#### Sponsored Products (`sp`)

| Value | Label | Description |
|-------|-------|-------------|
| `searchTerms` | Search term | Customer search queries that triggered ads |
| `keywords` | Targeting | Keyword/target performance |
| `adProducts` | Advertised product | Per-ASIN ad performance |
| `campaigns` | Campaign | Campaign-level metrics |
| `budgets` | Budget | Budget utilization |
| `placements` | Placement | Ad placement performance (top of search, etc.) |
| `audience` | Audience | Audience segment performance |
| `purchasedProducts` | Purchased product | Products buyers actually purchased |
| `performanceOverTime` | Performance Over Time | Time-series performance data |
| `searchTermsImpressionRank` | Search Term Impression Share | Share of impressions for search terms |
| `grossAndInvalidTraffic` | Gross and Invalid Traffic | Invalid click detection |

#### Sponsored Brands (`sb`)

| Value | Label |
|-------|-------|
| `keywords` | Keyword |
| `keywordPlacements` | Keyword Placement |
| `campaigns` | Campaign |
| `campaignPlacements` | Campaign placement |
| `searchTerms` | Search term |
| `searchTermsImpressionRank` | Search Term Impression Share |
| `peerBenchmark` | Category benchmark |
| `attributedPurchases` | Attributed Purchases |
| `grossAndInvalidTraffic` | Gross and Invalid Traffic |

#### Sponsored Display (`sd`)

| Value | Label |
|-------|-------|
| `campaigns` | Campaign |
| `keywords` | Targeting |
| `adProducts` | Advertised product |
| `purchasedProducts` | Purchased product |
| `matchedTarget` | Matched target |
| `grossAndInvalidTraffic` | Gross and Invalid Traffic |
| `pricingTransparency` | Pricing transparency |

#### Sponsored TV (`st`)

| Value | Label |
|-------|-------|
| `pricingTransparency` | Pricing transparency |

#### All Amazon campaigns (`all`)

| Value | Label |
|-------|-------|
| `conversionPath` | Conversion path |

### Create Report Dialog

Click "Create report" button (first `<button>` inside `#advertising-reports`):

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/reports")
wait_for_load()
js("document.querySelector('#advertising-reports button').click()")
print(page_info())
PY
```

Dialog fields:

| Field | Element Pattern | Options |
|-------|----------------|---------|
| Report category | `button[id*=report-category-control]` | SP, SB, SD, ST, All |
| Report type | `button[id*=report-type-control]` | Depends on category (see tables above) |
| Country | Auto-set to marketplace | Current marketplace |
| MRC accredited data | `input[id=mrc-check-box]` | Checkbox |
| Currency conversion | `input[id=cov-check-box]` | Add converted columns |
| Time unit | `input[name=time-units]` | `summary` (default) or `day` (Daily). **Default to `summary` unless the user explicitly asks for daily** — Summary returns one row per (campaign, ad group, SKU) with the period totals; Daily returns one row per (date, …, SKU) and the report blows up to ~30× the row count. Downstream profit/cost analysis aggregates by SKU, so Summary is what produces the right monthly Spend / 7-day-attributed-units totals; summing Daily's `7 Day Total Units (#)` rows is **not** equivalent because each row's value uses a 7-day attribution window. |
| Report period | `button[id*=report-period-control]` | Last 30 days, Last month, Custom |
| Report name | `input[id=report-settings-card-report-name-input]` | Free text, auto-generates from category + type |
| Schedule | (below name) | One-time or recurring |

Action buttons:
- `#urc_run_subscription_button` → **Run report**
- `#urc_cancel_subscription_button` → **Cancel**

### Sponsored Products Advertised Product report — DO NOT reuse cached files

The Ziniao download dir (`~/.vibe-seller/downloads/<slug>/`) is
PERSISTENT across tasks — files from previous runs accumulate
there. Observed (iteration 6): an agent saw
`Sponsored_Products_Advertised_product_report_Apr_SA.xlsx`
already in the slug's downloads dir and `cp`'d it directly into
the task workspace, bypassing the entire reports-page Time-unit
verification below. The cached file was a Daily report from an
earlier run, so the new task workspace got a Daily xlsx even
though the skill said Summary.

**Rule:** for the SP Advertised Product report, do NOT `cp` /
reuse a file from `~/.vibe-seller/downloads/<slug>/` based on
filename alone. Either:

1. Always go through `advertising.amazon.{tld}/reports` and
   verify Time unit on the list page (steps below); OR
2. If you must reuse a cached file (e.g., the user explicitly
   asks), open it with `python3 -c "import pandas as pd; df =
   pd.read_excel('<path>'); print(list(df.columns)[:5])"` and
   confirm column 1 is `Start Date` (English) or `开始日期`
   (Chinese). If column 1 is `日期` / `Date` (singular), it's
   Daily — discard and create a new Summary instead.

This rule applies specifically to the SP Advertised Product
report. The Transaction CSV, FBA Customer Returns CSV, and noon
report are agnostic to filename-based cache reuse — their
columns don't shift between Daily/Summary the way the SP report
does.

### Export Flow — Preferred: Download Existing Report

**Always check for an existing report first.** If a completed report
already covers the requested date range, download it directly — no need
to generate a new one.

The reports list page is a **split-panel layout**:
- **Left panel** (fixed): report name `<a>` links
- **Right panel** (horizontally scrollable): run details columns
  (Last run, Report category, Report type, Country, Report period,
  **Time unit**)

Multiple reports can share the same name (e.g., separate one-time runs
of "Sponsored Products Advertised product report"). Identify the correct
one by matching the **Report period** column in the right panel.

**Time-unit gate for reuse.** The list page
exposes a `[col-id=timeUnit]` cell per row containing the literal
"Summary" / "Daily" string (or `摘要` / `每日` on a Chinese UI). Before
clicking through to download an existing report, READ this column on
the same row whose Report period matches your target — and **only
reuse rows where Time unit is Summary**. Daily reports are not a
drop-in replacement for downstream profit aggregation: their per-row
`7 Day Total Units (#)` and `7 Day Total Sales` columns use a 7-day
attribution window, so summing 30 daily rows ≠ Summary's single
monthly total. If the only matching row is Daily, skip reuse and
fall through to "Create New Report" (which defaults to Summary).

```bash
# Filter the run rows to "Summary, target-period match" before
# you commit to a reuse:
browser-use <<'PY'
print(js("""
  var rows = document.querySelectorAll('.ag-center-cols-container [role=row]');
  var matches = [];
  rows.forEach(function(r) {
    var tu = r.querySelector('[col-id=timeUnit]');
    var rp = r.querySelector('[col-id=reportPeriod]');
    var ts = r.querySelector('[col-id=reportCreationTimestamp]');
    if (!tu || !rp) return;
    var unit = tu.textContent.trim();
    var period = rp.textContent.trim();
    var isSummary = unit === 'Summary' || unit === '摘要';
    var isTargetPeriod = period.indexOf('TARGET_RANGE_HERE') !== -1;
    if (isSummary && isTargetPeriod) {
      matches.push({ rowId: r.getAttribute('row-id'),
                     ts: ts ? ts.textContent.trim() : '' });
    }
  });
  return JSON.stringify(matches);
"""))
PY
# If matches is empty → go to Create New Report flow.
# If matches has entries → look up the same row-id in the LEFT
# pinned panel (.ag-pinned-left-cols-container) to get the report
# detail URL, then visit and click Download.
```

**Download links do NOT appear on the list page.** You must click
through to the report detail page.

The list page has a **split-panel layout** — left panel has report name
links, right panel has run details. They are in separate DOM containers,
so you CANNOT reliably match left links to right rows by position.

**Reliable approach: extract report URLs via JS, then visit each one.**

```bash
# 1. Get all report detail URLs (language-agnostic)
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/reports")
wait_for_load()
print(js("""
  var section = document.querySelector('#advertising-reports');
  if (!section) return '[]';
  var anchors = section.querySelectorAll('a[href*="/reports/history/"]');
  var urls = [];
  for (var a of anchors) urls.push(a.href);
  return JSON.stringify(urls);
"""))
PY
# Returns array of detail page URLs (each has a unique UUID)
# Works regardless of language (EN/CN/AR)

# 2. Visit each URL and check the history table
browser-use <<'PY'
js("location.href='<url>'")   # already on the ad console — navigate in place
wait_for_load()
print(page_info())
# Look for: "Completed" + target period + "Download" <a>.
# If this one doesn't match, repeat with the next URL.
# If it matches → click Download:
js("[...document.querySelectorAll('a')].find(a => a.textContent.trim() === 'Download').click()")
PY
```

**Do NOT click right-panel rows** — they show misleading inline text.
**Do NOT guess which left-panel link is correct** — use the JS eval
approach above to get direct URLs.

### Export Flow — Create New Report (only if no existing match)

Only create a new report if the list page has no completed report
matching the requested date range and report type.

```bash
# 1. Open reports page and click Create report
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/reports")
wait_for_load()
js("document.querySelector('#advertising-reports button').click()")
print(page_info())

# 2. Select report type (dropdown renders in #portal as role=listbox).
#    Default is "Search term". Click the type button to open the dropdown:
js("document.querySelector('button[id*=report-type-control]').click()")
print(page_info())                 # look for role=option buttons in #portal
# e.g. select the Advertised Product type. Type values: searchTerms,
# keywords, adProducts, campaigns, budgets, placements, audience,
# purchasedProducts, etc.
js("document.querySelector('button[role=option][value=adProducts]').click()")

# 3. CONFIRM Time unit = summary (the page default; do NOT switch to Daily).
#    For an Advertised Product report consumed by downstream profit
#    aggregation, Summary returns one row per (campaign, ad group, SKU)
#    with the period totals — which is what the consumer expects.
#    Daily blows the file up ~30× and the per-row "7 Day Total Units (#)"
#    uses a 7-day attribution window, so summing daily rows is NOT
#    equivalent to Summary's monthly totals. Verify the radio:
print(js("""
  var r = document.querySelector('input[name="time-units"]:checked');
  return r ? r.value : '(no time-units radio found)';
"""))
# Expected: "summary". If it returns "day" or "daily", click the
# summary option:
#   js("document.querySelector('input[name=\\"time-units\\"][value=summary]').click()")

# 4. Set time period. Click the period button to open a HYBRID picker:
#    - Top section: preset buttons (Today, Yesterday, Last month, etc.)
#    - Bottom section: dual-calendar date picker with clickable day cells
js("document.querySelector('button[id*=report-period-control]').click()")
print(page_info())                 # presets + calendar appear in #portal
# Option A — use a preset:
js("document.querySelector('button[value=LAST_MONTH]').click()")
# Option B — pick custom dates on the calendar:
#   js("document.querySelector('button[aria-label=\\"Sunday March 1 2026\\"]').click()")
#   js("document.querySelector('button[aria-label=\\"Tuesday March 31 2026\\"]').click()")
#   js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Save').click()")

# 5. Run the report
js("document.querySelector('#urc_run_subscription_button').click()")
PY

# 6. WAIT — dialog closes, you land on the report detail page.
#    Poll the detail page for completion (reload each poll):
browser-use <<'PY'
js("location.reload()")
wait_for_load()
print(page_info())   # check Status column in run history table
# Status values in the detail page history table:
#   "Pending"    → Amazon has not started; Action column empty. WAIT.
#   "Processing" → Amazon is generating; Action column empty. WAIT.
#   "Completed"  → Done; Download <a> appears in Action column.
# All non-Completed statuses are normal — just reload and retry.
# Do NOT treat Pending/Processing as errors. Once Completed:
js("[...document.querySelectorAll('a')].find(a => a.textContent.trim() === 'Download').click()")
PY
```

The download link URL pattern:
```
/reports/subscriptions/{report-uuid}/download-report/{run-uuid}
```

### Report Detail Page Actions

Click the "Action" dropdown (`button[id=action-dropdown-trigger]`):
- **History** — view all past runs
- **Report settings** — edit configuration

To re-run: click the "Run report" button on the detail page.

### Gotchas

1. **Use the default browser session** for `advertising.amazon.{tld}` —
   it shares authentication with Seller Central. Do NOT use aux session
   (it requires a separate login).
2. **No status or Download on the list page** — the list page does NOT
   show report status (Completed/Pending/Processing). The list has two
   panels: left panel has report name `<a>` links, right panel has run
   detail columns. Clicking a right-panel row does NOT navigate — it
   may show misleading inline text like "No data available." You MUST
   click the report **name `<a>` link in the left panel** to navigate
   to the detail page where the real status and Download link appear.
3. **Period picker has two modes** — preset buttons at the top and a
   dual-calendar below. For custom date ranges, click start date, then
   end date on the calendar, then click **Save**.
4. **Prefer preset buttons over custom calendar dates.** The calendar
   Save button often fails to persist custom date selections via
   automated clicks. Use presets like `LAST_MONTH` when they match
   the target period.
5. **~90-day calendar lookback (ad console only).** In the advertising
   console's period picker, dates older than ~90 days may appear
   greyed out or unselectable. If the target period is outside
   preset range and calendar dates are greyed out, the report
   cannot be generated — report this to the user.
6. **Dropdown options use `kat-option` elements.** When clicking
   dropdowns (month, year, report type), the options render as
   `kat-option` custom elements. Use `print(page_info())` to see
   them, then click the matching option by text via
   `js("[...document.querySelectorAll('kat-option')].find(o => o.textContent.trim() === '<label>').click()")`.
7. **Download every submitted report before completing.** Track all
   reports you submit. Before marking the task complete, go back
   and confirm every pending report has been downloaded. You may
   do other work while reports generate, but do not forget them.
8. **Always click into the detail page to check status.** A report
   that appears to have "no data" on the list page may actually be
   Completed with a Download link on the detail page. Never assume
   status from the list — always click through and check the
   history table's Status column and Download link.

### Country Switching

#### Seller Central

```bash
browser-use <<'PY'
print(page_info())   # look for the "Switch Accounts" button
js("document.querySelector('button[aria-label=\"Switch Accounts\"]').click()")
print(page_info())   # locate country options
js("[...document.querySelectorAll('button, [role=option]')].find(e => e.textContent.trim() === 'United States').click()")
wait_for_load()      # page reloads with the new country context
PY
```

Some accounts may have the switcher inside
`#ngstrim-account-switcher-dropdown`. If the button is not visible,
try clicking on the store name / country text area to reveal it.

#### Ad Console

The advertising console has its OWN country switcher, separate from
Seller Central's. Use this to switch between marketplaces without
leaving the ad console.

```bash
browser-use <<'PY'
# 1. Click the marketplace switcher (shows current country name)
js("document.querySelector('[data-takt-id=header_marketplace_switcher]').click()")
print(page_info())
# Look for buttons with id=aac-chrome-{CODE} (e.g. aac-chrome-AU).
# Each has role=option and value={CODE}, selected=true/false.

# 2. Click the target country option
js("document.querySelector('button#aac-chrome-AU').click()")

# 3. Click "Change country" to confirm
js("document.querySelector('button#aac-chrome-change-country-button').click()")
wait_for_load()   # page reloads with the new country context
PY
```

Available country buttons follow the pattern `id=aac-chrome-{CODE}`
where CODE is the 2-letter country code (US, UK, etc.).
Cancel button: `id=aac-chrome-cancel-button`.

**Note**: This switcher is DIFFERENT from Seller Central's
`button[aria-label="Switch Accounts"]`. The ad console uses
`ax-chrome-marketplace-switcher` with `data-takt-id` buttons.
Seller Central's account switcher may also appear as a modal
overlay — they are separate UI patterns.

### Wait Times by Report Type

| Report Type | Typical Wait | Notes |
|------------|-------------|-------|
| Search term | 5-15 min | Moderate data volume |
| Targeting / Keyword | 5-15 min | |
| Advertised product | 5-30 min | Per-ASIN, can be large |
| Campaign | 3-10 min | Fewer rows |
| Budget | 3-10 min | |
| Placement | 5-15 min | |
| Purchased product | 10-30 min | Cross-references purchases |
| Gross and Invalid Traffic | 15-60 min | Complex analysis |
| Conversion path | 30 min – 24 hours | Cross-campaign, very heavy |
| Category benchmark | 10-30 min | Requires peer data |

**Key**: Simple campaign/budget reports are fastest. Cross-campaign or
purchased-product reports take longer. If the report hasn't completed
after 1 hour, it may take up to 24 hours.

### Campaign Report CSV Structure

```csv
State,Campaign name,Status,Type,Targeting,Campaign start date,
Campaign end date,Campaign budget amount (converted),
Campaign budget amount,Clicks,CTR,Total cost (converted),
Total cost,CPC (converted),CPC,Purchases,Sales (converted),
Sales,ACOS,ROAS
```

#### Column Details (20 columns)

| Column | Type | Example |
|--------|------|---------|
| State | enum | `ENABLED`, `PAUSED`, `ARCHIVED` |
| Campaign name | string | Campaign identifier |
| Status | enum | `CAMPAIGN_STATUS_ENABLED`, `CAMPAIGN_OUT_OF_BUDGET` |
| Type | enum | `SP` (Products), `SB` (Brands), `SD` (Display) |
| Targeting | enum | `MANUAL`, `AUTO` |
| Campaign start date | date | `01/15/2026` |
| Campaign end date | date | Empty if ongoing |
| Campaign budget amount (converted) | currency | `USD 50.00` |
| Campaign budget amount | currency | `USD 50.00` |
| Clicks | int | `24` |
| CTR | decimal | `0.0312` (click-through rate) |
| Total cost (converted) | currency | `USD 65.80` |
| Total cost | currency | `USD 65.80` |
| CPC (converted) | currency | `USD 2.74` |
| CPC | currency | `USD 2.74` (cost per click) |
| Purchases | int | `1` |
| Sales (converted) | currency | `USD 54.99` |
| Sales | currency | `USD 54.99` |
| ACOS | decimal | `0.4520` (ad cost / sales) |
| ROAS | decimal | `2.2125` (return on ad spend) |

#### Analysis Script Hint

```python
import pandas as pd

df = pd.read_csv('campaign_report.csv')
# Parse currency columns
for col in ['Total cost', 'Sales', 'CPC', 'Campaign budget amount']:
    df[col + '_num'] = (
        df[col].str.replace(r'[^\d.]', '', regex=True).astype(float)
    )

# Top campaigns by ROAS
top_roas = df.sort_values('ROAS', ascending=False).head(10)
# Campaigns over budget
over_budget = df[df['Status'] == 'CAMPAIGN_OUT_OF_BUDGET']
# Total spend vs sales
total = df[['Total cost_num', 'Sales_num']].sum()
```

### Advertised Product Report CSV Structure (typical)

```csv
Date,Campaign Name,Ad Group Name,Targeting,Match Type,
Customer Search Term,Advertised SKU,Advertised ASIN,
Impressions,Clicks,CTR,CPC,Spend,
7 Day Total Sales,Total ACOS,Total ROAS,
7 Day Total Orders,7 Day Total Units,
7 Day Conversion Rate
```

### Search Term Report CSV Structure (typical)

```csv
Date,Campaign Name,Ad Group Name,Targeting,Match Type,
Customer Search Term,Impressions,Clicks,CTR,CPC,Spend,
7 Day Total Sales,ACOS,ROAS,7 Day Total Orders,7 Day Total Units
```

#### Analysis Hint (Search Term / Advertised Product)

```python
import pandas as pd

df = pd.read_csv('search_term_report.csv')
# Find high-spend low-conversion terms
waste = df[(df['Spend'] > 10) & (df['7 Day Total Orders'] == 0)]
# Best performing search terms
best = df[df['ROAS'] > 3].sort_values('7 Day Total Sales', ascending=False)
# SKU-level performance
sku_perf = df.groupby('Advertised SKU').agg({
    'Spend': 'sum',
    '7 Day Total Sales': 'sum',
    'Clicks': 'sum',
})
sku_perf['ACOS'] = sku_perf['Spend'] / sku_perf['7 Day Total Sales']
```

---

## 7. Download Behavior

### Where Files Go

Browser-use downloads go to the **browser profile's download directory**,
which is per-store. The path depends on the browser backend configured for
the store (check `stores/{store-slug}/STORE.md` for the download path).

Files do **NOT** download to the task workspace automatically.

### Copying Downloads to Task Workspace

The browser download directory is **SHARED across all tasks**. It accumulates
files from every run. When copying to the task workspace:

- **Only copy files matching** the invoices/reports you just downloaded
- Files with `(1)`, `(2)` etc. suffixes are re-downloads from previous runs.
  Always copy the **latest** version (highest number or most recent timestamp)
  and rename to remove the suffix: `INV-001 (3).pdf` → `INV-001.pdf`
- **Do NOT** copy the entire directory or use wildcards like `cp *.pdf`

```bash
# List downloaded files (newest first)
# DLDIR path depends on the browser backend configured for the store
ls -lt "$DLDIR/"

# Copy specific file to task workspace
cp "$DLDIR/INV-001.pdf" workspace/invoices/

# If only "(N)" versions exist, copy the latest and rename:
cp "$DLDIR/INV-001 (2).pdf" workspace/invoices/INV-001.pdf
```

### Download Mechanism by Report

| Report | Format | Mechanism | API Endpoint |
|--------|--------|-----------|-------------|
| Business Reports | CSV | Button click → direct download | Internal |
| Fulfillment | CSV/TSV | Request → generate → download | Internal |
| Tax Document Library | PDF | "View" button → form POST | POST `/tax/seller-fee-invoices` |
| Custom Reports | CSV | Request → generate → download | Internal |
| Payments Repository | CSV | "Download CSV" button → XHR | GET `/payments/reports/api/download-report?reportId={UUID}` |
| Advertising Reports | CSV | "Download" link → direct | GET `/reports/subscriptions/{id}/download-report/{run-id}` |

### Download Troubleshooting

If downloads don't appear in the browser download directory:
1. Check that the CDP proxy is correctly forwarding download headers
2. Try fetching the download URL directly via `js("...fetch()...")`
3. For Payment reports, the API endpoint can be called directly from the browser context
4. For Tax PDFs, the form POST may open in a new tab instead of downloading

---

## Quick Reference: Navigation Cheat Sheet

```
Seller Central Home
├── hamburger menu (☰) → hover "Reports"
│   ├── Business Reports        → /business-reports
│   ├── Custom Analytics        → (new)
│   ├── Fulfillment             → /reportcentral/WelcomePage
│   ├── Advertising Reports     → advertising.amazon.{tld}/reports
│   ├── Return Reports          → /reportcentral/WelcomePage
│   ├── Custom Reports          → /listing/reports
│   ├── Inventory Reports       → /inventory-reports
│   ├── Tax Document Library    → /tax/seller-fee-invoices
│   ├── Manage Taxes            → /tax/tax-settings
│   └── Selling Economics & Fees → /selling-economics-and-fees
│
├── hamburger menu (☰) → hover "Payments"
│   └── Payments Dashboard      → /payments/reports-repository (tab)
│
└── Advertising Console (separate app)
    └── Measurement & reporting → Sponsored ads reports
```

## Tips

- **Always dispatch `mouseover`** on hamburger menu categories, never a click
- **Check `aria-expanded`** in `page_info()` output to confirm the submenu is revealed
- **For instant reports** (Business, Tax Library): click Download/View directly
- **For generated reports** (Fulfillment, Custom, Payments): Request → poll
  status → Download when ready. Typically **1-5 minutes**.
- **For ad reports**: Create/Run → wait **5 min to 24 hours** depending on
  type → check Status = "Completed" → Download
- **Always copy downloads** from the browser download dir to task workspace
- Use **direct URLs** when possible to skip hamburger menu navigation
- The **hamburger menu button** is inside a `navigation-hamburger-menu` shadow
  DOM element — look for `role=button` inside it
- **Payment CSV**: skip first 7 lines (definitions) when parsing
- **Ad report CSV**: no header lines to skip, starts with column row
- When building analysis scripts, use the column structures documented above
