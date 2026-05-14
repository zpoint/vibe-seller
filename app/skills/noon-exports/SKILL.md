---
name: noon-exports
description: "Noon data exports — Sales report (per country), Transaction View (multi-contract CSV with status enum gotchas), Catalog Exports (Content / Pricing / Stock / Reports). Load when downloading sales, finance, transaction, or catalog data from noon."
---

# Noon — Data Exports

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, and the per-store download directory.

Three export surfaces, each with its own quirks: Sales (per
country), Transaction View (multi-contract, long polling, status
enum), Catalog Exports (typed templates).

## 1. Sales Export (Per Country)

**URL**: `https://reports.noon.partners/en/sales/?project=PRJ{project_id}`

### Country Switching

Use the **"My Stores" dropdown at bottom-left**, NOT the "Destination"
filter in the toolbar. Each store shows a flag icon.

### Date Range for Last Month

```bash
browser-use input <start-date> "01 Mar 2026"
browser-use input <end-date> "31 Mar 2026"
# Calendar popup opens; click the end date cell to confirm:
browser-use state                     # find td title="2026-03-31"
browser-use click <date-cell>
```

URL reflects dates: `?from_date=2026-03-01&to_date=2026-03-31`.

### Export Flow (Verified)

1. Click Export button → modal appears
2. Modal shows previous export file with download link (if any)
3. Click "New export" to regenerate
4. Wait ~10-30s for "Exporting" spinner to finish
5. Download link appears with filename:
   `sales_export_{date}_{time}_{project}_{country}_{seq}.csv`
6. Click download link
7. Close modal

### Both Countries

After SA export, close modal, switch "My Stores" to AE, repeat.

## 2. Transaction View Export

**URL**: `https://noon-payments.noon.partners/en/transaction-view?project=PRJ{project_id}`

Left nav: Statements, SOA, Transaction View, Invoices & Creditnotes,
Legacy Exports.

### Critical facts (read before acting)

1. **One unfiltered export covers every contract in the project.**
   A single Transaction View export for a project that has multiple
   country contracts produces **one CSV with rows for every
   contract** — identifiable by the `Contract` / `Contract Title`
   columns (e.g. `MPXXXXXXXXSA,Noon SA` and `MPXXXXXXXXAE,Noon
   AE`). See "Transaction CSV Structure" below. **Do not run one
   export per country** — run one, then split locally with pandas /
   awk. To limit the export to a single country, filter the
   Contracts dropdown to that country's contract *before* clicking
   Download.
2. **Status enum is `Requested → Exporting → Processed`** (not
   "Processing → Completed"). When the panel row reads `Processed`,
   click the **Export** button on the right of that row — that is
   what actually writes the CSV. The first Download click only
   queues the export job.
3. **Exports can take several minutes**, occasionally >5. Do not
   re-click Download while a previous export is still `Exporting` —
   each click queues a *new* `EXP{…}` job that competes for the
   same backend and usually extends the wait.
4. **Download location depends on the browser backend**, not on
   noon. The file is not guaranteed to land in `~/Downloads`. See
   "Finding the downloaded file" below.
5. **`No data` disables the Download button.** Date ranges with no
   activity show a greyed-out button and no panel appears — that's
   a no-op, not a bug. Skip that range.

### Country switching on Transaction View

The top-of-page **flag widget** (little arrow next to the country
flag) is the country switcher — not the Contracts dropdown. The
Contracts dropdown lists only contracts for the *currently selected
country*, so after switching flag the dropdown repopulates.

But since unfiltered Download already covers all contracts, you
usually don't need to switch country at all.

### Download flow

```bash
# 1. Navigate.
browser-use open "https://noon-payments.noon.partners/en/transaction-view?project=PRJ{project_id}"
sleep 2 && browser-use state

# 2. Set date range (Start / End date inputs). Confirm via state.
browser-use input <start-date-input> "YYYY-MM-DD"
browser-use input <end-date-input>   "YYYY-MM-DD"
# Optional: filter Contracts dropdown to a single country's contract.

# 3. Click Download ONCE. NEVER re-click — each click queues a
#    new export job on Noon's backend, backing up the queue and
#    making every subsequent attempt take longer.
browser-use click <download-btn>

# 4. Poll for Processed status. Exports can take up to 35 min
#    (data-heavy months are slower). Keep waiting — do NOT
#    re-click Download, do NOT close the panel.
for i in $(seq 1 70); do
  sleep 30
  browser-use state | grep -q "Processed" && break
done

# 5. Click the Export button on the panel row (labelled "Export"
#    with a download icon, right of the export code). This is the
#    click that actually writes the file.
browser-use click <export-btn>
```

