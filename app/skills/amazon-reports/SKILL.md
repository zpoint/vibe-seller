---
name: amazon-reports
description: "Amazon platform only. MUST load BEFORE taking any action when the task involves Amazon Seller Central report pages (Tax Document Library, Business Reports, Fulfillment, Payments CSV, Advertising Reports, etc.). Contains URLs, hover navigation, CSV structures, and wait times."
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
This menu uses a **hover-to-reveal** pattern — you must `hover`, not `click`,
on a category to reveal its submenu items.

```bash
# 1. Open the hamburger menu
browser-use state               # find the menu button (role=button inside navigation-hamburger-menu shadow DOM)
browser-use click <menu-btn>    # opens the full sidebar overlay

# 2. Reveal submenu by hovering
browser-use state               # find "Reports" (aria-expanded=false)
browser-use hover <reports-idx> # changes aria-expanded to true, reveals flyout

# 3. Click the submenu item
browser-use state               # find target item (e.g. "Tax Document Library")
browser-use click <item-idx>    # navigates to the page
```

**Do NOT click the category name** — clicking navigates away instead of
revealing the submenu. Always `hover` first, then `state` to see submenu
items, then `click` the target.

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
browser-use open "https://sellercentral.amazon.{tld}/business-reports"
browser-use state   # find "Download" button (inside shadow DOM kat-table-cell)
browser-use click <download-btn>   # triggers CSV download — immediate
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
browser-use open "https://sellercentral.amazon.{tld}/reportcentral/WelcomePage"
browser-use state   # find report link in sidebar (id=report-nav-link-url)
browser-use click <report-link>   # opens specific report
browser-use state   # find "Request Report" / date range / "Generate Report"
browser-use click <request-btn>
# Wait 1-5 minutes. Refresh page to check status.
browser-use open "https://sellercentral.amazon.{tld}/reportcentral/WelcomePage"
browser-use state   # find "Download" button when status shows ready
browser-use click <download-btn>
```

### Wait Time

**1-5 minutes** for most reports. Refresh the page to check status.

### Monthly Storage Fees — publication latency

**Verified observation (2026-05-07, both `sellercentral.amazon.sa`
and `.ae`):** requesting Monthly Storage Fees for the previous
month early in the new month returns `No Data Available` on the
detail page even though the request itself is accepted.

Concrete data points captured by driving the wrapper directly:
- April 2026 (range 01/04/26-30/04/26) requested May 7 → status
  `No Data Available` on both SA and AE.
- March 2026 storage was successfully requested April 13.
- February and January 2026 storage successfully requested
  April 14.

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

Where `{CC}` = country code (e.g. `AE`), `{ENTITY}` = Amazon entity code, `{SEQ}` = sequence number.

### Export Flow

```bash
browser-use open "https://sellercentral.amazon.{tld}/tax/seller-fee-invoices"
browser-use state   # find View buttons (id=view_invoice_button-announce)

# Each "View" button has a value=urn:alx:ver:{UUID} attribute
# Clicking triggers a form POST that downloads a PDF
browser-use click <view-btn-idx>

# For a specific month, search state output for date strings:
# e.g. "Mar 01" or "Mar 31" for March invoices

# For older invoices beyond most recent 1,000:
browser-use scroll down   # to bottom
browser-use state         # find "Load More"
browser-use click <load-more-btn>
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
browser-use open "https://sellercentral.amazon.{tld}/listing/reports"
browser-use state   # find "Select Report Type" dropdown and "Request Report"

# Select type
browser-use click <dropdown>
browser-use state   # find options
browser-use click <report-type-option>

# Request
browser-use click <request-btn>

# Wait 1-5 minutes, then check for Download
browser-use state   # table shows new row with status
browser-use click <download-btn>
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
browser-use open "https://sellercentral.amazon.{tld}/payments/reports-repository"
browser-use state

# 1. Report Type dropdown (id=katal-id-* with title=Transaction)
browser-use click <report-type-dropdown>
browser-use state   # find kat-option elements
browser-use click <option>   # Transaction or Summary

