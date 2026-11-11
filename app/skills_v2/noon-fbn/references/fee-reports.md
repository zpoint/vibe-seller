# Noon FBN — per-SKU fee reports (storage, RTV removal)

Read from `noon-fbn` § 8 when a task needs **per-SKU** FBN charges —
monthly storage, long-term storage, non-saleable ageing storage, RTV
removal — rather than the single lumped settlement line the Transaction
View gives you.

## Contents

1. Page + report catalogue
2. Generating a missing report
3. Downloading — naming, checking, empty files
4. Reconciliation
5. Column shapes + join keys

The Transaction View export (see `noon-exports` § 2) reports the whole
month's FBN storage settlement as **one `balance_transfer` row** with an
opaque `Others including VAT` amount and **no SKU attribution**. The FBN
Reports page is where the per-SKU decomposition lives.

## 1. Page + report catalogue

**URL**: `https://fbn.noon.partners/en-{cc}/fbnreports?project=PRJ{project_id}`

`Generate Report` (top-right) opens a two-level picker —
**category** → **report** — then the report's own parameters:

| Category | Reports |
|---|---|
| **Finance** | Estimated Monthly Storage Fee, **Monthly Storage Charge**, **Long Term Storage Charge**, **Non Saleable Storage Charge**, **RTV Removal Charge**, Non-Saleable Ageing |
| Inbound | FBN Received, Scheduled Delivery Accuracy, Inbound Performance Score |
| Inventory | FBN Inventory Detail, Inventory Ledger (Summary View), Inventory Ledger (Detailed View), SKU Level Inventory Ledger |
| Inventory v2 | Ledger Detailed View, Ledger Summary View, aging |
| Returns | Saleable Recommended RTV, Non-saleable Recommended RTV, RTV Report – Requests, RTV Report – Shipments |

The **four bolded Finance reports are the complete decomposition** of the
storage settlement (see § 4). The others are not needed for fee work.

### Facts that shape the flow

1. **There is no country field in the modal.** The report's country comes
   from the **`en-{cc}` segment of the page URL**. To generate an AE
   report, be on `…/en-ae/fbnreports…`. Generating from
   `en-sa` silently produces an `sa` report.
2. **The report LIST is project-wide** — an `en-sa` page lists both `sa`
   and `ae` rows. So you only need to switch URL to *generate*, never to
   *download*.
3. **`Service Month` is a month picker**, not a date range. Future
   months are greyed out. Read the field back as `YYYY-MM` before
   submitting rather than assuming the click landed.
4. **The modal layout moves** as fields render, so locate each control
   by its placeholder (`Select a category` / `Select a report` /
   `Select month`) at click time. Reload the page between generations; a
   stale modal is the usual cause of a "category not found" failure.
5. **Status is `Pending → Complete`.** Small reports finish in well
   under a minute; a data-heavy Monthly Storage report can take a few.
   `Nr. of Rows` shows a number for a populated report and `...` for an
   empty one. `...` is not an error, but don't assert on it either —
   download the file and classify it (§ 3).

## 2. Generating a missing report

Reports are not generated automatically for every month and country — a
country can simply be missing one (e.g. Monthly / LTS / Non-Saleable
present but no RTV Removal). Reconcile (§ 4); if the sum doesn't close, a
report is missing, not rounded differently.

Locate every control by its text or placeholder and read its rect at
click time: the modal renders asynchronously and its layout shifts as
fields appear, so fixed coordinates break. Build JS string literals with
`json.dumps(value)` — a `js()` string is Python and JavaScript at once,
and hand-nested quotes produce a `SyntaxError` that points at the wrong
language. `js()` returns JS objects as Python dicts, so return the object
directly.

