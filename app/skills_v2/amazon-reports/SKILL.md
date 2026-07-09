---
name: amazon-reports
description: "Amazon platform only. MUST load BEFORE taking any action when the task involves Amazon Seller Central report pages (Tax Document Library, Business Reports, Fulfillment, Payments CSV, Advertising Reports, etc.). Contains URLs, hover navigation, CSV structures, and wait times."
requires: [amazon-shared]
review:
  criteria: |
    - The requested report(s) were ACTUALLY exported: the file exists,
      is non-empty, and has rows > 0 — OR the page explicitly shows "No
      Data Available" (a valid empty result, not a missing/failed pull).
    - Scope matches the ask: every requested country / the requested date
      window is covered, not a partial pull.
  verify_by: |
    Open the newest downloaded file in ~/.vibe-seller/downloads/<slug>/
    and confirm the header columns + a non-zero row count; open the
    report-history page and confirm the "Date Range Covered" and scope
    match the request. Do not accept a claimed export without the file.
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

- **Advertising reports** (Unified Reporting, `advertising.amazon.{tld}/reporting`):
  the report list shows saved/generated reports with a **报告状态**
  column; click into a **已完成** (Completed) row for the Download link.
  If a completed run matches the date range, download it. (The old
  `/reports` console is retiring 2026-12-31 — see §6.)

- **Payments reports** (`/payments/reports-repository`): Check the report
  list for existing reports covering the target period.

Only request/generate a new report if no existing one covers the needed
date range.

## An empty export is a SUCCESS; "N/A" must be proven on the page

Two rules that together stop the biggest false-failure pattern (a store
was marked FAILED for reports it "doesn't have" — decided from metadata,
never checked on the page):

**1. Store metadata does NOT tell you which reports apply.** The store's
`platforms` / `countries` (e.g. `{"amazon": ["SA"]}`) only say which
marketplaces it sells on. They encode **nothing** about FBA enrollment
or whether an Advertising account exists. **Never** conclude "no FBA →
skip storage/returns" or "no Ads → skip the ad report" from metadata.
You must open the actual page and let *it* tell you.

**2. A report that exports with zero data rows is DOWNLOADED, not
missing.** Request each report on its page:

- **Ads report:** warm the ad console first (via Campaign Manager in the
  Seller Central menu, **or** the `choose-account?destination=/reporting`
  flow in §6.1) — a **cold** direct `advertising.amazon.{tld}` URL gives
  a "Sign in / Register" marketing page. Once warm, build the report in
  Unified Reporting (§6) — Amazon **generates it even with zero
  campaigns** (headers, 0 rows) → download that empty file, it's a
  completed deliverable. The ad report is genuinely N/A **only** if,
  *after* warming up, the store shows an advertiser **onboarding /
  registration** flow (no advertiser account). A marketing landing
  reached by direct URL proves nothing — it just means you weren't SSO'd;
  it is NOT evidence of "no Ads".
- **FBA storage / returns:** open the report page and request the month.
  An empty result / "No Data Available" for a *valid* request still
  means you asked correctly — download whatever file is produced. Only
  if the page **explicitly says the store is not enrolled in FBA** is it
  N/A.

**When to use each task outcome:**

- **downloaded** (incl. empty 0-row exports) → deliverable met.
- **N/A** — only when the *page* proved the capability is absent
  (advertiser-registration landing; explicit not-enrolled-in-FBA). Record
  in `vibe_seller_set_task_result`; do **NOT** `vibe_seller_set_task_error`.
- **pending-Amazon-latency** (e.g. Monthly Storage Fees not yet
  published, see §2) → `vibe_seller_set_task_result`, not
  `vibe_seller_set_task_error`.
- **failed** → `vibe_seller_set_task_error` **only** for a report the page
  shows you *should* be able to get but couldn't (dead button, 0-byte
  download, error page).

If the only gaps are proven-N/A or latency-pending, the task
**COMPLETES**. State per report which of {downloaded, N/A-proven-on-page,
pending-latency, failed} it is, so the outcome is unambiguous — and never
downgrade "I didn't check" into "N/A".

## Clicking a Download button (Amazon `kat-*` shadow DOM)

Report tables wrap the Download control as
`kat-table-row → kat-table-cell → kat-button`, and the real clickable
`<button>` lives **inside `kat-button.shadowRoot`**. A plain
`document.querySelector('kat-table-cell button')` (or any
`[data-testid=...]` / `.download-btn` guess) returns `null` — CSS
selectors do **not** cross shadow boundaries, and
`[...document.querySelectorAll('button')]` never sees a button that
lives inside a shadow root. So those selectors silently no-op and you
waste turns. Reach the inner button through the shadow host and
`.click()` it. (This `.click()` works for table Download buttons; the
left-sidebar `kat-button`s are the exception — those need a coordinate
click.)

**Use this one snippet on every report page.** Match the target row by a
substring of its date range, then click its Download control:

