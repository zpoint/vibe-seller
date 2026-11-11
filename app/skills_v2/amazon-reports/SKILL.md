---
name: amazon-reports
description: "Navigates and exports Amazon Seller Central and Amazon Ads reports: Business Reports, Fulfillment/FBA reports (storage fees, customer returns, inventory), Tax Document Library invoices, listing reports, the Payments Reports Repository, and Unified Reporting ad reports. Covers URLs, kat-* date and download controls, download-folder handling, the monthly report-run file layout, and CSV structures. Use when a task downloads, exports or analyses any Amazon Seller Central or advertising report."
requires: [amazon-shared]
---

# Amazon Seller Central — reports

Load the `amazon-shared` skill first (Skill tool) for the marketplace/TLD
map, sign-in and Ziniao handling, and switching marketplaces. Browser
mechanics (heredoc helpers, the 120 s per-invocation limit, DOM vs
screenshots) are in the `browser-harness` skill.

## Report index

| Report | URL (Seller Central unless noted) | Export | Typical wait | Details |
|---|---|---|---|---|
| Business Reports | `/business-reports` | CSV, instant | — | [seller-central-reports.md](references/seller-central-reports.md#business-reports) |
| Fulfillment / Return / inventory reports | `/reportcentral/WelcomePage` | Request → CSV/TSV | 1-5 min | [fulfillment-reports.md](references/fulfillment-reports.md) |
| FBA Customer Returns | `/reportcentral/CUSTOMER_RETURNS/1` | Request → CSV | 1-5 min | [fulfillment-reports.md](references/fulfillment-reports.md#fba-customer-returns-exact-dates) |
| Monthly Storage Fees | Report Central → Fees | Request → CSV | ~30 s once published | [fulfillment-reports.md](references/fulfillment-reports.md#monthly-storage-fees-publication-latency) |
| Inventory Reports | `/inventory-reports` | Request → download | 1-5 min | — |
| Listing reports (Active / All Listings) | `/listing/reports` | Request → TXT | 1-5 min | [seller-central-reports.md](references/seller-central-reports.md#listing-reports-custom-reports) |
| Tax Document Library | `/tax/seller-fee-invoices` | PDF (some CSV), instant | — | [seller-central-reports.md](references/seller-central-reports.md#tax-document-library) |
| Manage Taxes | `/tax/tax-settings` | settings page | — | — |
| Selling Economics & Fees | `/selling-economics-and-fees` | dashboard | — | — |
| Payments Reports Repository | `/payments/reports-repository` | Request → CSV | 1-3 min | [payments-repository.md](references/payments-repository.md) |
| Ad reports (all ad products) | `advertising.amazon.{tld}/reporting` | Build → CSV | minutes to 30+ min | [ads-unified-reporting.md](references/ads-unified-reporting.md) |

Full URL: `https://sellercentral.amazon.{tld}<path>`; the `{tld}` per
marketplace is in the `amazon-shared` skill.

## Navigation

Open report pages by direct URL with `new_tab(url)` — it is faster and
doesn't depend on which UI generation the account has. Use the menus only
to discover a page whose URL you don't know or that 404s:

- **New Seller Central (NGS):** the drawer `div#ngstrim-nav-menu`, opened
  by `button[aria-label="Menu"]`, holds plain anchors with real hrefs —
  read the href and `new_tab()` it.
- **Classic UI:** the `navigation-hamburger-menu` web component (its
  `[role=button]` is inside its shadow root). Categories reveal their
  submenu on **`mouseover`** — dispatch
  `new MouseEvent('mouseover', {bubbles: true})` on the category (its
  `aria-expanded` flips to true); clicking the category navigates away
  instead. Then click the submenu item.

The `amazon-shared` skill has the full menu recipes.

## Reuse before you request

Every report page keeps a history table (date range covered, status,
Download). If a completed row already covers the requested range, download
it instead of generating a new one. Request a new report only when none
matches.

## Outcomes: downloaded, N/A, pending, failed

Store metadata (`platforms` / `countries`) says where the store sells, not
whether it uses FBA or has an advertiser account. Decide applicability on
the page:

- **Downloaded** — including a valid request that exports zero data rows.
  Amazon generates an ad report even with zero campaigns; that empty file is
  a completed deliverable.
- **N/A** — only when the page proves the capability is absent: an
  advertiser onboarding/registration flow *after* warming up the ad console
  (a cold-URL marketing page proves nothing), or an explicit "not enrolled
  in FBA". Record it with `vibe_seller_set_task_result`.
- **Pending** — Amazon hasn't published the month yet (Monthly Storage Fees
  early in M+1). Report it as pending with `vibe_seller_set_task_result`.
- **Failed** — the page shows you should be able to get it but you couldn't
  (dead button, 0-byte download, error page). Only this case warrants
  `vibe_seller_set_task_error`.

State per report which of these it is, and don't turn "I didn't check" into
"N/A".

## Clicking Download / Request buttons (`kat-*` shadow DOM)

Report tables wrap the control as `kat-table-row → kat-table-cell →
kat-button`, and the real `<button>` is inside `kat-button.shadowRoot`.
CSS selectors don't cross shadow boundaries, so
`querySelector('kat-table-cell button')` and similar guesses return `null`.
Reach the inner button through the host and `.click()` it. Match the target
row by a substring of its date range:

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
return 'NO download control for ~' + rowMatch;
"""))
import time; time.sleep(3)   # let the download start
PY
```

The same host → inner-button walk works for any `kat-button` ("Request
Report", "Download CSV") — match its `label`/text instead of a date. If it
finds nothing, check `page_info()` for a plain Download link, or locate the
control's coordinates and `click_at_xy`. The left-sidebar `kat-button`s are
the exception: those need `click_at_xy`.

## Date ranges

Date controls differ per marketplace and per report page: native
`<select>` presets, `kat-dropdown` presets, plain `<input>`s,
`kat-date-picker` / `kat-date-range-picker` shadow widgets, month/year
dropdowns. Identify the widget from the DOM before acting, then:

- **Native `<select>`** takes a value-set (`el.value='…'` plus a `change`
  event).
- **`kat-dropdown`** doesn't — `selectOption()` changes `.value` without
  firing the component's handlers. Click it open with `click_at_xy`, read
  the `kat-option` boxes from the dropdown's **own `shadowRoot`**, and
  `click_at_xy` the option. Example in
  [payments-repository.md](references/payments-repository.md#pick-a-calendar-month-with-the-month-radio).
  Month dropdowns have been seen 0-indexed (Jan=0); confirm against the
  visible label.
- **`kat-date-*` calendars** only commit real clicks; programmatic value
  sets update the display but the report still runs for today. Walk the
  shadow roots to get cell coordinates and click them — see
  [the calendar walk](references/fulfillment-reports.md#the-calendar-walk-three-shadow-roots).
  Because the calendar fills the field, the display date format doesn't
  matter.
- **Plain light-DOM `<input>`s** may accept `fill_input(selector, date)`,
  but verify as below.
- Prefer a mode that avoids the calendar when the page offers one (the
  Payments `Month` radio).

**Verify the outcome, not the widget.** After requesting, confirm the new
history row's **Date Range Covered** is your target range. A today-dated
row means the date never committed: redo it with real clicks and don't
download the wrong file. A preset like "last 30 days" is anchored to today
and is not a calendar month.

## Waiting for a report

Each `browser-use` invocation is killed at 120 s, so poll across
invocations: one check (reload or `page_info()`, then look for the
Ready/Download state) per call, every 30-90 s, driven from the shell. A
single long sleep inside one heredoc gets the call killed and counts as a
browser wedge.

## Downloads

Downloads land in the store's downloads dir,
`~/.vibe-seller/downloads/<slug>/`, which is shared by every task on the
store and keeps old files. Nothing reaches the task directory on its own.

**Move each file out the moment it lands, under a name unique to its
marketplace and period, before you trigger the next download.** Several
reports download under names that carry no marketplace
(`2026JulMonthlyTransaction.csv`, `storage.csv`,
`All+Listings+Report_MM-DD-YYYY.txt`), so a second marketplace's download
silently overwrites the first, and if that second download fails, `ls -lt`
still shows a file with the expected name — the wrong marketplace's.
Moving (not copying) leaves nothing behind to be picked up by mistake.

```bash
DL=~/.vibe-seller/downloads/<slug>
ls -lt "$DL" | head          # identify the file you just downloaded
mkdir -p reports_07_<cc1>_<slug>
mv "$DL/2026JulMonthlyTransaction.csv" reports_07_<cc1>_<slug>/
# only now switch marketplace and download the next one
```

Pick only the files you just downloaded; a ` (1)`, ` (2)` suffix is a
browser re-download — take the newest and drop the suffix when renaming.

Deliverables go in the task directory (your CWD). If later runs will reuse
the data, also copy it to `store-data/<slug>/reports/<YYYY-MM>/`. Don't use
`/tmp`.

### Check which marketplace a file holds

Run this on every per-marketplace file before calling it done. It finds the
header row by shape (these exports carry preamble lines), reads the
marketplace column by name, and hashes the file:

```bash
python3 - reports_07_<cc1>_<slug>/2026JulMonthlyTransaction.csv \
          reports_07_<cc2>_<slug>/2026JulMonthlyTransaction.csv <<'PY'
import csv, hashlib, sys
for path in sys.argv[1:]:
    digest = hashlib.md5()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    with open(path, newline='', encoding='utf-8-sig') as fh:
        rows = csv.reader(fh)
        header = next(r for r in rows if len(r) > 5)
        cols = [c.strip().lower() for c in header]
        name = 'marketplace' if 'marketplace' in cols else 'country_code'
        i = cols.index(name)
        seen = {r[i].strip().lower() for r in rows if len(r) > i and r[i].strip()}
    print(f'{path}\n  md5={digest.hexdigest()}\n  {name}={sorted(seen)}')
PY
```

- The file's values must include the marketplace you're filing it under. If
  they don't, you're holding the other marketplace's export: redo it.
- **Transaction CSV** (`marketplace` column) is strictly per-marketplace —
  exactly one value — so two marketplaces with identical hashes means one
  export never happened. Redo it.
- **`storage.csv`** (`country_code`) can be account-level on a
  multi-marketplace account: the same file, with both codes, in both
  folders is legitimate. Judge it by its codes, not its hash.
- If a marketplace's export can't be produced, leave its folder without
  that file and say so. Never fill the gap with another marketplace's file —
  an absent file is visible and recoverable; a substituted one looks like
  real data downstream.

## Monthly report-download runs

Two different tasks land on this page: the monthly download — the full
month-end set for this store — and a lookup, which wants one report or one
number. Only you can tell which one this task is, so say it: declare
(`vibe_seller_declare_report_run`, with the month `YYYY-MM`) when, and
only when, the task IS the monthly download. Do not declare a lookup: one
report ("last month's transaction report for AE"), a question about
numbers you already have, a partial fetch, a retry that pulls the single
file that failed. A declared run is held to the whole set, so declaring
one for a lookup gets you refused for files nobody asked you for.

Declaration is what makes the check exist at all. A declared run's result
is compared against an expected file list derived from the store's
declared capabilities and the calendar (never from what you produced), and
the tool answers with that list, so you know what you owe. An undeclared
task is not looked at: filing CSVs under a `reports_*` folder is not a
declaration and used to be read as one.

For the declared reporting month `M` — the previous calendar month for an
ordinary run, or the month the user named for a back-fill (`MM` = its
two-digit number, `Mon` = `Jan`…`Dec`), per Amazon country `<cc>`
(lowercase):

| File | Notes |
|---|---|
| `reports_<MM>_<cc>_<slug>/<YYYY><Mon>MonthlyTransaction.csv` | Payments transaction report; must have data rows |
| `reports_<MM>_<cc>_<slug>/Sponsored_Products_Advertised_product_report_<Mon>.csv` | Unified Reporting split per marketplace; zero rows is fine; not expected if the store declares no ads |
| `reports_<MM>_<cc>_<slug>/return.csv` | FBA customer returns; zero rows is fine; not expected if the store declares no FBA |
| `reports_<MM-1>_<cc>_<slug>/storage.csv` | Storage fees for the month before `M` (fee reports publish late); needs rows; counted as pending, not missing, before day 15 of the following month |

A header-only file counts as missing data for files that need rows. Gaps
come back once for you to fix; after that they are recorded as caveats on
a completed run, so report honestly what you got and what you didn't.

## CSV notes

- Payments transaction CSV: skip the first 7 preamble lines; columns in
  [payments-repository.md](references/payments-repository.md#transaction-report-csv-structure).
- Unified Reporting CSV: no preamble; first row is the header.
- FBA returns CSV: `return-date` is UTC while the date filter is
  marketplace-local — late-evening UTC rows on the previous month's last
  day belong to the requested month.