```bash
browser-use <<'PY'
import time, json

def centre(expr):
    """Centre {x, y} of the element the JS body returns, or None."""
    r = js("(function(){var e=(function(){%s})(); if(!e) return null;"
           "var b=e.getBoundingClientRect();"
           "return {x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)};})()" % expr)
    return r if isinstance(r, dict) else None

def click(expr, what, tries=3):
    """Click the located element; name the control if it never appears."""
    for _ in range(tries):
        c = centre(expr)
        if c:
            click_at_xy(c['x'], c['y']); time.sleep(2); return True
        time.sleep(2)
    print(f'NOT FOUND: {what} — {page_info()}')
    return False

def by_placeholder(ph):
    return """var ph=%s;
      return Array.from(document.querySelectorAll('input')).find(x=>x.placeholder===ph)
          || Array.from(document.querySelectorAll('div,span')).find(
               x=>x.children.length===0 && (x.textContent||'').trim()===ph);""" % json.dumps(ph)

def option(text):
    """A visible option in the open dropdown or month picker, by exact text."""
    return """var want=%s;
      var pop=document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden),'
        + '.ant-picker-dropdown:not(.ant-picker-dropdown-hidden),[role=listbox]') || document;
      return Array.from(pop.querySelectorAll('div,li,span,td')).find(e=>
        e.children.length===0 && (e.textContent||'').trim()===want
        && e.getBoundingClientRect().height>5);""" % json.dumps(text)

def generate_button(in_modal):
    """The page's own Generate Report button, or the modal's submit."""
    return """var m=document.querySelector('.ant-modal,[role=dialog]');
      var bs=Array.from(document.querySelectorAll('button')).filter(
        x=>/generate report/i.test(x.textContent));
      return bs.find(x=>%s(m && m.contains(x)));""" % ('' if in_modal else '!')

# Reload first — a stale modal is the usual cause of "category not found".
goto_url("https://fbn.noon.partners/en-{cc}/fbnreports?project=PRJ{project_id}")
wait_for_load(); time.sleep(5)

click(generate_button(in_modal=False), 'page Generate Report button')
click(by_placeholder('Select a category'), 'category select'); click(option('Finance'), 'Finance')
click(by_placeholder('Select a report'), 'report select');     click(option('<Report Name>'), 'report')
click(by_placeholder('Select month'), 'month picker')
# The picker opens on the current year. For a month in an earlier year,
# click the picker header's previous-year arrow (« ,
# .ant-picker-header-super-prev-btn) first, then the month cell.
click(option('<Mon>'), 'month cell')        # 3-letter month, e.g. 'Jun'

val = js("var i=document.querySelector('input[placeholder=\"Select month\"]'); return i ? i.value : '?'")
assert val == "<YYYY-MM>", f"service month did not take: {val}"

click(generate_button(in_modal=True), "the modal's submit button")
time.sleep(5)
PY
```

If the modal submit is not found, the modal is probably not an
`.ant-modal` / `[role=dialog]` on the current build: take the
`Generate Report` button that is not the page's top-right one.

## 3. Downloading — naming, checking, empty files

`Download` in the Actions column is a real `<button>` that writes the CSV
straight to `~/.vibe-seller/downloads/<slug>/`. No modal, no signed URL.

**The filename carries only the report type** (e.g.
`fbn_finance_monthlystoragechargereport.csv`) — no country, no service
month. Downloading `sa` then `ae` of the same report produces ` (1)`
suffixes and you lose track of which is which, so move and rename each
file before clicking the next Download.

Names to use (lowercase country code; these four stems are what
`scripts/check_fee_rows.py` and the server's monthly-report check
recognise):

```
monthly_storage_<cc>_<YYYY-MM>.csv
longterm_storage_<cc>_<YYYY-MM>.csv
nonsaleable_storage_<cc>_<YYYY-MM>.csv
rtv_removal_<cc>_<YYYY-MM>.csv
```

