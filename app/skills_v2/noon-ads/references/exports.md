# Noon Ads — exports

- [Which export for what](#which-export-for-what)
- [Export campaigns (list level)](#export-campaigns-list-level)
- [Setting a custom date range](#setting-a-custom-date-range)
- [What's inside the workbook](#whats-inside-the-workbook)
- [Reconciling ad spend against the statement](#reconciling-ad-spend-against-the-statement)
- [Per-tab Export Data](#per-tab-export-data)

## Which export for what

| | `Export campaigns` (list level) | `Export` on **Customer Queries** | `Export Data` on Products / Targets |
|---|---|---|---|
| Where | Campaigns tab, top-right of the list | campaign detail → Customer Queries tab | campaign detail → those tabs |
| Behaviour | async, ~1–5 min | ~15–25 s | often a silent no-op |
| Scope | every campaign in the list filter | one campaign, both layers | the tab's current view |
| No file yet | keep polling (below) | wait ~60 s, then check you clicked `Export` on that tab | give up after ~10 s and read the DOM |

The Customer Queries export is the drill source (`../SKILL.md` § 6).
Every file lands in `~/.vibe-seller/downloads/<slug>/`; list the folder
before clicking so you can diff.

## Export campaigns (list level)

The practical way to get **ad spend per SKU** and month-range figures.
It honours the list's date-range filter, so set the range first.

Click the page's button. An internal endpoint
(`/_svc/productads/v2/noon/reports` or similar) answers with a workbook
holding one empty `Report` sheet (~5 KB) that looks downloaded and holds
nothing — a downstream profit calculation then reads "no SKU spent
anything". If the calendar is awkward, keep driving it rather than
switching to an endpoint.

The button doubles as the progress indicator:

1. Click it once. It turns `disabled` while noon builds the file.
2. The file arrives as `campaigns-export-*.xlsx`, usually after 1–5 min
   for a few dozen campaigns. `disabled` means "generating" — poll the
   folder in short checks and don't re-click.
3. Check the `Read me` sheet: its second row states the range
   (`Performance data is for 01 Sep 2026 to 30 Sep 2026`). A workbook
   without that sheet, or a file of a few KB, is a failed export, not a
   quiet month.
4. The file names the range but not the country. Rename it on arrival
   (`ads_overview_{CC}_{YYYY-MM}.xlsx`) before exporting another country,
   or the next download overwrites it. Country comes from the `en-{cc}`
   URL segment.

Click the export with a JS `.click()` on the `<button>` (not the label
`<span>` inside it) — coordinate clicks do not trigger it:

```bash
browser-use <<'PY'
print(js("""(function(){
  var d=document.querySelector('[class*=CampaignStatusTabs_headerExportAction]');
  var b=d&&(d.querySelector('button')||d);
  if(!b) return 'nf';
  b.click();                 // once, then poll the download dir
  return 'clicked, disabled=' + String(b.disabled);
})()"""))
PY
```

## Setting a custom date range

Presets: Today / Yesterday / Last 7 / 30 / 90 days / Year to Date / All
time / Custom range.

The picker is a React date picker: drive it with JS `element.click()`.
Coordinate clicks open it but do not select a day, which reads as "the
calendar is unusable". Each step below stops on `nf` instead of carrying
on, because a missed click followed by **Apply** keeps a stale or
half-set range and the export then looks fine while covering the wrong
days.

```bash
browser-use <<'PY'
import calendar, time
YEAR, MONTH = 2026, 9                  # the service month to export
TARGET = f'{calendar.month_name[MONTH]} {YEAR}'   # calendar caption, e.g. "September 2026"

def jsclick(expr):
    r = js("(function(){var el=%s; if(!el) return 'nf'; el.click(); return 'ok';})()" % expr)
    if r != 'ok':
        raise SystemExit(f'not found: {expr[:80]} -- stop, do not Apply')
    time.sleep(1)

def caption():
    return (js("(document.querySelector('[class*=rdp-caption]')||{}).textContent") or '').strip()

# 1. open the range menu by its component class (its label is whatever is selected now)
jsclick("document.querySelector('[class*=DateRangeFilter_trigger]')")
# 2. Custom range
jsclick("Array.from(document.querySelectorAll('.ant-popover-content *')).find(e=>e.children.length===0&&(e.textContent||'').trim()==='Custom range')")
# 3. page back until the caption shows the target month
for _ in range(24):
    if TARGET in caption():
        break
    jsclick("document.querySelector('[class*=rdp] button[aria-label=\"Previous month\"]')")
else:
    raise SystemExit(f'calendar never reached {TARGET}; caption reads {caption()!r}')
print('caption:', caption())
PY
```

Then pick the two days, clicking the **Start date** / **End date** label
before each. Skip day buttons whose class contains `outside`: the grid
also draws the neighbouring months, and a "30" from the previous month
turns the range into `30 Aug – 30 Sep`.

```bash
browser-use <<'PY'
import calendar, time
YEAR, MONTH = 2026, 9
LAST = calendar.monthrange(YEAR, MONTH)[1]

def jsclick(expr):
    r = js("(function(){var el=%s; if(!el) return 'nf'; el.click(); return 'ok';})()" % expr)
    if r != 'ok':
        raise SystemExit(f'not found: {expr[:80]} -- stop, do not Apply')
    time.sleep(1)
def day(n):
    return ("Array.from(document.querySelectorAll('[class*=rdp] button[name=day]'))"
            ".find(b=>b.textContent.trim()==='%s'&&!/outside/i.test(b.className))" % n)
def label(t):
    return ("Array.from(document.querySelectorAll('[class*=CustomRangePicker_picker] span'))"
            ".find(s=>s.textContent.trim()==='%s')" % t)

for expr in (label('Start date'), day(1), label('End date'), day(LAST)):
    jsclick(expr)
jsclick("Array.from(document.querySelectorAll('[class*=CustomRangePicker_picker] button')).find(b=>b.textContent.trim()==='Apply')")
time.sleep(2)
# the trigger should now read the whole month, e.g. "01 Sep 2026 - 30 Sep 2026"
print(js("(document.querySelector('[class*=DateRangeFilter_trigger]')||{}).textContent"))
PY
```

## What's inside the workbook

The list-level and the Customer Queries exports share one layout ("Noon
Ads Bulk Update"): one sheet per kind, all ad products mixed.

| Sheet | Grain | Key columns |
|---|---|---|
| `Read me` | — | row 2: the performance date range |
| `Campaigns` | campaign | Campaign ID, **Ad Type** (product / brand / display), Campaign Name, budget, … Views, Clicks, Orders, ATC, Spends, Revenue |
| `SKUs` | campaign × offer | Campaign ID, SKU, PSKU, Offer Code, … Spends, Orders |
| `Targets` | keyword / target | Campaign ID, Ad Group ID, Target ID, Target Value, Target Type, Bid, … |
| `Negative Keywords` | negative | Campaign ID, Keyword Text, Match Type, Status |
| `Placement` | placement | Campaign ID, Placement Type, metrics |
| `Queries` | search term | Campaign ID, SKU, Query, metrics |

Row 1 of every data sheet is a colour-group banner (`OPERATION`, `KEYS ·
do not change`, `PERFORMANCE · read-only` …); row 2 is the header. Only
`Campaigns` carries `Ad Type`; join the others on `Campaign ID`.

**Per-SKU ad spend** = `Spends` from `SKUs`, grouped by `SKU`:

```python
skus = pd.read_excel(path, 'SKUs', header=1, dtype={'Campaign ID': str})
per_sku = skus.groupby('SKU', as_index=False)[['Spends', 'Orders']].sum()
```

Product campaigns decompose exactly into their SKU rows. A brand ad's
banner (and any display campaign) spends money no SKU row claims:
`Campaigns.Spends − Σ SKUs.Spends` per campaign is real, unattributed
spend — keep it as its own bucket rather than dropping it or charging it
to a SKU.

- **Parent vs variant SKUs.** `SKU` is a noon-internal variant
  (`Z…Z-<n>`) and `PSKU` its parent (`Z…Z`). Neither is the seller code
  in the Transaction View's `Partner SKUs`; bridge through that export's
  `SKUs` column ([`noon-fbn` `fee-reports.md` § 5](../../noon-fbn/references/fee-reports.md#5-column-shapes--join-keys)).
- **The list-level `Queries` sheet may be capped.** A round row count
  means truncated. For the drill use the per-campaign Customer Queries
  export instead.

## Reconciling ad spend against the statement

Ad spend does not tie exactly to the Transaction View's `Advertising
Fee` (`statement_fee` rows, `Non-Order Fees`):

```
sum(Spends over the calendar month) x (1 + VAT)  ~=  sum(statement_fee Advertising Fee)
```

within a couple of percent. Ad statements run on a weekly cycle whose
periods straddle month ends, while the export filters on performance
date. Use the export for per-SKU attribution (its parts sum to its
total) and `statement_fee` only for the amount actually invoiced.

## Per-tab Export Data

The **Export Data** button on the Products and Targets tabs exports the
current filtered view, but in this environment it is often a silent
no-op. If no file lands within ~10 s, read the table from the DOM
(`../SKILL.md` § 4) rather than retrying — retries just spend steps.
This does not apply to the Customer Queries `Export`, which works and is
the drill source.

```bash
browser-use <<'PY'
js("Array.from(document.querySelectorAll('button')).find(b=>/export data/i.test(b.textContent))?.click()")
PY
```
