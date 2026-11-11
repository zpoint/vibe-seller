---
name: noon-exports
description: "Noon data exports — Sales report (per country), Transaction View (one multi-contract CSV, long-running job, two Export buttons), Catalog Exports (Content / Pricing / Stock / Reports). Load when downloading sales, finance, transaction, or catalog data from noon."
requires: [noon-shared]
---

# Noon — Data Exports

Load the `noon-shared` skill with the Skill tool first; it covers login,
page structure and the store (country) switcher.

Three export surfaces: Sales (per country), Transaction View (one CSV for
every contract, slow), Catalog Exports (typed templates).

## Done when

The requested files exist with a header and data rows, covering every
requested country / contract / date window. An export left at
Requested/Exporting with no file written, or a multi-country request whose
Transaction View `Contract` column covers only one country, is a gap.

## Where files go

Downloads land in `~/.vibe-seller/downloads/<slug>/` (managed by the CDP
proxy, not `~/Downloads`). Move each file into the task workspace as soon
as it arrives, under a name that says what it is — repeat downloads only
get ` (1)` suffixes. Declare a run with `vibe_seller_declare_report_run`
(month `YYYY-MM`) before fetching, and only when the task is the monthly
download — the full month-end set. One export ("the transaction view for
last month", a single country's sales CSV) is a lookup, not a run: a
declared run owes every file in the month's set, so declaring a lookup
gets you refused for exports nobody asked for. Only a declared run is
checked at `set_task_result`, and the declaration answers with the list it
will be checked against. For a declared run the expected layout is
`reports_noon_<MM>_<slug>/noon_financeweb_transactionviewreport.csv`
(one file for all countries). Keep copies you want to reuse across runs
under `store-data/<slug>/exports/<YYYY-MM>/`.

## Choosing the country

Sales, Transaction View and Catalog are `/en/` portals: the URL is the
same for every country, and the market shown is whatever the **sidebar
store switcher** is set to. It persists across sessions, so read the flag
before trusting a page and switch by matching the entry's flag, as
described in the `noon-shared` skill (§ 2). Not the "Destination" filter
in the Sales toolbar, which filters rows rather than switching the store.

## 1. Sales export (per country)

**URL**: `https://reports.noon.partners/en/sales/?project=PRJ{project_id}`

**The page opens on a rolling 30-day window, not last month.** Exporting
without changing it gives a file that silently misses the start of the
month and includes days from the current one. Set the range, then read it
back before exporting:

```bash
browser-use <<'PY'
# The date inputs have no name=; address them by placeholder.
fill_input('input[placeholder="Start date"]', "01 Mar 2026")
fill_input('input[placeholder="End date"]',   "31 Mar 2026")
js("document.querySelector('td[title=\"2026-03-31\"]')?.click()")   # confirm in the calendar popup
print(js("return ['Start date','End date'].map(function(p){"
         "var i=document.querySelector('input[placeholder=\"'+p+'\"]');"
         "return p+'='+(i?i.value:'MISSING');});"))
PY
```

The readback should show the first and last day of the target month, and
the URL carries `?from_date=2026-03-01&to_date=2026-03-31`. If it still
shows a 30-day window, the range did not commit — set it again. Read the
two inputs by placeholder; scanning every input on the page returns
unrelated controls.

**Export:** Export → the modal shows the previous file (if any) → **New
export** → wait 10–30 s for "Exporting" to finish → click the download
link (`sales_export_{date}_{time}_{project}_{country}_{seq}.csv`) → close
the modal. For another country, switch the store (above) and repeat.

## 2. Transaction View export

**URL**: `https://noon-payments.noon.partners/en/transaction-view?project=PRJ{project_id}`

Left nav: SOA, Statements, Transaction View, Invoices & Creditnotes,
Legacy Exports. Step-by-step flow, recovery after a disconnect and the CSV
columns: `references/transaction-view.md`.

What shapes the approach:

- **One unfiltered export covers every contract in the project.** The
  CSV has rows for each country contract, identified by `Contract` /
  `Contract Title`. Run it once and split locally; filter the Contracts
  dropdown first only if you want one country. The Contracts dropdown
  lists the contracts of the store selected in the sidebar switcher.
- **Status runs `Requested → Exporting → Processed`.** Jobs can take
  several minutes and up to ~35 for data-heavy months.
- **There are two "Export" buttons.** The main one near the Contracts /
  Transaction Type controls queues the job; its text flips to
  `Exporting` and back. The one that writes the CSV is inside the results
  panel (`ExportBox_content__*`) below the table, after the job reaches
  Processed. A bare text match finds the main button first, so scope the
  click to the panel.
- **Click the main button once.** Each further click queues another
  `EXP…` job on the same backend and slows every job down. After a page
  refresh, recover through the status API instead (reference § 2).
- **`No data` disables the button.** A range with no activity shows a
  greyed-out button and no panel; skip that range.

## 3. Catalog exports

**URL**: `https://noon-catalog.noon.partners/en/exports?project=PRJ{project_id}`

**Add Export** → modal with a **Type** dropdown (and Cancel / Create):

| Type | Description |
|------|-------------|
| Catalog Export | Full catalog data |
| Content | Titles, descriptions, images, attributes |
| Pricing | Current + promotional prices |
| Stock | Inventory levels |
| Partner SKU Generate | Partner SKU mappings |
| Reports | Performance/sales report data |
| Global Catalog Export | Global catalog across markets |

Select the type → Create → download from the Result column once Status
reads "Completed" (1–10 minutes depending on size).

## 4. What the Transaction View does not give you

It is order-grained for sales but lumped for FBN fees:

- **Per-SKU storage / return fees.** The month's FBN storage settles as
  one `balance_transfer` row with an opaque `Others including VAT`.
  Per-SKU storage and RTV removal fees come from FBN Reports — the
  `noon-fbn` skill § 8 and its `references/fee-reports.md`, which also
  reconciles the four reports to this settlement (one-month lag, VAT
  gross-up).
- **Per-SKU ad spend.** `statement_fee` rows carry only an account-level
  `Advertising Fee`; per-SKU spend comes from Ad Manager's export (the
  `noon-ads` skill).

It is, however, the SKU bridge for both: its `SKUs` column (noon's
internal `Z…Z-<n>` key) and `Partner SKUs` (the seller's code) sit on the
same row, and nowhere else do the two keyspaces meet. Keep the
transaction CSV next to the fee and ads files.

## See also

- `noon-shared` — login, page structure, store switcher
- `noon-fbn` § 8 + `references/fee-reports.md` — per-SKU FBN fees and the
  reconciliation against § 2's settlement line
- `noon-ads` — per-SKU ad spend export