```bash
browser-use <<'PY'
print(js(r"""
var rowMatch = '01/06/26';   // substring of the TARGET row's date range
var rows = document.querySelectorAll('kat-table-row, tr');
for (var r of rows) {
  if (!(r.textContent || '').includes(rowMatch)) continue;
  for (var h of r.querySelectorAll('kat-button, kat-link, a, button')) {
    var real = h.shadowRoot ? h.shadowRoot.querySelector('button, a') : h;
    var label = (h.textContent||'') + ' ' + ((h.getAttribute && h.getAttribute('label')) || '');
    if (real && (h.tagName === 'KAT-BUTTON' || /download|\.csv/i.test(label))) {
      real.click();
      return 'clicked download for row ~ ' + rowMatch;
    }
  }
}
return 'NO download control for ~' + rowMatch + ' (check the date substring / row exists)';
"""))
import time; time.sleep(3)   # let the download start
PY
```

On "NO download control", fall back to `print(page_info())` (it often
lists a plain `Download` link, clickable by index) or `capture_screenshot()`
+ coordinate click — but try the snippet first; it one-shots the common
`kat-button` shadow case. The same host-then-inner-button walk applies to
any `kat-button` (e.g. "Request Report", "Download CSV") — match on its
`label`/text instead of the date substring.

## Setting a custom date range (works across every Seller Central variant)

Date controls differ **per marketplace and per report page** — and
Amazon changes them over time. You will meet native `<select>` presets,
`kat-dropdown` presets, plain `<input>` date fields, `kat-date-picker` /
`kat-date-range-picker` shadow widgets, and month/year dropdowns — the
same report can render differently on `.sa` vs `.ae` vs `.com`. **Do not
assume a widget.** Follow the same three-step loop everywhere:
**observe → interact the human way → verify the outcome.** The outcome
check is DOM-independent and is what makes this robust.

**Step 1 — Observe.** `capture_screenshot()` and glance at the DOM
(`document.querySelector('kat-date-range-picker')`? a native
`<select>`? plain `input[placeholder]`?). Identify the preset control
and, once a custom range is chosen, the two date fields.

**Step 2 — Interact the way a human would for THAT widget.** Prefer the
interaction that fires the component's real handlers:

- **Preset → "Exact dates":** a **native `<select>`** takes a value-set
  (`el.value='…'` + `change`); a **`kat-dropdown`** does **not** —
  `click_at_xy` to expand, then `click_at_xy` the "Exact dates" row.