### API fallback (if page refreshes or panel disappears)

If the browser session disconnects or the page refreshes during
the long wait, do NOT click Download again — the export job is
still running on Noon's backend.  Instead, query the status API
directly and download via curl:

```bash
# Poll the status API for the export code you recorded earlier.
browser-use eval "
var xhr = new XMLHttpRequest();
xhr.open('POST', '/_svc/mp-partner-impex-api/export/status', false);
xhr.setRequestHeader('Content-Type', 'application/json');
xhr.send(JSON.stringify({exportCode: '<EXP_CODE>'}));
xhr.responseText;
"
# Response: {"export": {"status_code": "COMPLETE",
#   "download_url": "https://storage.googleapis.com/..."}}

# Once status_code is COMPLETE, download via curl:
curl -sL -o transaction_view.csv "<download_url>"
```

This avoids re-triggering the export and lets you recover a
completed export even after a browser disconnect.

### Finding the downloaded file

Downloads go to `~/.vibe-seller/downloads/<store-slug>/`.
This is a stable per-store directory managed by the CDP proxy
(it overrides browser-use's random temp dirs).

File names for Transaction View exports start with
`noon_financeweb_transactionviewreportonitemlevelwithcontractselection`
and end with `.csv`. Repeat downloads get ` (1)`, ` (2)` suffixes —
always take the newest by `mtime`, then copy into the task
workspace (dropping the `(N)` suffix). See the amazon-reports
skill's "Download Behavior" section for the general pattern.

### Transaction CSV Structure

Header columns (in order):

```csv
Contract,Contract Title,Reference Nr,Order Nr,Item Nr,Order Date,
Transaction Date,Title,SKUs,Partner SKUs,Transaction Type,Currency,
Net Proceeds,Referral Fee including VAT,
Fullfilment & Logistics Fees including VAT,  ← sic, Noon misspells this
Shipping Credits including VAT,Other Order Fees including VAT,
Order Subsidies including VAT,Non-Order Fees including VAT,
Non-Order Subsidies including VAT,Others including VAT,Total
```

- **`Contract`** is the contract code — 12 chars, `MP` prefix,
  country-code suffix (e.g. `MPXXXXXXXXSA`, `MPXXXXXXXXAE`,
  `MPXXXXXXXXEG`). Use the last 2 chars to bucket by country.
- **`Contract Title`** is the human-readable name (`Noon SA`,
  `Noon AE`, …).
- Use either column to split a mixed-country export locally:

```python
import pandas as pd

df = pd.read_csv('<exported-file>.csv')
for cc in df['Contract'].str[-2:].unique():
    df[df['Contract'].str.endswith(cc)].to_csv(
        f'transactions_{cc}.csv', index=False
    )
```

Observed `Transaction Type` values include `order`, `order_update`,
`statement_fee`, `payment`, `balance_transfer`. Noon may add more;
filter liberally when aggregating.

## 3. Catalog Exports

**URL**: `https://noon-catalog.noon.partners/en/exports?project=PRJ{project_id}`

Click "Add Export" → modal with Type dropdown:

| Type | Description |
|------|-------------|
| Catalog Export | Full catalog data |
| Content | Product titles, descriptions, images, attributes |
| Pricing | Current + promotional prices |
| Stock | Inventory levels |
| Partner SKU Generate | Generate partner SKU mappings |
| Reports | Performance/sales report data |
| Global Catalog Export | Global catalog across markets |

Select type → Create → wait → download from Result column when
Status shows "Completed" (1-10 minutes depending on size).

## Tips

- **Transaction View export covers all contracts by default** —
  one click produces one CSV with both SA and AE rows. Don't
  export twice (see § 2).
- **Transaction View status enum is `Requested → Exporting →
  Processed`**, not "Processing → Completed". Click the Export
  button on the panel only after it reads `Processed`.
- **Exports can take up to 35 min** — data-heavy months are
  slower. Poll for at least 35 min before giving up. NEVER click
  Download again while a previous export is still processing — it
  queues a new job and makes everything slower.
- **Export files land in `~/.vibe-seller/downloads/<store-slug>/`**,
  not `~/Downloads`.

## See also

- `noon-shared` — login, page structure (prerequisite)
- `amazon-reports` — Amazon equivalent + general "Download Behavior" pattern