# 2. Date Range — set start and end dates
browser-use state   # find date range inputs
browser-use click <start-date>
browser-use input <start-date> "01/01/2026"
browser-use click <end-date>
browser-use input <end-date> "03/31/2026"

# 3. Request Report
browser-use click <request-report-btn>

# 4. Wait ~1-3 minutes, then scroll down to find the report in the table
browser-use scroll down
browser-use state   # find "Download CSV" button (inside kat-table-cell shadow DOM)
# Status must show "Ready" (with check_circle icon)
browser-use click <download-csv-btn>
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
| marketplace | string | `amazon.ae` | Marketplace domain |
| fulfillment | string | `Amazon` | `Amazon` (FBA) or `Seller` |
| order city | string | City name (may be Arabic) | Buyer's city |
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
browser-use open "https://advertising.amazon.{tld}/reports"
browser-use eval "document.querySelector('#advertising-reports button').click()"
browser-use state
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
there. Real incident (iteration 6, 2026-05-07): an agent saw
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

**Time-unit gate for reuse — verified 2026-05-07.** The list page
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
browser-use eval "(function() {
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
})()"
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
browser-use open "https://advertising.amazon.{tld}/reports"
browser-use eval "(function() {
  var section = document.querySelector('#advertising-reports');
  if (!section) return '[]';
  var anchors = section.querySelectorAll('a[href*=\"/reports/history/\"]');
  var urls = [];
  for (var a of anchors) urls.push(a.href);
  return JSON.stringify(urls);
})()"
# Returns array of detail page URLs (each has a unique UUID)
# Works regardless of language (EN/CN/AR)

# 2. Visit each URL and check the history table
browser-use open "<url>"
browser-use state
# Look for: "Completed" + target period + "Download" <a>
# If this one doesn't match, try the next URL
# If it matches → click Download

# 3. On the detail page, find and click Download
browser-use state   # find <a> with text "Download"
browser-use click <download-link>
```

**Do NOT click right-panel rows** — they show misleading inline text.
**Do NOT guess which left-panel link is correct** — use the JS eval
approach above to get direct URLs.

### Export Flow — Create New Report (only if no existing match)

Only create a new report if the list page has no completed report
matching the requested date range and report type.

```bash
# 1. Open reports page and click Create report
browser-use open "https://advertising.amazon.{tld}/reports"
browser-use eval "document.querySelector('#advertising-reports button').click()"
browser-use state

# 2. Select report type (dropdown renders in #portal as role=listbox)
#    Default is "Search term". Click the type button to open dropdown:
browser-use click <type-btn>       # button[id*=report-type-control]
browser-use state                  # look for role=option buttons in #portal
browser-use click <desired-type>   # e.g. button[role=option][value=adProducts]
# Type values: searchTerms, keywords, adProducts, campaigns, budgets,
#              placements, audience, purchasedProducts, etc.

# 3. CONFIRM Time unit = summary (the page default; do NOT switch to Daily).
#    For an Advertised Product report consumed by downstream profit
#    aggregation, Summary returns one row per (campaign, ad group, SKU)
#    with the period totals — which is what the consumer expects.
#    Daily blows the file up ~30× and the per-row "7 Day Total Units (#)"
#    uses a 7-day attribution window, so summing daily rows is NOT
#    equivalent to Summary's monthly totals. Verify the radio:
browser-use eval "(function(){var r=document.querySelector('input[name=\"time-units\"]:checked');return r?r.value:'(no time-units radio found)';})()"
# Expected: "summary". If it returns "day" or "daily", click the
# summary option:
#   browser-use click <summary-radio>