For a monthly report-download run, declare it first with
`vibe_seller_declare_report_run` (the month it covers, `YYYY-MM`) and put
them in the task workspace under `reports_noon_<MM>_<slug>/`, `<MM>` being
the service month — that is the layout checked at `set_task_result`, for a
declared run and for no other. A run that only wants this month's fee
numbers ad hoc changes nothing about the layout; it just isn't declared.
Copies kept for later analysis go under `store-data/<slug>/fbn/<YYYY-MM>/`.

Two mechanical gotchas:

- **Scroll the row into view first.** `click_at_xy` on a row below the
  viewport silently does nothing. `row.scrollIntoView({block:'center'})`,
  wait, then re-read the button's rect — the coordinates change.
- Match the row by its **Report Code** (`EXP…`), not by report name —
  names repeat across countries and months.

```bash
browser-use <<'PY'
import time
CODE = "EXP..."          # the row's Report Code
r = js("""
var rows=document.querySelectorAll('table tr');
for(var i=0;i<rows.length;i++){
  if(/%s/.test(rows[i].textContent||'')){ rows[i].scrollIntoView({block:'center'}); return 'ok'; }
}
return 'rownf';
""" % CODE)
time.sleep(1.5)
r = js("""
var rows=document.querySelectorAll('table tr');
for(var i=0;i<rows.length;i++){
  if(/%s/.test(rows[i].textContent||'')){
    var b=rows[i].querySelector('button'); var q=b.getBoundingClientRect();
    return {x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2)};
  }
}
return 'rownf';
""" % CODE)
if isinstance(r, dict):
    click_at_xy(r['x'], r['y'])
else:
    print(f'row {CODE} not on screen: {r}')
PY
# then, in the shell, rename the newest file before the next download:
#   mv ~/.vibe-seller/downloads/<slug>/fbn_finance_monthlystoragechargereport.csv \
#      reports_noon_06_<slug>/monthly_storage_sa_2026-06.csv
```

### Check each file against its name

The name is your claim, not the file's: click one row too far and a
perfectly named file holds another country's or month's money, which
surfaces only much later as a reconciliation that misses by a few
percent. Every storage report carries its own `country_code` and
`service_month`, so check the file against its name before the next
download (pass the path, the lowercase country and the month):

```bash
# needs pandas — use the project venv's python if bare python3 lacks it
python3 - reports_noon_06_<slug>/monthly_storage_sa_2026-06.csv sa 2026-06 <<'PY'
import pandas as pd, sys
path, want_cc, want_sm = sys.argv[1], sys.argv[2], sys.argv[3]
df = pd.read_csv(path)
if len(df) == 0:
    print(f'{path}: header only — see "An empty file is two different facts"')
else:
    cc = set(df['country_code'].astype(str).str.lower())
    sm = set(df['service_month'].astype(str))
    assert cc == {want_cc}, f'{path}: holds {cc}, expected {want_cc}'
    assert sm == {want_sm}, f'{path}: holds {sm}, expected {want_sm}'
    print(f'{path}: OK {cc} {sm} {len(df)} rows')
PY
```

### An empty file is two different facts — resolve which

A header-only download is not self-explanatory. Run the checker rather
than judging by eye:

```bash
python3 .claude/skills/noon-fbn/scripts/check_fee_rows.py <out-dir> --service-month 2026-07
# exit 1 ⇒ something is unposted or undecidable
```

It splits the two cases by what the report measures:

| Report | Zero rows means |
|---|---|
| `monthly_storage` | **Not published yet.** It bills all held stock, so any inventory produces rows; it is the publication witness for its country and month. Treat as pending. |
| `longterm_storage`, `nonsaleable_storage` | Genuinely zero **if** that country's monthly storage has rows (a small, fast-turning store often has no aged or damaged stock); not published if monthly storage is empty too. |
| `rtv_removal` | **Genuinely zero** — removals are discrete events and a month can have none. Keep the file; it is proof the question was asked. |

Why this is easy to miss:

- The service month is selectable in the picker and the report still
  reaches `Complete` for a month with no data. Selectable ≠ published.
