# Amazon Ads — reading campaigns

How to read an account's campaigns, targets and search terms off the ad
console. Preconditions, URLs and marketplace layouts are in
[`mechanics.md`](mechanics.md); what to record from what you read is in
[`data-contract.md`](data-contract.md) and the audit procedure in
[`audit-quickref.md`](audit-quickref.md).

The rule behind most of this file: **exports are ground truth, grids are
fragile.** Amazon's grids virtualize rows (and sometimes columns), so
only a dozen or so rows exist in the DOM at any moment. Reach for the
bulk export or a per-table CSV export before scraping.

## Contents

1. [The campaign list: clear filters, trust the grid's total](#1-the-campaign-list-clear-filters-trust-the-grids-total)
2. [Bulk download (the account-wide export)](#2-bulk-download-the-account-wide-export)
3. [Campaign list view and per-campaign metrics](#3-campaign-list-view-and-per-campaign-metrics)
4. [Date pickers](#4-date-pickers)
5. [Campaign and ad-group pages](#5-campaign-and-ad-group-pages)
6. [Targeting tab](#6-targeting-tab)
7. [Search-terms tab](#7-search-terms-tab)
8. [Bid adjustments page (placements)](#8-bid-adjustments-page-placements)
9. [Scroll-and-accumulate for virtualized grids](#9-scroll-and-accumulate-for-virtualized-grids)
10. [Brand Analytics ASIN search-term report](#10-brand-analytics-asin-search-term-report)

## 1. The campaign list: clear filters, trust the grid's total

The campaign list has a search input directly above the grid. Its
placeholder has changed over time (`Find a campaign` / `查找广告活动`,
now `Search by name or ID`), so key on its position or on the
`Clear search terms` aria-label of its ✕, never on the placeholder.
Setting its value with the React-friendly setter and dispatching
`input` + `keydown:Enter` filters the table; result rows link to
`…/cm/sp/campaigns/<id>?entityId=…`. The other top-of-page input
(`Search with metrics or settings` / `使用指标或设置进行搜索`) is a
saved-filter builder, not a name search.

**The search term persists in the browser profile**, not the URL, so a
term typed in an earlier session is still applied when you open the
list and hides every campaign that does not match. Enumerating the
whole account therefore starts with clearing it, and three plausible
ways of clearing it do not work:

1. **The native value setter is a false clear.** The input is
   React-controlled: setting `value` + `input`/`Enter` empties the DOM
   value but keeps the applied filter. The grid still shows the subset,
   and an `input.value === ''` check now passes.
2. **The list-level "clear filters" / "remove all" control** removes
   the status/type filter chips only; the search term survives.
3. **A synthetic `dispatchEvent(new MouseEvent('click'))` on the ✕** is
   ignored — the widget only reacts to a trusted pointer event.

What works is a real `click_at_xy` on the search box's own ✕, found by
the English aria-label `Clear search terms` (aria-labels stay English on
a localized UI). There can be two such ✕ (metrics search + campaign
search); take the closest one above the grid:

```python
xy = js(r'''
  var grid=document.querySelector("[role=grid],[role=treegrid]");
  var gtop=grid?grid.getBoundingClientRect().top:1e9;
  var cands=[...document.querySelectorAll('button[aria-label]')]
    .filter(function(b){return /clear search/i.test(b.getAttribute("aria-label")||"");})
    .map(function(b){return {b:b,r:b.getBoundingClientRect()};})
    .filter(function(o){return o.r.width>0 && o.r.top<gtop;});   // ✕ above the grid
  if(!cands.length) return "null";                                // no ✕ => already empty
  cands.sort(function(a,z){return z.r.top-a.r.top;});             // closest above grid
  var e=cands[0].b; e.scrollIntoView({block:"center"});
  var r=e.getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
''')
import json
if xy!="null":
    c=json.loads(xy); click_at_xy(c["x"], c["y"])   # trusted click; dispatchEvent is ignored
```

Then verify by the grid's own total, not by the input value.
`aria-rowcount` minus the header row is language-neutral and counts the
whole filtered set; a real clear makes it jump to the account total:

```python
print(js('var g=document.querySelector("[role=grid],[role=treegrid]"); return g?(+g.getAttribute("aria-rowcount"))-1:null;'))
```

That number is your completeness target for the marketplace. If it did
not rise after the clear, the search is still applied — click the ✕
again rather than proceeding on a total that looks small for the
account.

**Status filter.** The default "Active : Enabled" chip near the table
hides paused and archived campaigns, and "Remove all" does not clear it;
click the chip's own ×. A status/type chip left from an earlier session
persists the same way the search does, so check the chips before
enumerating.

**Pagination.** The list shows 50 per page and ignores `pageNumber`
URL params; only the bottom-right `Next Page` button pages. Because the
grid virtualizes, `[role=row]` never yields more than about a dozen
rows, so enumerate from the bulk download (§ 2). Paging `Next Page`
until you hold `aria-rowcount` ids is the backstop when Bulk Operations
is unavailable.

**The grid's own inline "导出 / Export" button is page-scoped.** Its
menu item "当前表格数据 / current table data → 下载" exports only the ~50
rows of the current page, not the account, and after it the grid's
"下一页 / Next Page" can refuse to advance. Use Bulk Operations (§ 2) for
the account. After any download, compare its campaign-row count with
`aria-rowcount`; a file of about 50 rows is the page-scoped one.

Amazon ships more than one list layout (grid vs card, different class
names, LTR/RTL). If `[role=grid]` or the ✕ is not found, the invariant
still holds: find that layout's total-count element and search-clear
control, and trust the total over any DOM row count.

## 2. Bulk download (the account-wide export)

One XLSX covering every campaign in the selected marketplace, with both
the targeting rows and (if ticked) the search-term reports for the same
window. Script support for parsing it (`inspect`, `scope`, …) is in
[`bulk-operations.md`](bulk-operations.md).

**Reuse the newest existing export first.** The Bulk Operations page
keeps recent completed jobs with live download links, and the
shadow-root walk below finds them. Run it: it clicks the newest link
(`a[0]`) and returns its filename. The filename encodes the metric
window — `bulk-<entity>-<START>-<END>-<epoch>.xlsx`
(`…-20260602-20260702-…` = Jun 2–Jul 2). If that window covers yours,
you are done; reusing a file avoids the slow, flaky download modal. A
short window is not a 30-day analysis: sales attribute late, so a 2-day
file shows `spend > 0, sales = 0` for campaigns that are fine. The
Campaign Manager date picker defaults to 2 days, so set 30 days before
generating a fresh export.

**Generating a fresh export** (only when no recent file covers the
window). The `Download campaigns` (`下载广告活动`) trigger is inside the
`<bulk-storm-dashboard>` shadow DOM, so a top-level
`querySelector('button')` never finds it and a click on a look-alike
top-level element silently does nothing. It is the dashboard's second
header button (1st = Upload, 2nd = Download): walk shadow roots (same
`walk()` as below) and `el.click()` it, or `click_at_xy` its rect
centre. The structural anchors for the whole page are in
[`bulk-operations.md` § The Bulk Operations UI](bulk-operations.md#the-bulk-operations-ui-drive-it-by-language-independent-selectors).
The modal's checkboxes (default all on for "Enabled & Paused") group
the export's data: terminated / paused / zero-impression campaigns,
placement data, per-type campaign data, search-term data, budget rules.
The observed order is the anchor table's row in
[`bulk-operations.md` § The Bulk Operations UI](bulk-operations.md#the-bulk-operations-ui-drive-it-by-language-independent-selectors) —
select by position, but confirm the boxes you see before clicking: the
two search-term boxes sit immediately before the last one (Budget
Rules).

Tick both search-term boxes for an audit: the workbook then carries
`SP Search Term Report` and `SB Search Term Report` sheets for the same
window as the targeting rows, so the two layers of every campaign come
from one file.

Click the modal's submit (`Download` / `下载` / `下載`). The job takes
5–15 min for ~150 campaigns. Poll in short separate calls (re-open the
bulk-operations URL every 20 s or so) rather than one long sleep. When
the row's Status reads `Success`, download it through the anchor,
matched by href. The visible "下载 / 下載 / Download" text in that cell
is a `<p>` label; clicking it works on macOS but silently does nothing
on Ziniao / native Windows. The real link is an
`<a download href="…/bulk-operations/download/…xlsx">`:

```bash
browser-use <<'PY'
js(r"""
(function(){
  function walk(root,acc){root.querySelectorAll('*').forEach(function(e){
    if(e.shadowRoot)walk(e.shadowRoot,acc);
    if(e.tagName==='A'&&/bulk-operations\/download\//.test(e.href||''))acc.push(e);
  });return acc;}
  var a=walk(document,[]);
  if(!a.length)return 'NO_LINK';   // newest job is a[0]
  a[0].click();                    // real <a download> click → fires the download
  return 'CLICKED '+a[0].href.slice(-48);
})()
""")
PY
# Alternative: return a[0].href and new_tab(href). Chrome aborts the
# "navigation" to start the download, so you get a benign
# net::ERR_ABORTED — the file still lands.
```

The file lands in `~/.vibe-seller/downloads/<slug>/` (the CDP proxy pins
Chrome's download path there on every platform), under Amazon's own name
`bulk-<entity>-<YYYYMMDD>-<YYYYMMDD>-<epochms>.xlsx`. Take the newest
xlsx once any `*.crdownload` is gone:

```bash
ls -t ~/.vibe-seller/downloads/<slug>/*.xlsx 2>/dev/null | head -1
```

The workbook has 9 sheets. For SP the useful ones are
`Sponsored Products Campaigns` (one row per entity: Campaign / Bidding
Adjustment / Ad Group / Product Ad / Keyword / Negative Keyword /
Product Targeting / Negative Product Targeting) and
`SP Search Term Report`. Every target row carries `Ad Group ID` /
`Ad Group Name`; the search-term rows carry `Ad Group ID`, `Keyword ID`,
`Keyword Text` and `Match Type`, so each query is attributable to the
target that matched it.

**Export gotchas:**

- The date range decides whether structure is emitted, not only
  metrics: a paused campaign with no recent activity can be dropped
  entirely even with "Paused campaigns" and "zero impressions" ticked.
  Read such a campaign from its UI tabs (§ 6 CSV export).
- An auto campaign's `complements` group can be missing from the export
  when it sits at the ad-group default while the other three groups have
  their own bids — 3 product-targeting rows in the file, 4 in the UI.
  Cross-check auto campaigns in the UI.

**When Bulk Operations is missing.** Some marketplaces have no Bulk
Operations page (observed on `advertising.amazon.ae`: the host-root form
and every prefixed variant 404), and some consolidated accounts show no
nav link for it. Confirm with the host-root URL before concluding — the
prefixed forms 404 everywhere. The 404 is not transient, and it says
nothing about whether the marketplace has campaigns: each marketplace is
its own advertising account, so an export from one says nothing about
another. Routes that work without it:

1. **Campaign detail pages** — `/cm/sp/campaigns/<id>?entityId=…` and
   `/cm/sb/campaigns/<id>?entityId=…` render, and `document.body.innerText`
   carries spend, sales, orders, ACOS and ROAS with no virtualized table.
2. **The unified campaign manager on a sibling site** —
   `advertising.amazon.<other-tld>/campaign-manager/all-campaigns` lists
   several countries' campaigns with a Country column; page it the § 1
   way.
3. **The reports page** — `/reports?entityId=…` can build a Sponsored
   Products Advertised Product report.
4. **The grid itself**, once § 1's search and status filters are provably
   cleared: page `Next Page` until you hold `aria-rowcount` ids.

## 3. Campaign list view and per-campaign metrics

Reach the list through the Campaign Manager link in Seller Central's
menu (see `amazon-shared`).

- **Date range picker** in the page header, with presets `Today`,
  `Yesterday`, `Last 7 days`, `Last 30 days`, `Last Week`, `This Week`,
  `Last Month`, `This Month`, `Year to Date`, `Lifetime`.
- **Table-level date filter** at the bottom of the table near Export,
  independent of the header one; it drives the table, the header one
  drives only the top tiles.
- **Tuning filter chips** above the table: `Targets with impressions and
  no sales`, `Targets with clicks and no orders`, `Targets with
  conversions`.
- **Default columns**: `Active`, `Campaign name`, `Status`, `Type`,
  start/end date, `Campaign budget amount`, `Top-of-search impression
  share`, `Top-of-search bid adjustment`, `Clicks`, `CTR`, `Total cost`,
  `CPC`, `Purchases` (= orders), `Sales`, `ACOS`, `ROAS`.
- **Status values**: `Delivering`, `Out of budget`, `Paused`, `Ended`,
  `Archived`. `Out of budget` means the daily cap was hit recently.
- The row-name link points to `/cm/sp/campaigns/<id>/ad-groups`.

The list's ag-Grid virtualizes columns as well as rows (about 6 of 17
columns exist in the DOM at a time), so a single `js()` read of every
column is unreliable. For per-campaign figures either use the bulk
download (better beyond ~20 campaigns) or collect the campaign URLs and
read each detail page's top tiles:

```bash
# Step 1 — campaign deep links from the list (the name column is pinned
# left, so this works at any horizontal scroll). sp / sb / sd = Sponsored
# Products / Sponsored Brands incl. video / Sponsored Display.
browser-use <<'PY'
js("""
var urls = [];
document.querySelectorAll('a[href*="/cm/sp/campaigns/"], a[href*="/cm/sb/campaigns/"], a[href*="/cm/sd/campaigns/"]')
  .forEach(a => { if (a.href && !urls.includes(a.href)) urls.push(a.href); });
return JSON.stringify(urls);
""")
PY
# Save to ./campaign-urls.json.

# Step 2 — per URL: SP detail pages expose the four top tiles as chart
# toggle buttons whose innerText carries label + value.
browser-use <<'PY'
new_tab("<url>")
wait_for_load()
js("""
return (() => {
  var r = {};
  document.querySelectorAll('button[pressed], button[aria-pressed="true"]').forEach(b => {
    var t = b.innerText.trim();
    if (t) r['metric_' + Object.keys(r).length] = t;
  });
  return JSON.stringify(r);
})();
""")
PY
# metric_0..3 = Total cost / Sales / ROAS / Purchases (fixed order).
```

**SB / SBV detail tiles are static text**, label on one line and the
value on the next with a `TOTAL` / `AVERAGE` suffix and the currency or
`%` embedded (`USD96.00TOTAL`, `80.01%AVERAGE`). The pressed-button read
finds almost none of them; scan line pairs instead:

```bash
browser-use <<'PY'
js("""
return (function() {
  const lines = document.body.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
  const tiles = {};
  for (let i = 0; i < lines.length - 1; i++) {
    const labelLooksOk = /^[A-Z][A-Za-z ]{2,30}$/.test(lines[i]);
    const m = lines[i + 1].match(/^([A-Z]{3}\\s*)?([\\d,\\.]+)%?(TOTAL|AVERAGE)$/);
    if (labelLooksOk && m) tiles[lines[i]] = lines[i + 1];
  }
  return JSON.stringify(tiles);
})()
""")
PY
```

Strip the suffix and currency prefix. The tile set varies with the SB
goal / cost type, so don't hard-code it.

## 4. Date pickers

Every page has its own picker and the defaults differ per page and per
campaign; setting the range on the list does not carry into detail
pages, the Targeting tab, the Search-terms tab or Bid adjustments. Pin
the same window on every page you read for one analysis, otherwise
placement rows will not sum to the campaign and the search-term layer
will not reconcile with the targeting layer.

Applying a preset takes up to three steps:

```bash
# 1. Open the picker (its button shows the current range)
browser-use <<'PY'
js("""
return (() => {
  var d = null;
  document.querySelectorAll('button').forEach(function(b) {
    if (b.innerText.match(/<current displayed date string>/)) d = b;
  });
  if (d) { d.click(); return 'opened'; }
})()
""")
PY

# 2. Click the preset: its text is a leaf inside a clickable parent.
# Two pickers can coexist (table filter + chart range); offsetParent
# picks the visible one.
browser-use <<'PY'
js("""
return (() => {
  var leaf = Array.from(document.querySelectorAll('*')).find(function(e) {
    return e.children.length === 0 &&
      (e.textContent || '').trim() === 'Last 30 days' &&
      e.offsetParent !== null;
  });
  if (leaf) {
    (leaf.closest('button, kat-button, [role=button]') || leaf).click();
    return 'clicked';
  }
})()
""")
PY

# 3. Apply — only the campaign-manager list needs it; detail pages
# apply on the preset click.
sleep 3
browser-use <<'PY'
js("""
return (() => {
  var b = Array.from(document.querySelectorAll('button'))
    .find(function(x) { return x.innerText.trim() === 'Apply' && x.offsetParent !== null; });
  if (b) { b.click(); return 'apply'; }
  return 'auto-applied';
})()
""")
PY
```

Read back the window a page is showing; the pattern differs by page
type, so try these in order:

```bash
browser-use <<'PY'
js("""
return document.body.innerText.match(/Date range:\\s*[A-Za-z0-9 ,\\-–]+\\d{4}/)?.[0] ||
document.body.innerText.match(/Data ranges from\\s+(\\d{4}-\\d{2}-\\d{2}).*?to\\s+(\\d{4}-\\d{2}-\\d{2})/s)?.[0]?.replace(/\\s+/g,' ') ||
document.body.innerText.match(/\\d{1,2}\\s+[A-Z][a-z]{2}\\s*[-–]\\s*\\d{1,2}\\s+[A-Z][a-z]{2},?\\s*\\d{4}/)?.[0] ||
'unknown';
""")
PY
```

The first matches SB detail pages (`Date range: 1 Apr - 30 Apr 2026`),
the second SP detail pages (chart ARIA text `Data ranges from
2026-04-01 00:00:00 to 2026-04-30 00:00:00`), the third is a fallback.

A campaign created after the range ends shows `—` for every metric;
widen the range to include its start date.

## 5. Campaign and ad-group pages

**Campaign settings tab** (`…/cm/sp/campaigns/<id>/settings?entityId=…`)
— page text in order:

```
Campaign:<name>
Status:<状态>
Type:Sponsored Products - Manual|Auto
Country:<country>
Schedule:<start date> - <No End Date|end date>
Budget:<currency><amount> - Daily
…
Campaign ID
<id>
Portfolio
<No portfolio|name>
…
Bid optimization
<Dynamic (down only)|Dynamic (up and down)|Fixed bids>
…
Top of search (first page)
adjust value %
Product detail pages
adjust value %
Rest of search
adjust value %
```

`Schedule` shows one date when there is no end date. A blank placement
adjustment is 0%, not unset.

**Campaign page** (`/cm/sp/campaigns/<id>/ad-groups`) — top: four tiles
(Total cost, Sales, ROAS, Purchases) and a chart. Left sidebar:

- **Ad groups** (landing) — per-group default bid, targets, products,
  cost, purchases, sales, ACOS, ROAS.
- **Bid adjustments** — placement modifiers (§ 8).
- **Negative targeting** — campaign-level negative keywords / products.
- **Budget rules** — scheduled budget bumps for events.
- **Campaign settings** — name, schedule, budget, bidding strategy.
- **History** — change log.

These sidebar entries are shadow-DOM `kat-button`s whose handlers ignore
a `js()` click; read the rect and `click_at_xy`.

**Ad-group page** (`…/ad-groups/<agId>/ads`) — sidebar: **Ads**
(advertised SKUs), **Targeting**, **Negative targeting**, **Search
terms**, **Ad group settings** (default bid), **History**. The tabs are
JS-routed buttons without hrefs, so either `click_at_xy` them or open
the deep link:

```
…/cm/{sp|sb}/campaigns/<campId>/ad-groups/<agId>/targeting?entityId=…
…/cm/{sp|sb}/campaigns/<campId>/ad-groups/<agId>/search-terms?entityId=…
```

Ad-group ids come from the campaign page's links. An auto campaign's ad
group id always differs from its campaign id
(`…/ad-groups/<campId>/targeting` renders blank), so read it:

```bash
browser-use <<'PY'
js("""
var agIds = [];
document.querySelectorAll('a[href*="/ad-groups/"]').forEach(function(l) {
  var m = l.href.match(/\\/ad-groups\\/([^?/]+)/);
  if (m && agIds.indexOf(m[1]) === -1) agIds.push(m[1]);
});
return JSON.stringify(agIds);
""")
PY
```

Campaigns can hold several ad groups; iterate all of them. Most SP-Auto
and SBV campaigns have one.

## 6. Targeting tab

**Prefer the per-table CSV export.** The tab virtualizes (about 16 of
20 rows in the DOM) and in-table scrolling does not always materialize
the rest. The **Export** button top-right of the targets table writes
`Sponsored_Products_Target_<date>.csv` to
`~/.vibe-seller/downloads/<store>/`, with `State`, `Keyword`,
`Target match type`, `Status`,
`Suggested bid (low|median|high)(<currency>)`, `Bid (<currency>)` and
the metrics. Read the currency from the header, don't assume one. It
works for keyword, category and auto-target tables, and the table
footer's `Total: N` is the row count to match.

SP-Manual's Targeting grid does not render match type at all, so the
CSV (or the bulk download) is also how you get it.

**Reading the grid directly.** Keyword text sits in a pinned-left
container (`.ag-pinned-left-cols-container`), metrics in the centre
container (`.ag-center-cols-container`); a row's `innerText` misses the
keyword. Merge them by `row-index`:

```bash
browser-use <<'PY'
js("""
return (() => {
  var pc = document.querySelector('.ag-pinned-left-cols-container');
  var pr = pc ? pc.querySelectorAll('[role=row]') : [];
  var bc = document.querySelector('.ag-center-cols-container');
  var br = bc ? bc.querySelectorAll('[role=row]') : [];
  var result = [];
  pr.forEach(function(row) {
    var idx = row.getAttribute('row-index');
    if (idx) {
      var kw = row.innerText.trim().replace(/\\n/g, ' ');
      br.forEach(function(b) {
        if (b.getAttribute('row-index') === idx) {
          var met = b.innerText.trim().replace(/\\n/g, ' | ');
          if (met) result.push(kw + ' ||| ' + met);
        }
      });
    }
  });
  return result.join('@@@');
})()
""")
PY
```

Use `@@@` as the row delimiter: `js()` output is line-oriented and
newlines lose rows in shell pipelines.

**SP-Manual-Keyword cell layout.** Split the metric string on ` | ` and
index by position, because the suggested bid comes before the bid — a
"first currency value" regex returns the suggested midpoint, not the
bid:

```
0  Status                 ("Delivering" | "Paused" | ...)
1  (empty spacer)
2  Suggested bid + range  ("USD 0.60 (USD 0.45-USD 0.75)")
3  Apply button
4  Rules indicator        ("—" when none)
5  Bid                    ("USD 3.00")   ← the bid
6  Bid (duplicate render)
7  Clicks
8  Spend
9  Orders
10 Sales
11 ACOS
12 ROAS
```

Example row: `wireless mouse ||| Delivering |  | USD 0.60 (USD 0.45-USD 0.75) | Apply | — | USD 3.00 | USD 3.00 | 200 | USD 500.00 | 30 | USD 1,200.00 | 41.67% | 2.40`
→ bid 3.00, suggested 0.45–0.75 (mid 0.60), clicks 200, spend 500.00,
orders 30, sales 1200.00, ACOS 41.67%, ROAS 2.40, actual CPC = 500 / 200
= 2.50. Paused rows with no clicks show `—` in positions 7–12; record
`—`, not 0.

**The metric columns after Bid differ by marketplace** — both order and
which metrics appear (one marketplace shows `Clicks | Total cost |
Purchases | Sales | ACOS | ROAS`, another `ACOS | Clicks | Purchases |
ROAS | Impressions | Total cost`). Before indexing, read the header row
and map columns by header text on each marketplace.

**SP-Auto: the four auto-target groups.** Rows are `[role=row]` (no
`<table>`); group-label rows (Close match / Substitutes / Loose match /
Complements — match by label, the order varies) and data rows are
siblings paired by position. The
pinned/centre merge above also works. Positional read, valid when the
last seven cells are bid / clicks / spend / orders / sales / ACOS / ROAS
(check the header first, as above):

```bash
browser-use <<'PY'
js("""
return (function() {
  const rows = [...document.querySelectorAll('[role=row]')];
  const LABELS = ['Close match', 'Loose match', 'Complements', 'Substitutes'];
  const labelRows = rows.filter(r => LABELS.includes(r.innerText.trim()));
  const dataRows = rows.filter(r => {
    const t = r.innerText.trim();
    return (t.startsWith('Delivering') || t.startsWith('Paused'));
  });
  return JSON.stringify(labelRows.map((lr, i) => {
    const cells = (dataRows[i]?.innerText || '').split('\\n').map(c => c.trim()).filter(Boolean);
    return {
      group:  lr.innerText.trim(),
      status: cells[0],
      bid:    cells.at(-7), clicks: cells.at(-6), spend: cells.at(-5),
      orders: cells.at(-4), sales:  cells.at(-3), acos:  cells.at(-2),
      roas:   cells.at(-1)
    };
  }));
})()
""")
PY
```

**SB / SBV Targeting.** A ReactVirtualized dual-pane grid, not ag-Grid:
`grids[2]` is the keyword pane, `grids[3]` the metrics pane. Paused rows
insert a `Details` token after the status; skip such tokens by value,
not by offset. Columns in order: keyword / match type / status /
top-of-search IS% / CPC / clicks / spend / orders / sales / ACOS / ROAS
/ NTB% (new-to-brand, SB only). `grids[2]/grids[3].innerText` already
contains rows below the fold, so one read gets them all. Deep-linking to
an SB keyword page leaves the grid empty; arrive by clicking through
(see [`mechanics-edit.md` § 8](mechanics-edit.md#8-sponsored-brands-sb--sbv-bid-edit-and-pause)).

```bash
browser-use <<'PY'
js("""
return (function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const kwLines = (grids[2]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const metLines = (grids[3]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const keywords = kwLines.filter(l => l !== 'select');
  const MATCH = new Set(['Phrase', 'Exact', 'Broad']);
  const rows = [];
  let i = 0;
  while (i < metLines.length && rows.length < 200) {
    if (!MATCH.has(metLines[i])) { i++; continue; }
    const match_type = metLines[i++], status = metLines[i++];
    if (metLines[i] === 'Details') i++;
    if (metLines[i] === 'No current data') i++;
    if (metLines[i] === 'adjust value') i++;
    if (/^[A-Z]{3}$/.test(metLines[i])) i++;   // bare currency token
    rows.push({
      keyword: keywords[rows.length] || null,
      match_type, status,
      top_search_IS: metLines[i++], cpc: metLines[i++], clicks: metLines[i++],
      spend: metLines[i++], orders: metLines[i++], sales: metLines[i++],
      acos: metLines[i++], roas: metLines[i++], ntb: metLines[i++]
    });
  }
  return JSON.stringify(rows);
})()
""")
PY
```

The campaign header's keyword count ("13 keywords") is the number your
read must reach.

## 7. Search-terms tab

Reach it from the ad group's `Search terms` sidebar entry
(`click_at_xy`) or the deep link in § 5.

**For the full set use an export.** The grid holds about 13–16 rows of
what is often 200+ terms, and scroll capture misses rows. In order of
preference:

1. The bulk download's `SP Search Term Report` / `SB Search Term Report`
   sheets (§ 2) — the whole account, same window as the targeting rows.
   Filter by `Campaign ID`.
2. Per campaign: set the date range first, to exactly the Targeting
   tab's window, then click **Export** (top-right of the table); the CSV
   lands as `~/.vibe-seller/downloads/<store>/Sponsored_Products_SearchTerm_*.csv`.
   It has no match-type column; join its `Keywords` column to the
   targeting table to attribute source keyword and match type.

Sum the search-term spend and compare it with the targeting spend for
the same campaign and window; what the two should agree to, and what a
gap means, is in [`output-spec.md`](output-spec.md).

Columns: `Actions`, `Added as`, `Customer search term`, `Keywords`
(match type beneath), `Target bid`, `Clicks`, `Total cost`,
`Purchases`, `Sales`, `ROAS`.

**ASIN rows.** A search term of the form `B0` + 8 characters is a
product-page placement, not a typed query. On SP-Auto the cell holds the
product title followed by `ASIN : <B0…>`, so an anchored `^B0` regex
misses it; extract with `/ASIN\s*:\s*(B0[A-Z0-9]{8})/`. Record ASINs
upper-case.

**Grid read (SP).** Same pinned/centre layout as Targeting — the
pinned-left container holds the search term, the centre holds source
keyword, `Match type: Broad|Phrase|Exact`, bid and metrics:

```bash
browser-use <<'PY'
js("""
return (() => {
  var pc = document.querySelector('.ag-pinned-left-cols-container');
  var pr = pc ? pc.querySelectorAll('[role=row]') : [];
  var bc = document.querySelector('.ag-center-cols-container');
  var br = bc ? bc.querySelectorAll('[role=row]') : [];
  var result = [];
  pr.forEach(function(row) {
    var idx = row.getAttribute('row-index');
    if (idx) {
      var term = row.innerText.trim().replace(/\\n/g, ' ');
      br.forEach(function(b) {
        if (b.getAttribute('row-index') === idx) {
          var met = b.innerText.trim().replace(/\\n/g, ' | ');
          if (met) result.push(term + ' === ' + met);
        }
      });
    }
  });
  return result.join('@@@');
})()
""")
PY
```

**Grid read (SB / SBV).** Dual-grid again; the left pane is triples of
search term / target keyword / match type, the right pane a flat 5-cell
sequence (clicks / spend / orders / sales / ACOS — no ROAS):

```bash
browser-use <<'PY'
js("""
return (function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const left  = (grids[2]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const right = (grids[3]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const leftRows = [];
  for (let i = 0; i + 2 < left.length; i += 3) {
    leftRows.push({ search_term: left[i], target_keyword: left[i+1], match_type: left[i+2] });
  }
  const FIELDS = 5, metRows = [];
  for (let i = 0; i + FIELDS <= right.length; i += FIELDS) {
    metRows.push({ clicks: right[i], spend: right[i+1], orders: right[i+2], sales: right[i+3], acos: right[i+4] });
  }
  return JSON.stringify(leftRows.map((lr, i) => ({...lr, ...(metRows[i] || {})})));
})()
""")
PY
```

Both grids virtualize: compare what you read with the header's
`Total: N` and export (or scroll the inner container, § 9) when you are
short. The tail you missed is often where the negate and harvest
candidates are.

The per-row `Add as ⌄` dropdown (negate / harvest) is covered in
[`mechanics-edit.md` § 4](mechanics-edit.md#4-negating-search-terms-and-products).

## 8. Bid adjustments page (placements)

`/cm/sp/campaigns/<id>/bid-adjustments`, header text "Increase your bid
for specific placements. These placements include Amazon Business."
Rows: **Top of search (first page)**, **Rest of search**, **Product
pages**. Columns: Placement Name, Campaign bid strategy, Bid adjustment
(editable %), Impressions, Clicks, CTR, Total cost, CPC, Purchases,
Sales, ACOS.

This page has its own date picker. The placement rows sum to the
campaign's top tiles only when both are on the same range; a mismatch
is a date problem, not an attribution gap. So:

1. Read the range in this page's header.
2. Compare it with the range you read the campaign tiles on.
3. If they differ, set this page's picker to match (§ 4) and wait for
   the table to refresh.
4. Sum Purchases / Sales / Total cost across placements and check they
   equal the campaign tiles; if not, the dates still differ or the
   table has not refreshed.

Editing the % is in [`mechanics-edit.md` § 7](mechanics-edit.md#7-placement-bidding-strategy-and-budget-edits).

## 9. Scroll-and-accumulate for virtualized grids

Some grids (notably Sponsored Brands Video targeting on some layouts)
render rows on demand, so a `[role=row]` read returns only the visible
6–10. Scroll the grid container in chunks, read after each scroll, and
de-duplicate by `row-index`:

```bash
browser-use <<'PY'
js("""
return (async () => {
  const grid = document.querySelector('.ag-center-cols-container, [role=grid] [role=rowgroup]');
  if (!grid) return JSON.stringify({error: 'no grid'});
  const scroller = grid.closest('.ag-body-viewport, [role=grid] [data-virtual]') || grid.parentElement;
  const collected = new Map();   // row-index → cell text
  const scrape = () => {
    document.querySelectorAll('[role=row][row-index]').forEach(r => {
      const idx = r.getAttribute('row-index');
      if (!collected.has(idx)) collected.set(idx, r.innerText.replace(/\\n/g,' | ').trim());
    });
  };
  scrape();
  let last = 0, stable = 0;
  while (stable < 3 && scroller.scrollTop < scroller.scrollHeight) {
    scroller.scrollTop += 200;
    await new Promise(r => setTimeout(r, 250));
    scrape();
    if (collected.size === last) stable++; else { stable = 0; last = collected.size; }
  }
  return JSON.stringify({rows: collected.size, data: [...collected.entries()]});
})()
""")
PY
```

Treat each row like the Targeting merge output (§ 6). The count in the
campaign header is the target; if you have fewer rows, scroll again.
`document.querySelector(".ag-body-viewport").scrollTop = N` scrolls an
ag-Grid body directly. Page-level scrolling does not move these inner
viewports.

## 10. Brand Analytics ASIN search-term report

Useful when you need keyword ideas for a specific listing — e.g. what
to target after pausing a campaign whose traffic dried up. Use the
**ASIN** report, not the category one: category reports return generic
high-volume terms with no tie to the listing.

Brand Analytics is on `sellercentral.amazon.<tld>`, not the ad console.
Both share the signed-in Amazon account; if Seller Central lands on
`/ap/signin`, run the `amazon-shared` § 2 sign-in loop. It needs Brand
Registry but no extra OTP beyond sign-in.

```
Landing:   https://sellercentral.amazon.<tld>/brand-analytics/dashboard
Reports:   https://sellercentral.amazon.<tld>/brand-analytics/<report>
           search-terms        (category report)
           search-terms-asin   (ASIN reverse lookup)
           item-comparison | demographics | repeat-purchase
```

If `/search-terms-asin` 404s in a locale, go through the dashboard:
left nav "Top Search Terms" / "搜索词排名" → toggle Category ↔ ASIN.

1. Open the dashboard (sign in if redirected; if Amazon asks for an
   authenticator-app code you do not have, ask the user with
   `AskUserQuestion`).
2. Open the ASIN report, filter by the listing's ASIN (from the
   campaign's Ads tab or the bulk export's Product Ad rows) and the
   last 30 days, and run it.
3. Export (CSV/TSV); it lands in `~/.vibe-seller/downloads/<slug>/`.
4. Keep a copy at
   `store-data/<slug>/ads/brand-analytics/<ASIN>_<YYYY-MM-DD>.tsv`
   (a run-data capture, not one of the audit files in
   [`data-contract.md`](data-contract.md) — the `#` preamble below is
   fine for it), normalised to:

   ```
   # asin: B0EXAMPLE1
   # captured: <YYYY-MM-DD>
   # source: sellercentral.amazon.<tld>/brand-analytics/search-terms-asin
   asin	rank	query	click_share	conversion_share
   ```

A capture younger than 7 days can be reused. An empty result is still
an answer (low-traffic or new listing) — keep the header-only file. If
the page says Brand Analytics is not enabled (no Brand Registry), say so
in the report rather than citing it.