# 4. Set time period
#    Click the period button to open a HYBRID picker:
#    - Top section: preset buttons (Today, Yesterday, Last month, etc.)
#    - Bottom section: dual-calendar date picker with clickable day cells
browser-use click <period-btn>     # button[id*=report-period-control]
browser-use state                  # presets + calendar appear in #portal
# Option A — use a preset:
browser-use click <preset-btn>     # e.g. button[value=LAST_MONTH]
# Option B — pick custom dates on the calendar:
browser-use click <start-date>     # button[aria-label="Sunday March 1 2026"]
browser-use click <end-date>       # button[aria-label="Tuesday March 31 2026"]
browser-use click <save-btn>       # Save button at bottom of calendar

# 5. Run the report
browser-use click <run-btn>        # button#urc_run_subscription_button

# 6. WAIT — dialog closes, you land on the report detail page.
#    Poll the detail page for completion (reload page each poll):
browser-use eval "location.reload()"
browser-use state   # check Status column in run history table
# Status values in the detail page history table:
#   "Pending"    → Amazon has not started; Action column empty. WAIT.
#   "Processing" → Amazon is generating; Action column empty. WAIT.
#   "Completed"  → Done; Download <a> appears in Action column.
# All non-Completed statuses are normal — just reload and retry.
# Do NOT treat Pending/Processing as errors.
browser-use click <download-link>  # <a> with text "Download"
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
   browser-use automation. Use presets like `LAST_MONTH` when they
   match the target period.
5. **~90-day calendar lookback (ad console only).** In the advertising
   console's period picker, dates older than ~90 days may appear
   greyed out or unselectable. If the target period is outside
   preset range and calendar dates are greyed out, the report
   cannot be generated — report this to the user.
6. **Dropdown options use `kat-option` elements.** When clicking
   dropdowns (month, year, report type), the options render as
   `kat-option` custom elements. Use `browser-use state` to find
   them, then click the matching option by index.
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
browser-use state   # look for "Switch Accounts" button
browser-use click <switch-accounts-btn>  # button[aria-label="Switch Accounts"]
browser-use state   # find country options
browser-use click <country-option>
# Page reloads with the new country context
```

Some accounts may have the switcher inside
`#ngstrim-account-switcher-dropdown`. If the button is not visible,
try clicking on the store name / country text area to reveal it.

#### Ad Console

The advertising console has its OWN country switcher, separate from
Seller Central's. Use this to switch between marketplaces without
leaving the ad console.

```bash
# 1. Click the marketplace switcher (shows current country name)
browser-use eval "document.querySelector('[data-takt-id=header_marketplace_switcher]').click()"
browser-use state
# Look for buttons with id=aac-chrome-{CODE} (e.g. aac-chrome-AU)
# Each has role=option and value={CODE}, selected=true/false

# 2. Click the target country option
browser-use click <country-btn>   # e.g. button#aac-chrome-AU

# 3. Click "Change country" to confirm
browser-use click <confirm-btn>   # button#aac-chrome-change-country-button
# Page reloads with the new country context
```

Available country buttons follow the pattern `id=aac-chrome-{CODE}`
where CODE is the 2-letter country code (AE, AU, etc.).
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
| Campaign budget amount (converted) | currency | `AED 50.00` |
| Campaign budget amount | currency | `AED 50.00` |
| Clicks | int | `24` |
| CTR | decimal | `0.0312` (click-through rate) |
| Total cost (converted) | currency | `AED 65.80` |
| Total cost | currency | `AED 65.80` |
| CPC (converted) | currency | `AED 2.74` |
| CPC | currency | `AED 2.74` (cost per click) |
| Purchases | int | `1` |
| Sales (converted) | currency | `AED 54.99` |
| Sales | currency | `AED 54.99` |
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
cp "$DLDIR/INV-2026-100880.pdf" workspace/invoices/

# If only "(N)" versions exist, copy the latest and rename:
cp "$DLDIR/INV-2026-100880 (2).pdf" workspace/invoices/INV-2026-100880.pdf
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
2. Try fetching the download URL directly via `browser-use eval` with `fetch()`
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

- **Always use `hover`** on hamburger menu categories, never `click`
- **Check `aria-expanded`** in state output to confirm submenu is revealed
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