- Retrying does not help: a second generation of an unpublished month
  returns a byte-identical empty file. Report it as pending. Fee months
  are reliably published from about the 15th of the following month;
  emptiness is about the service month, not the generator.

RTV Removal has neither column — bucket it by `currency_code` instead.
The Ad Manager xlsx has no country column at all, so for those the only
check is that SA's and AE's `Spends` totals **differ**; if they match,
the second download overwrote the first.

## 4. Reconciliation — the check that proves nothing is missing

```
Transaction View  balance_transfer."Others including VAT"  for month M
  ==  SUM(charged_amount) over the four Finance reports
        WHERE service_month = M - 1
      x (1 + VAT)
```

- **VAT**: SA 15%, AE 5%. `charged_amount` in every FBN report is
  **ex-VAT**; the Transaction View column is **VAT-inclusive**. Never
  compare them raw.
- **The one-month offset is real.** The settlement that appears in month
  M's transaction export is service month **M − 1**'s storage. So
  per-SKU storage for analysis month M must be read from the **service
  month M** reports — never from month M's `balance_transfer`.
- The identity holds to the cent. If your sum doesn't close, in order of
  likelihood: (a) a report for that country/month
  was never generated (§ 2); (b) you compared ex-VAT to incl-VAT;
  (c) you used the wrong service month.

Illustrative shape (round numbers, not live figures):

```
service month M-1, country SA        ex-VAT
  monthly storage                   5,300.00
  long term storage                     0.00
  non-saleable ageing storage         180.00
  RTV removal                         100.00
                                    --------
                                    5,580.00  x 1.15 = 6,417.00
month M Transaction View balance_transfer     = 6,417.00   ✓
```

## 5. Column shapes + join keys

All three **storage** reports share a spine:
`soa_reference_nr, description_en, id_partner, service_month, sku,
pbarcode, year_month, country_code, cost_per_cubic_feet, charged_amount`
plus per-type quantity columns (`average_stock_on_hand` /
`qty_aged_6m` / `qty_aged_0_to_1m` …). `description_en` is the
human-readable fee name — use it as the fee-type label.

**RTV Removal is the odd one out**: it has **no `sku` and no
`country_code`** column. It carries `barcode`, `return_request_nr`,
`shipment_nr`, `return_type` (`saleable` / non-saleable),
`shipped_qty`, `currency_code`, `charged_amount`.

> To attribute RTV removal per SKU, join `rtv.barcode` →
> `storage.pbarcode` → `storage.sku`. The storage reports for the same
> month are the barcode→SKU dictionary; in practice this joins every row.
> Use `currency_code` (not `country_code`) to bucket RTV by country.

### The SKU keyspace trap

Three different keys are in play, and they don't match each other:

| Source | SKU column | Form |
|---|---|---|
| FBN fee reports | `sku` | noon internal, `Z<20 hex>Z-<n>` |
| Ad Manager export | `Sku` | noon internal, parent `Z…Z` **or** variant `Z…Z-<n>` |
| Transaction View | `Partner SKUs` | **the seller's own code**, e.g. `WIDGET-006` |

The Transaction View is the bridge: its **`SKUs`** column is the noon
internal key and **`Partner SKUs`** the seller code, on the same row.
Build the map from the same month's transaction export:

```python
bridge = (txn[['SKUs', 'Partner SKUs']].dropna()
              .astype(str).apply(lambda s: s.str.strip())
              .drop_duplicates())
noon_to_partner = dict(zip(bridge['SKUs'], bridge['Partner SKUs']))
```

Coverage is high but **not** total — a SKU can hold stock (and accrue
storage) while making no sales that month, so it has no transaction row
and no bridge entry. Carry those through as unmapped rather than
dropping them, or the per-SKU total stops matching § 4.

## See also

- `noon-exports` § 2 — the Transaction View export that provides both
  the settlement line to reconcile against and the SKU bridge
- `noon-ads` — per-SKU **ad spend** export, same keyspace trap, same
  rename-on-arrival rule