- **The two date fields:** the reliable path on **every** variant is the
  **calendar popup with real clicks** — click the field's calendar icon,
  `click_at_xy` the **‹ / ›** month arrows to the target month, then
  `click_at_xy` the day cell. (Locate cells from the screenshot — they
  aren't reliable via `page_info()`/JS rects.) Plain `<input>` fields on
  some marketplaces also accept `fill_input(selector, "<date>")` — try it
  if they truly are light-DOM inputs, but **only trust it after Step 3**.

> ⚠️ **On `kat-*` date widgets, programmatic value-setting is
> display-only and silently ignored on submit** — live-verified on AE FBA
> Customer Returns (2026-07-07): `setAttribute('start-value')`, the
> `startValue`/`endValue` property, the nested shadow `<input>.value` +
> `input`/`change` events, and CDP `Input.insertText` all updated the
> visible field (and even `rp.startValue`) yet the report generated for
> **today**. `dropdown.selectOption('-1')` likewise sets `.value` without
> emitting the reveal event. The component's submit reads an internal
> model only real user input updates — so calendar clicks, not scripts.

**Step 3 — Verify the outcome (the universal acceptance test).** This is
the check that survives every DOM difference:

1. A quick sanity read after a calendar click —
   `js("var rp=document.querySelector('kat-date-range-picker'); return rp?rp.startValue+' -> '+rp.endValue:'n/a'")`
   — is fine as a *first* look, but it is **not authoritative**: a
   programmatic set can leave `startValue`/`endValue` showing your target
   without the value actually committing (see the warning above), so a
   match here does **not** guarantee the report will use it.
2. The authoritative check: request the report, then **CONFIRM the new
   history row's "Date Range Covered" is your target month, not today.** A
   today-dated (or wrong) row means the date never committed — go back to
   Step 2 and use real clicks; do not download the wrong-range file.

Because the calendar fills the field itself, **date-format variance
(`DD/MM/YYYY` / `MM/DD/YYYY` / `YYYY/M/D`) is irrelevant** — never hand-
type the format. Month/year dropdowns (Payments Reports Repository,
Monthly Storage Fees) are `kat-dropdown`s → select by real click, never
`selectOption()`; month index has been seen **0-indexed** (Jan=0) on some
pages — confirm against the visible label, don't hardcode it.

**Report-status polling — keep each sleep short.** After requesting a
report, poll for the Download button; do **not** `time.sleep(120)` in one
call — a single long sleep exceeds the browser-use tool timeout and the
turn is killed mid-wait. Loop with short sleeps and re-check instead:

```bash
browser-use <<'PY'
import time
for _ in range(6):          # ~6 × 30s ≈ 3 min, each well under the tool timeout
    time.sleep(30)
    info = page_info()
    if 'Download' in info or 'Ready' in info:
        break
print(info)
PY
```

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
| Ad reports — Unified Reporting | `advertising.amazon.{tld}/reporting` | CSV download | few min – 30+ min |

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
PY
# Trigger the CSV download with the canonical shadow-DOM download snippet
# (see "Clicking a Download button" above). The Business Reports view has a
# single Download button, so match on its label rather than a date row:
#   real = h.shadowRoot ? h.shadowRoot.querySelector('button,a') : h  → real.click()
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
print(page_info())   # sidebar report links carry id=report-nav-link-url
PY
# Click the sidebar link and the "Request Report" button by their
# page_info() index (they are kat-* controls; a raw querySelector guess
# no-ops). For any kat-button, use the host→inner-button walk from
# "Clicking a Download button" above, matching on its label text.
# Set the date range first via "Setting a custom date range" above.
# Wait 1-5 minutes, then download with the canonical download snippet,
# matching the history row by its date-range substring.
```

### Wait Time

**1-5 minutes** for most reports. Refresh the page to check status. Poll
with short sleeps (never one `time.sleep(120)` — see the polling loop in
"Setting a custom date range" above).

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

Select `Exact dates` FIRST — the `.csv` download control only appears
*after* an exact range is chosen (with a preset selected, only a TSV
button shows).

Open the page, then set the range with the **real-click calendar
method** in "Setting a custom date range" above — screenshot, click the
preset dropdown, click "Exact dates", then set start/end from the
calendar popups. Do **not** try `selectOption('-1')` or any programmatic
date-set: live-verified that they leave the request on today's date.

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/reportcentral/CUSTOMER_RETURNS/1")
wait_for_load()
import time; time.sleep(3)
capture_screenshot()   # locate the preset dropdown + (after Exact dates) the calendar icons
PY
```

Finally request and download — the "Request .csv" control is a
`kat-button`, so use the host→inner-button walk from "Clicking a
Download button". **Then confirm the new history row's date range is the
month you asked for, not today**, before downloading (a today-dated row =
the date never committed; redo the calendar clicks). The `.csv` control
only appears once an exact range is set; a preset shows only TSV.

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

# Select the report type. If it is a kat-dropdown, use its native API
# (see "Setting a custom date range" above):
#   d.toggleExpanded(true); d.selectOption('<value>')
# A plain [data-testid=...] / <select> querySelector no-ops on a kat-*
# control. Then click "Request Report" via the host→inner-button walk
# from "Clicking a Download button", matching its label text.
PY
# Wait 1-5 minutes (poll with short sleeps — see the polling loop above),
# then download the new row with the canonical download snippet from
# "Clicking a Download button", matching the row by its date-range /
# request-time substring.
```

### Wait Time

**1-5 minutes**. Refresh page to check if Report Status = ready.

### Multi-country downloads collide on filename — rename each immediately

The All Listings / Active Listings Report downloads with a **fixed,
date-based filename** (e.g. `All+Listings+Report_MM-DD-YYYY.txt`) that is
**identical across marketplaces**. If you download the report for two
countries in a row (e.g. SA then AE), the second **silently overwrites**
the first in the download dir — no browser prompt. Two consequences:

- **Rename right after each download**, before triggering the next. Copy
  the freshly-downloaded file to a country-suffixed name
  (`All_Listings_<CC>_<YYYY-MM-DD>.txt`) the moment it lands, then request
  the next country.
- The All Listings TSV is **account-level** (byte-identical across a
  unified account's marketplace subdomains), so a per-country re-download
  often yields the same rows anyway — verify content rather than assuming
  each country's file differs.

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

# 2. Date Range — set start and end dates with the REAL-CLICK calendar
#    method in "Setting a custom date range" above (screenshot → click
#    the field's calendar icon → month arrows → click the day cell).
#    Programmatic date-sets do NOT commit. If the page offers a "Month"
#    radio, click it and pick month + year from the kat-dropdowns by
#    real clicks (month observed 0-indexed). Verify the picker shows your
#    range before requesting.

# 3. Request Report — kat-button; use the host→inner-button walk from
#    "Clicking a Download button", matching its label.
PY
# 4. Wait ~1-3 minutes (poll with short sleeps — see the polling loop
#    above), then scroll down and download the "Ready" row (check_circle
#    icon) with the canonical download snippet, matching on the
#    "Download CSV" label. A raw querySelectorAll('button') never sees
#    the button inside kat-button.shadowRoot.
browser-use <<'PY'
js("window.scrollTo(0, document.body.scrollHeight)")
print(page_info())
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

## 6. Advertising Reports — Unified Reporting (统一报告 / new report center)

> **⚠️ Amazon is retiring the old ad-report consoles.** The Sponsored Ads
> report console (`advertising.amazon.{tld}/reports`) **and** the Amazon
> DSP report console close on **2026-12-31** — after which all saved
> reports, scheduled reports, and their history are permanently deleted.
> New-report creation/editing in the old console is disabled from
> **2026-12-17** (existing reports go read-only). **All new ad reporting
> goes through Unified Reporting** (统一报告), the centralized report
> center documented below. Migration guide:
> `advertising.amazon.com/help/GK3JZZE5QLTF8U8A`.

Unified Reporting combines **every ad product** (Sponsored Products,
Sponsored Brands, Sponsored Display, Sponsored TV, **and Amazon DSP**)
and **multiple accounts / marketplaces** into one report, built from a
shared library of standardized **dimensions** and **metrics**. One
report can span products and countries that used to need several
separate exports.

### 6.0 The two report systems (know which URL you're on)

| System | URL | State |
|--------|-----|-------|
| **Unified Reporting** (use this) | `advertising.amazon.{tld}/reporting` | Current. List + templates + custom builder. |
| Legacy Sponsored-ads console (retiring) | `advertising.amazon.{tld}/reports` | Read-only after 2026-12-17, deleted 2026-12-31. See §6.10. |

The nav item **效果衡量和报告 → 报告** (Measurement & reporting →
Reports) links to `/reporting` (unified). **搜索广告报告** (Search ads
reports) still links to the old `/reports` console.

### 6.1 Navigation & warm-up

Same cold-session caveat as the old console: a **cold** direct hit to
`advertising.amazon.{tld}/reporting` redirects to
`advertising.amazon.com/register` (the marketing/onboarding page) — that
is **NOT** evidence the account has no ads; it just means you are not
SSO'd into the ad console yet. Warm the account first:

**Warm-up via choose-account (reliable):**

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/choose-account?destination=/reporting")
wait_for_load()
# Click the advertising account button (its label = your ad-account name),
# then the marketplace button (e.g. 沙特阿拉伯 / 阿联酋 / United States).
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '<your-ad-account>').click()")
print(page_info())            # marketplace sub-buttons appear
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '沙特阿拉伯').click()")
wait_for_load()
print(page_info())            # → advertising.amazon.{tld}/reporting?entityId=<ENTITY>
PY
```

You land on `advertising.amazon.{tld}/reporting?entityId=<ENTITY>`. The
`entityId` is per-account **and per-marketplace** — each marketplace of
a multi-country account has its own `entityId`. **Once SSO cookies are
set in the profile, direct nav to `.../reporting?entityId=<ENTITY>`
(and `.../reporting/new?...`) works warm** for the rest of the session.
You can also reach it in-console via the left nav **效果衡量和报告 →
报告**. Do NOT hard-code an `entityId` from a past run — read it back
from the URL after warm-up.

### 6.2 The report list page (`/reporting`)

Top of the page: **report templates** row (§6.3) + a **创建报告**
(Create report) button for a custom report. Below: your saved/generated
reports table with columns **报告名称** (Report name), **报告状态**
(Report status), **创建时间** (Created), **上次修改时间** (Last
modified), **上次运行时间** (Last run), **下次运行时间** (Next run),
**账户** (Account), **报告提取频率** (Frequency). Empty state: "尚无
报告 — 首先选择模板或创建自定义报告".

A finished report is downloaded from here (§6.7). **报告状态** values:
排队/正在处理 (Queued/Processing — wait) → 已完成 (Completed — download
available) → 失败 (Failed).

### 6.3 Templates (fastest path)

Three prebuilt templates sit at the top of `/reporting`, each with a
**使用模板** (Use template) button. Clicking one opens the builder at
`/reporting/new?entityId=<ENTITY>&templateId=<id>` **with a sensible
default column set pre-seeded**:

| Template (zh) | `templateId` | Equivalent old report |
|---------------|--------------|-----------------------|
| 广告活动 (Campaign) | `campaign` | Campaign report |
| **推广的商品 (Advertised product)** | `advertised_product` | **SP "Advertised product" (adProducts)** |
| 搜索词 (Search term) | `searchterm` | Search term report |

Use **查看所有模板** (View all templates) for the full set.

> **Seeding is async and only happens via the template CLICK.** After
> clicking **使用模板**, the ~20+ default columns populate a second or
> two later — **wait for them** (poll for `删除`/Delete buttons in the
> column list). Navigating **directly** to
> `/reporting/new?...&templateId=advertised_product` by URL loads the
> page but does **NOT** seed columns — you'd submit an empty report and
> hit the validation error in §6.4. Always enter the builder by clicking
> the template on `/reporting`.

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/reporting?entityId=<ENTITY>")
wait_for_load()
# Click 使用模板 inside the 推广的商品 card (match the card that mentions
# 推广的商品 but not 搜索词):
js("""
  var btns=[...document.querySelectorAll('button,a')].filter(b=>b.textContent.trim()==='使用模板');
  for (var b of btns){ var p=b; for(var i=0;i<6&&p;i++){ p=p.parentElement;
    if(p && /推广的商品/.test(p.textContent) && !/搜索词/.test(p.textContent)){ b.click(); return; } } }
""")
wait_for_load()
import time; time.sleep(5)   # let default columns seed
print(js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='删除').length"))
PY
```

### 6.4 Building / customizing a report (the builder)

The builder (`/reporting/new`, title **创建报告**) is a single scrolling
form with five cards. The UI is Amazon's **storm-ui** framework — every
control carries a `data-takt-id="storm-ui-*"` and most have stable ids.

> **The 提交 (submit) button ignores synthetic `.click()`.** Verified
> live: `js("document.getElementById('urc_frb_create_report_button').click()")`
> silently no-ops — submit with a **real CDP click**
> (`click_at_xy(x,y)` from the element's `getBoundingClientRect`). Most
> other controls (the `使用模板` template button, date-range presets, the
> picker **保存**, column checkboxes) *do* respond to `js("el.click()")`
> in testing — but if any click appears to no-op, fall back to
> `click_at_xy`. Use `fill_input(sel, text)` / `type_text(...)` for text
> inputs.

**A. Report name** — input `id="report-name-form:report-name-control-component-0"`
(≤256 chars, auto-filled `推广的商品 - <timestamp>`). The field keeps its
auto-filled value unless you clear it, so to set a clean name **select-all
and delete (or empty the value) first**, then enter yours — don't assume
a single `fill_input`/`type_text` call replaces the existing text. A
unique, descriptive name makes the report easy to find later.

**B. 筛选条件 (Filters)** — four rows, each with an **添加/变更**
(Add/Change) button:

| Filter | Default | Change control (`id=`) | Notes |
|--------|---------|------------------------|-------|
| 账户 (Account) | current account (1) | 添加 button (`data-takt-id=storm-ui-button`) | Dialog: **添加** (single), **添加当前和未来账户** (a manager acct incl. future-linked), **添加所有显示的账户** (all shown) → **保存**. |
| 广告活动 (Campaigns) | 所有广告活动 (all with traffic) | `filter-form:campaigns-row-control-component-0-button` | Pick specific campaigns by name/id. |
| 国家/地区 (Country/Region) | 所有国家/地区 | `filter-form:campaign-country-row-control-component-0-button` | **See §6.8** — restrict here for a per-marketplace file, or add the 国家/地区 *dimension* and split downstream. |
| 广告产品 (Ad product) | 所有广告产品 | `filter-form:ad-product-row-control-component-0-button` | SP / SB / SD / STV / DSP. **Legacy SB (no ad groups) and self-service streaming-TV campaigns are NOT supported yet.** |

**C. 定制列 (Custom columns)** — the heart of the builder. Two tabs:
**尺寸** (Dimensions) and **指标** (Metrics), a category list on the
left, a **Search** box (type any field name), and checkbox rows
(`label[data-takt-id=storm-ui-checkbox]` — click to toggle). Chosen
columns appear as reorderable "Sortable Item" rows each with a **删除**
(Delete) button; drag to reorder.

**Minimum to submit** (enforced on 提交):
1. **≥1 time dimension** (维度 → 时间),
2. **≥1 detail dimension** (维度 → 详细程度: 广告主账户 / 广告活动 /
   广告组 / 广告), and
3. **≥1 metric** (指标).

Some fields grey out based on other choices (hover to see why, e.g.
"选择主 IMDb 广告展示量时此列不可用"). Empty cells for a dimension that
doesn't apply to a given ad product are **normal** — that's how one
report holds several products.

**Dimension categories** (维度 → tabs on the left): 时间 (Time), 详细
程度 (Detail level), 商品 (Product), 广告库存来源 (Inventory source),
交易 (Deals), 技术 (Tech), 地理位置 (Geography), 受众 (Audience), 投放
方案 (Targeting), 转化 (Conversion). Notable fields:

| Category | Key fields |
|----------|-----------|
| 时间 | 日期 (Date, → daily rows), 周, 月, 年份, 一周中的某一天, 小时, **日期范围** (Date range, → one start/end summary row) |
| 详细程度 | 广告主账户 / …ID / …名称, 实体编号, 广告组合(名称/编号), 广告活动(名称/编号/状态/预算/竞价方案/起止日期…), 广告组(名称/编号/状态/类型…), 广告(名称/ID/格式/尺寸/语言) |
| 商品 | **推广的商品** / 编号(=ASIN) / 名称 / 品牌 / 品类 / **SKU** / 站点; 达成转化的商品(编号/名称/品牌/品类); 商品相关度 |
| 广告库存来源 | 网站或应用程序, 广告位(名称/大小/分类) |
| 技术 | 操作系统, 浏览器名称/版本, 设备类型, 环境 |
| 地理位置 | 国家/地区, 国家/地区代码, 区域, 指定市场区域(DMA), 城市, 邮政编码 |
| 受众 | 细分受众群(ID/名称/类型/来源/国家/状态), 频次组 |
| 投放方案 | 投放目标, 目标竞价, 投放类型, 投放状态, 投放方案, 匹配类型, **搜索词**, 匹配的目标 |
| 转化 | 转化来源, 转化来源所有者, 转化定义/类型（亚马逊站外）, 归因类型 |
| 内容/直播 (STV/DSP) | 内容类型/评级/标题/创作者, 直播活动(编号/名称/广告位/广告时段…) |

**Metric categories** (指标 → tabs on the left): 投放 (Delivery), 成本
和费用 (Cost & fees), 触达 (Reach), 展示量份额 (Impression share), 互动
(Engagement), 亚马逊网站转化量 (On-Amazon conversions), 亚马逊设备转化量
(Device conversions), 亚马逊视频转化量 (Video conversions), 亚马逊站外
转化量 (Off-Amazon conversions), 已合并的转化量 (Combined conversions).
Notable fields:

| Category | Key fields |
|----------|-----------|
| 投放 | 展示量, 点击量, 点击率 (CTR), CPC, CPM, 可见展示量/可见率/vCPM, 无效展示/点击 |
| 成本和费用 | **成本** (= spend/花费), 总成本, 广告库存成本, 各类费用 (代理费/平台费/第三方费用 …) |
| 触达 | 用户触达量, 平均展示频率, 家庭触达量, 频次分布 (1…10+) |
| 展示量份额 | 展示量份额, 展示量份额排名, 搜索结果首页首位展示量份额 |
| 互动 | 视频四分位完成率/完播率/完整观看, 音频同类, 5 秒观看 |
| 亚马逊网站转化量 | **购买量, 销售额, 已售商品数量, 购买率, ROAS, 单次购买成本**; 商品详情页浏览量, 加入购物车, 加入心愿单, 品牌搜索量, 品牌旗舰店浏览, 评论页访问, 订购省, 长期销售 — each in base / **（推广）** / **（光环）** / **（品牌新客）** and 归因于点击 / 归因于浏览 variants |
| 亚马逊设备转化量 | 订阅注册, 应用商店启动/使用时长, Alexa 技能, Kindle KENP / KU 借阅 |
| 亚马逊视频转化量 | 加入播放列表, 下载视频播放, 租借, 预告片播放, 视频有效播放 |
| 亚马逊站外转化量 | 每 on-Amazon metric mirrored with the **（亚马逊站外）** qualifier + 潜在客户/结账/安装/页面浏览/搜索/注册/订阅 |
| 已合并的转化量 | 购买量/销售额/ROAS **（已合并）** = on-Amazon + off-Amazon combined |

**D. 报告期 (Report period)** — control
`id="report-range-form:date-range-row-control-component-0"` (default
**最近 7 天**). Opens a preset list (`data-takt-id=storm-ui-date-range-picker-preset-selector`)
**and** a dual-calendar for custom ranges. Presets: 今天, 昨天, 最近 7
天, 本周, 上周, 最近 30 天, 本月, **上个月**, 最近 90 天, 本季度, 上个
季度, 今年, 去年.

> **A preset auto-applies — do NOT click 保存 after one.** Verified live:
> clicking a preset (e.g. **上个月**) closes the picker and applies the
> range immediately; the **保存**
> (`data-takt-id=storm-ui-date-picker-confirmation-control-save`) button
> is **only present for a custom calendar start→end selection** — after a
> preset it isn't in the DOM, so a `querySelector(...).click()` on it
> returns null / throws. After clicking a preset, just verify the control
> label changed (e.g. reads `上个月`). Click **保存 only** when you picked
> custom calendar dates.

Lookback limits: **daily/weekly up to 15 months; monthly/yearly up to 6
years.** Max lookback varies per dimension/metric — some are shorter.
Presets that exceed a selected dimension's limit are hidden.

```bash
# Set period = last calendar month (for the monthly export)
browser-use <<'PY'
js("document.getElementById('report-range-form:date-range-row-control-component-0').click()")
import time; time.sleep(2)
js("[...document.querySelectorAll('[data-takt-id=storm-ui-date-range-picker-preset-selector]')].find(e=>e.textContent.trim()==='上个月').click()")
# A preset auto-applies — do NOT click 保存 (it isn't in the DOM after a
# preset). Just confirm the control label now reads 上个月:
import time; time.sleep(1)
print(js("document.getElementById('report-range-form:date-range-row-control-component-0').textContent"))
PY
```

**E. 投放设置 (Delivery)** —
- **发送至** (Send to): optional email(s), input `id=recipients-input-builder-input`.
  Anyone with the emailed link can view the report (no Amazon account
  needed).
- **报告提取频率** (Frequency): dropdown
  (`data-takt-id=storm-ui-dropdown-trigger-button`; items
  `storm-ui-dropdown-item`) = **一次** (Once, default) / 每日 / 每周 /
  每月. Recurring → pick weekday (weekly) or day-of-month (monthly), an
  **end date**, and a **time + timezone**.
- **文件格式** (File format): **CSV**. (Note: unified reporting delivers
  **CSV**, whereas the old SP Advertised Product report was **XLSX** —
  see §6.6.)

**F. Submit** — **提交** button `id=urc_frb_create_report_button`
(top-right; **real click**, not `.click()`). Cancel:
`id=urc_cancel_subscription_button`. On success the builder closes and
you return to `/reporting` with the new row in 正在处理 (Processing).
If required columns are missing you get an inline banner *"Fix the
following issues and submit again"* listing the missing time/detail/metric.

### 6.5 Standardized metrics & terminology — READ BEFORE trusting numbers

Unified Reporting **renamed and redefined** metrics. The underlying
measurement logic is unchanged, but display names — and some values —
differ from the old reports. Key changes:

- **Common terms unified:** (DSP → unified) 订单→广告活动, 广告订单项→
  广告组, 创意→广告, 点击跳转量→点击量.
- **Base conversion metrics now include halo.** 购买量 / 销售额 / 已售
  商品数量 (and 商品详情页浏览量 etc.) now count **both** promoted
  products **and** brand-halo (品牌光环) products. To reproduce the old
  SP "promoted-only" numbers, use the **（推广）** variants
  (推广商品的购买量 / 推广商品的销量 / 已售商品数量（推广） / 推广商品的
  ROAS). DSP users especially: dropping the old "总" prefix means base
  metrics can return **higher** values than the legacy DSP report.
- **Lookback window removed from names.** Base metrics automatically use
  the correct window: **Sponsored Products seller = 7-day**, vendor =
  14-day; other campaign types = 14-day (flexible-lookback DSP uses the
  advertiser's chosen click/view windows). So 销售额 = the old "7-day
  total sales" for an SP seller; ROAS = ROAS7d.
- **Traffic-date reporting.** All conversions are now reported on the
  **traffic date** (the ad-interaction date), not the conversion date
  (DSP/legacy SB used to report on conversion date). Expect higher
  conversions early in a campaign's run vs the old reports.
- **Off-Amazon** conversions all carry the **（亚马逊站外）** qualifier.
- Qualifiers you'll see: **（推广）** promoted, **（光环）/（品牌光环）**
  halo, **（品牌新客）** brand-new-customer, **归因于点击/归因于浏览**
  click-/view-attributed, **（已合并）** on+off-Amazon combined.

### 6.6 Reproducing the old SP "Advertised product" report (monthly export)

The monthly `下载上月数据报表` schedule needs a per-marketplace
"Sponsored Products Advertised product" report. In Unified Reporting
that is the **推广的商品** template (`advertised_product`) filtered to
**广告产品 = 商品推广 (SP)** and one marketplace. The old XLSX had 24
columns; here is the mapping to unified dimensions/metrics (SP seller,
7-day window):

| Old column (XLSX) | Unified dimension / metric |
|-------------------|----------------------------|
| 开始日期 / 结束日期 | dim **日期范围** (summary) — or **日期** for daily rows |
| 广告组合名称 | dim 广告组合名称 |
| 货币 | dim 广告活动货币代码 (or infer from 国家/地区) |
| 广告活动名称 | dim 广告活动名称 |
| 广告组名称 | dim 广告组名称 |
| 国家/地区 | dim 国家/地区 |
| 广告SKU | dim 推广的商品 **SKU** |
| 广告ASIN | dim 推广的商品 (编号 = ASIN) |
| 展示量 | metric 展示量 |
| 点击量 | metric 点击量 |
| 点击率 (CTR) | metric 点击率 |
| 单次点击成本 (CPC) | metric CPC |
| 花费 | metric **成本** |
| 7天总销售额 | metric **推广商品的销量** (promoted sales) |
| ACOS 总计 | derive (成本 / 推广商品的销量) — or an ACOS metric |
| 总 ROAS | metric **推广商品的 ROAS** |
| 7天总订单数(#) | metric **推广商品的购买量** |
| 7天总销售量(#) | metric **已售商品数量（推广）** |
| 7天的转化率 | metric 购买率（推广的商品） |
| 7天内广告SKU销售量(#) | metric 已售商品数量（推广） |
| 7天内其他SKU销售量(#) | metric 已售商品数量（光环） |
| 7天内广告SKU销售额 | metric 推广商品的销量 |
| 7天内其他SKU销售额 | metric 销售额（光环） |

**Column order and exact header text will differ** from the legacy
XLSX (Amazon standardized the names), and the **file is CSV, not XLSX**.
Downstream profit analysis keys on SKU/ASIN + spend + sales + units —
which all map above — but if a consumer parses by exact legacy header
string or `.xlsx` extension, it must be updated for the unified CSV. The
data (per SKU × campaign × country: spend, sales, units, impressions,
clicks) is fully reproducible.

**Verified live** (one SP marketplace, last-month period): the
`advertised_product` template seeds ~47 default columns — including
`推广的商品编号` (=ASIN), `推广的商品 SKU`, `广告活动名称`, `广告组名称`,
`广告组合名称`, `预算货币`, `展示量`, `点击量`, `总成本`, and the base /
`（推广）` / `（光环）` / `（品牌新客）` conversion families — and returns
one row per SKU × campaign. Two things to know:
- The default set uses **`推广的商品站点`** (Advertised product
  marketplace, values `AMAZON_SA`/`AMAZON_AE`/`AMAZON_AU`), **not**
  `国家/地区`. The report is **account-wide** — it contains every
  marketplace, and the country filter does NOT scope it (see §6.8). Split
  per-marketplace **downstream** on `推广的商品站点` (or add the
  **国家/地区** dimension, §6.4C, and split on that).
- The **`日期范围`** dimension shows each row's *active* date span
  **within** the selected period (rows with activity across the whole
  month show `<1st> - <last>`; sparser SKUs show a sub-span). The report
  period itself is still the one you picked (e.g. 上个月 = the full
  previous calendar month).

Example placeholder row (never use real SKUs/ASINs/campaign names):
`WIDGET-001-White, B0EXAMPLE001, "example manual - KSA", SAR, 沙特阿拉伯,
5000 impressions, 30 clicks, 30.00 spend, 250.00 sales, 6 units`.

### 6.7 Retrieving a finished report

After 提交 the report runs once automatically. Poll `/reporting` (reload
and read **报告状态**) until it flips 正在处理 → **已完成** (Completed).
Do not sleep >270 s at a stretch (the store session recycles at ~8 min
idle); reload on a ~60–90 s cadence. Reports typically finish in a few
minutes to ~30 min depending on data volume; the migration guide warns
data can lag and be revised within the attribution window.

When **已完成**, get the download from the **report detail page**, NOT
the list-row action menu. The row's **操作** button (`id=report-menu-trigger`)
menu only has 复制 (Copy) / 删除 (Delete) / edit — **no download**.
Instead click the **report name** link → detail page
`/reporting/history?entityId=<ENTITY>&reportId=<report-uuid>`, which has
a **下载** (Download) link (`data-takt-id=storm-ui-link`). Its href is:

```
/reporting/subscriptions/<report-uuid>/download-report/<run-uuid>
```

Trigger the real anchor (JS `.click()` on the `<a>`, or `browser-use`
open its `href`; a bare label click fires no download). The CSV lands in
the store's download dir (`~/.vibe-seller/downloads/<slug>/`); glob the
newest `*.csv` (Amazon names it after the report — e.g.
`<report-name>.csv`), then `mv`/rename per the task's convention. If you
set an email recipient, the CSV also arrives by email link.

### 6.8 Per-marketplace: split downstream (the filter does NOT scope)

Each marketplace has its **own campaigns and its own currency** (sa=SAR,
ae=AED, au=AUD, …). **Unified Reporting is account-wide, not
per-marketplace**, and — unlike the old per-TLD `/reports` console —
there is **no reliable way to get a single-marketplace file from the
builder**:

> **⚠️ Verified live (twice, identical MD5): neither the 国家/地区 filter
> nor the marketplace `entityId` scopes the report.** Entering via a
> marketplace's `entityId` (e.g. the AE entity) still returns **all**
> marketplaces, and the **筛选条件 → 国家/地区** storm-ui dialog
> (全部清除 → tick one country → 保存) does **not** commit to the backend
> — the CSV still contains every marketplace. A report created for "SA"
> and one created with an "AE-only" filter came out byte-identical.

**So: produce ONE account-wide report and split it downstream by
marketplace.** Add/keep the **`推广的商品站点`** (Advertised product
marketplace) column — its values are `AMAZON_SA` / `AMAZON_AE` /
`AMAZON_AU` — (or add the **国家/地区** dimension) and partition the rows
into each country's folder yourself:

```python
import pandas as pd
df = pd.read_csv('unified_advertised_product.csv')
col = [c for c in df.columns if '站点' in c or 'marketplace' in c.lower()][0]
for mkt, sub in df.groupby(col):        # AMAZON_SA / AMAZON_AE / AMAZON_AU
    cc = mkt.split('_')[-1].lower()      # sa / ae / au
    sub.to_csv(f'reports_{{MM}}_{cc}_{{slug}}/Sponsored_Products_Advertised_product_report_{{Mon}}.csv', index=False)
```

**Verify content, not just that a file exists:** after splitting, confirm
each per-country file's `推广的商品站点` (or `国家/地区`) values are all
the intended marketplace. Never hand-copy one marketplace's rows into
another country's folder — the currency and campaigns differ.

### 6.9 Limits & troubleshooting

- **Report > 25 GB times out.** Shorten the date range, keep advertisers
  < 50, or select fewer dimensions.
- **Empty fields / empty report are normal / success**, not failure — an
  inapplicable dimension, an unsupported historical lookback, or a
  permission-masked metric all yield blank cells. A 0-row export for a
  valid request is a completed deliverable (see the applicability rule
  near the top of this skill).
- **Account not visible?** You need access to the manager/advertiser
  account; newly-linked accounts can take up to ~1 hour to appear.
- **Not yet in unified reporting** (planned late-2026): MRC viewability,
  benchmarks, conversion path, SP video, SP prompts, cross-retailer SP,
  and legacy-SB targeting/search-term support. For those, use §6.10 until
  cutoff.

### 6.10 Legacy Sponsored-ads console (retiring 2026-12-31)

Only for reports not yet in unified reporting, or stores mid-migration —
and only until the cutoff. The old console is at
`advertising.amazon.{tld}/reports` (note: `/reports`, not `/reporting`).
Its create dialog used `#advertising-reports button` → category/type
controls (`button[id*=report-type-control]`, types `searchTerms`,
`keywords`, `adProducts`, `campaigns`, `placements`, `purchasedProducts`,
…), a `input[name=time-units]` Summary/Daily radio, and
`#urc_run_subscription_button` to run; the list page linked to a detail
page whose history table exposed the **Download** link. Full legacy
selectors are in this file's git history (pre-unified-reporting revision)
if a mid-migration store still needs them. **Do not build new workflows
on `/reports` — it stops accepting new reports on 2026-12-17.**

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
| Advertising Reports (Unified Reporting) | CSV | report row **已完成** → **下载** link (real `<a>` click) → direct | Internal (`/reporting`) |

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
│   ├── Advertising Reports     → advertising.amazon.{tld}/reporting (Unified; old /reports retiring 2026-12-31)
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
    └── 效果衡量和报告 (Measurement & reporting) → 报告 (Unified Reporting)
```

## Tips

- **Always dispatch `mouseover`** on hamburger menu categories, never a click
- **Check `aria-expanded`** in `page_info()` output to confirm the submenu is revealed
- **For instant reports** (Business, Tax Library): click Download/View directly
- **For generated reports** (Fulfillment, Custom, Payments): Request → poll
  status → Download when ready. Typically **1-5 minutes**.
- **For ad reports** (Unified Reporting, §6): 使用模板/创建报告 → 提交 →
  poll 报告状态 until **已完成** (few min – 30+ min) → **下载**
- **Always copy downloads** from the browser download dir to task workspace
- Use **direct URLs** when possible to skip hamburger menu navigation
- The **hamburger menu button** is inside a `navigation-hamburger-menu` shadow
  DOM element — look for `role=button` inside it
- **Payment CSV**: skip first 7 lines (definitions) when parsing
- **Ad report CSV**: no header lines to skip, starts with column row
- When building analysis scripts, use the column structures documented above
