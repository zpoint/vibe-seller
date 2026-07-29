# Noon FBN — per-SKU fee reports (storage, RTV removal)

> Loaded from `noon-fbn/SKILL.md` § 8. Read this when a task needs
> **per-SKU** FBN charges — monthly storage, long-term storage,
> non-saleable ageing storage, RTV removal — rather than the single
> lumped settlement line the Transaction View gives you.

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

### Critical facts

1. **There is NO country field in the modal.** The report's country comes
   from the **`en-{cc}` segment of the page URL**. To generate an AE
   report you must be on `…/en-ae/fbnreports…`. Generating from
   `en-sa` silently produces an `sa` report.
2. **The report LIST is project-wide** — an `en-sa` page lists both `sa`
   and `ae` rows. So you only need to switch URL to *generate*, never to
   *download*.
3. **`Service Month` is a month picker**, not a date range. Future
   months are greyed out. The field must read `YYYY-MM` before the
   modal's `Generate Report` submits — verify it, don't assume the click
   landed.
4. **Modal geometry moves.** The category select sits higher when no
   report is chosen yet (fewer fields rendered). Never hardcode
   coordinates — locate each control by its placeholder
   (`Select a category` / `Select a report` / `Select month`) and read
   its rect. Reload the page between generations; a stale modal is the
   usual cause of a "category not found" failure.
5. **Status is `Pending → Complete`.** Small reports finish in well
   under a minute; a data-heavy Monthly Storage report can take a few.
   `Nr. of Rows` shows `...` for some completed reports — that is a UI
   placeholder, **not** an error and **not** an empty report.

## 2. Generating a missing report

Reports are not auto-generated for every month/country — a country can
simply be **missing** one (observed live: a store had Monthly / LTS /
Non-Saleable for one month but **no RTV Removal report at all** for one
of its two countries). Always reconcile (§ 4); if the sum doesn't close,
a report is missing, not "noon rounds differently".

```bash
browser-use <<'PY'
import time, json
def rect(expr):
    r = js("(function(){%s})()" % expr)
    return json.loads(r) if r and r.startswith('{') else None

def click_ph(ph):                      # click a control by its placeholder
    r = rect("""
      var ph=%s;
      var e=Array.from(document.querySelectorAll('input')).find(x=>x.placeholder===ph)
         || Array.from(document.querySelectorAll('div,span')).find(
              x=>x.children.length===0 && (x.textContent||'').trim()===ph);
      if(!e) return 'nf';
      var b=e.getBoundingClientRect();
      return JSON.stringify({x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)});
    """ % json.dumps(ph))
    if not r: return False
    click_at_xy(r['x'], r['y']); time.sleep(2); return True

def click_opt(t, below):               # click a dropdown option below y=below
    r = rect("""
      var want=%s, lo=%d;
      var el=Array.from(document.querySelectorAll('div,li,span')).filter(e=>
        e.children.length===0 && (e.textContent||'').trim()===want
        && e.getBoundingClientRect().y>lo && e.getBoundingClientRect().height>5);
      if(!el.length) return 'nf';
      var b=el[0].getBoundingClientRect();
      return JSON.stringify({x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)});
    """ % (json.dumps(t), below))
    if not r: return False
    click_at_xy(r['x'], r['y']); time.sleep(2); return True

# ALWAYS reload first — a stale modal breaks the next generation.
goto_url("https://fbn.noon.partners/en-{cc}/fbnreports?project=PRJ{project_id}")
wait_for_load(); time.sleep(7)
js("window.scrollTo(0,0)")

r = rect("""var b=Array.from(document.querySelectorAll('button')).find(
             x=>/generate report/i.test(x.textContent) && x.getBoundingClientRect().y<200);
           if(!b) return 'nf'; var q=b.getBoundingClientRect();
           return JSON.stringify({x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2)});""")
click_at_xy(r['x'], r['y']); time.sleep(3)

click_ph("Select a category"); click_opt("Finance", 150)
click_ph("Select a report");   click_opt("<Report Name>", 200)
click_ph("Select month")
# month grid: click the 3-letter month cell (y>380); page back with the
# «  arrow near y 350-395 / x 580-620 if the picker opened on a later year.
click_opt("<Mon>", 380)

val = js("(function(){var i=document.querySelector('input[placeholder=\"Select month\"]');"
         "return i? i.value : '?'})()")
assert val == "<YYYY-MM>", f"service month did not take: {val}"

# submit = the SECOND 'Generate Report' button (y>200); the first is the
# page's top-right one, which would just re-open the modal.
r = rect("""var b=Array.from(document.querySelectorAll('button')).filter(
             x=>/generate report/i.test(x.textContent) && x.getBoundingClientRect().y>200);
           if(!b.length) return 'nf'; var q=b[0].getBoundingClientRect();
           return JSON.stringify({x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2)});""")
click_at_xy(r['x'], r['y']); time.sleep(5)
PY
```

## 3. Downloading — the filename collision

`Download` in the Actions column is a real `<button>` that writes the CSV
straight to `~/.vibe-seller/downloads/<slug>/`. No modal, no signed URL.

> **The filename contains ONLY the report type** — e.g.
> `fbn_finance_monthlystoragechargereport.csv`. It carries **no country
> and no service month**. Downloading `sa` then `ae` for the same report
> overwrites / produces ` (1)` suffixes and you lose track of which is
> which. **Rename immediately after each download, before clicking the
> next one**, to something like
> `monthly_storage_{CC}_{YYYY-MM}.csv`.

Two mechanical gotchas:

- **Scroll the row into view first.** `click_at_xy` on a row below the
  viewport does nothing (silently). `row.scrollIntoView({block:'center'})`,
  wait, then re-read the button's rect — the coordinates change.
- Match the row by its **Report Code** (`EXP…`), not by report name —
  names repeat across countries and months.

```bash
browser-use <<'PY'
import time, json
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
    return JSON.stringify({x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2)});
  }
}
return 'rownf';
""" % CODE)
c=json.loads(r); click_at_xy(c['x'], c['y'])
PY
# then, in the shell, rename the newest file before the next download:
#   mv ~/.vibe-seller/downloads/<slug>/fbn_finance_*.csv \
#      <out>/monthly_storage_SA_2026-06.csv
```

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
- Verified live to the **cent** on both countries of a two-country
  store across two consecutive service months. If your sum doesn't
  close, in order of likelihood: (a) a report for that country/month
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
> month are the barcode→SKU dictionary; this joined 100% of rows live.
> Use `currency_code` (not `country_code`) to bucket RTV by country.

### The SKU keyspace trap

Three different keys are in play — do **not** assume they match:

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
- `noon-ads` § Export all campaigns — per-SKU **ad spend**, same
  keyspace trap, same rename-on-arrival rule
