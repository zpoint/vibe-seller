---
name: noon-ads
description: "Noon Ad Manager — campaigns (Auto / Manual / Brand), tuning audits, keyword research, negatives, Vantage analytics. Covers Targets / Customer Queries / Export Data flows. Load for any noon ads work — review, audit, tune, create. References under references/ for the tuning playbook (ads-tuning.md), creation guide (ads-creation.md), and keyword research (ads-keyword-research.md)."
requires: [noon-shared]
gates: [ad_completeness_review, ad_negation_allowlist, ad_execution_fidelity]
---

# Noon — Ad Manager

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, and common patterns.

## 0. FIRST — declare what this task is for

**Before any browser action, call `vibe_seller_declare_ad_task`.** This is
a precondition, not a courtesy: a report with no declaration behind it is
refused.

Two things follow from the declaration and cannot be changed afterwards —
how much the completeness gate asks of you, and whether the user gets a
review console. Read them off what the person actually asked for, not off
what you expect to find once you look.

| They asked for | `kind` | `scope` |
|---|---|---|
| "审计一下广告" / "review our ads" | `audit` | omit `combos` — whole store |
| "复核 widget-006 在 amazon 的广告" | `audit` | that platform+country, those campaign ids |
| "把这三个词的出价降下来" | `audit` | those campaigns |
| "帮 widget-006 建关键词广告" | `create` | the market you are creating in |
| "执行刚才确认的调整" | `execute` | the campaigns being changed |
| "我们 SA 的 ACOS 大概多少" | `investigate` | the market asked about |

**There is no `edit` kind.** A request to change specific bids is an
`audit` whose scope names those campaigns — the user still reviews the
change before it is applied, and the scope is what makes it small.

**The scope trap:** omitting `combos` means EVERY marketplace this store
sells on, and you will be held to all of them. Omit it only when the
request really is store-wide. A store selling on five marketplaces has
been asked for all five because a one-product task left the field out.

**If the request names a product, not a campaign** ("widget-006 的广告"),
resolve it first: enumerate the campaign list, find the campaigns carrying
that SKU family, then declare those ids. A product name alone narrows
nothing — put it in `products` for the human reading the review page, and
put the ids in `campaigns`.

**Once per user turn, and it cannot be revised.** If a gate later asks for
something outside your declared scope, do NOT try to re-declare — the
server refuses it. Say so in your result and let the user redirect you.

**When the user sends a NEW message that changes what you are doing,
declare again.** That is a new phase, and it is the only way a
declaration changes. Two cases you will hit often:

- "现在把刚创建的广告复核一下" after a `create` phase → declare `audit`,
  and its scope may name the campaigns you created earlier in this same
  task.
- The review console submits the user's decisions back as a follow-up
  message → declare `execute`, scoped to the campaigns that submission
  actually names.

---

Mechanics for noon Ad Manager. The actual *thinking* — when to
create / tune / kill a campaign, how to research keywords — lives
in the three reference files (see § 11).

**URL**: `https://admanager.noon.partners/en-{cc}/home?mpCode=noon&project=PRJ{project_id}`

Left nav is an **icon rail** (not a text `ul[role=menu]`): **Campaigns**,
**Recommendations**, **Budget**, **Vantage**, **Billing**, **Settings**
— the labels render as `role=menuitem` spans, so match by text, not by
`ul[role=menu] a`.

> ⚠️ **The Ad Manager was redesigned (verified live 2026-07-21).** The
> `/home` landing is now an **Overview** dashboard (KPI panels +
> promos), NOT the campaign list. The "Ad Manager" card carries three
> tabs — **Overview | Performance | Campaigns** — and the campaign list
> lives under the **Campaigns** tab at `…/home?…&tab=campaigns`. A page
> that also shows a **Sale Event Optimizer** widget (event budget/bid
> boosts) and a **Recommendations** panel is the current build. See § 2
> for how to enumerate the list — the old paginator is gone.

## 1. Campaigns Overview Metrics

Overview-tab KPI cards:

| Metric | Formula / Notes |
|--------|-----------------|
| ROAS | Return on Ad Spend = Revenue / Spends |
| Revenue | Total ad-attributed revenue |
| Spends | Total ad spend |
| eCPC | Effective Cost Per Click |
| CTR | Click-Through Rate = Clicks / Views |
| Orders | Ad-attributed orders |
| Clicks | Total clicks on ads |
| Views | Total ad impressions |
| ATC | Add To Cart count |
| CvR | Conversion Rate (shown on campaign detail) |

A time-series performance chart below lets you toggle any metric.

## 2. Campaign List — Filters, True Totals, Full Enumeration

Open the **Campaigns** tab (`…/home?…&tab=campaigns`). Filters above
the list:

- **Search** box (by campaign name)
- **Date range** (default `Last 30 days`)
- **Ad Type**: `All types` / `Product` / `Brand` / `Display`
- **Targeting**: `All targeting` / `Auto` / `Manual`
- **Status count control** (segmented): `Live N` · `Paused N` · `All N`
  + a `More status filter` dropdown. The counts are the **true totals**
  for the current filter — read them directly; there is no page math.
- **Export all campaigns** (top-right of the list) — a list-level bulk
  export (distinct from the per-tab Export Data in § 7).

Columns (horizontally scrollable): Campaign, Status, Budget, Revenue,
ROAS, Ad Spend, eCPC, Orders, Views, Clicks, ATC, … Actions.

### Enumerate EVERY campaign — the paginator is gone

The Ant-Design pager (`li.ant-pagination-item-N`, "15 items per page")
**no longer exists**. The list is now a **lazy-loaded, inner-scroll
table**: only ~15–20 rows render on first paint, and **`window.scroll`
does nothing** — you must scroll the list's own container until every
row loads. Skipping this silently under-counts: an unscrolled read that
sees the first ~20 rows can miss more than half the Live campaigns when
the `Live N` chip is much larger, which then fails the completeness
gate.

> ⚠️ **Enumerate LIVE from the scrolled list — never from a pre-existing
> or downloaded export file.** A leftover `Campaign_*.csv` /
> `Export all campaigns` file in `~/.vibe-seller/downloads/` (from a
> prior run, or a first-paint export before you scrolled) captures only
> the rows that were loaded when it was written — typically the first
> ~20. Drilling that file makes the audit look done at `20/20` while the
> account has far more Live campaigns. (Live failure this fixes: an agent
> reused a 20-row export and reported noon SA `20/20` when the `Live`
> chip showed **45**.) **Any campaign set whose count is below the
> `Live N` chip is stale — re-enumerate by scrolling (below); and if you
> do use `Export all campaigns`, first scroll the list fully, then verify
> the file's row count equals the chip before trusting it.**

**Which countries you owe is fixed by `./AUDIT_TARGETS.json`** — the
server writes it at the task root before you start (`{"combos":
[{"platform": "noon", "country": "AE"}, …]}`, straight from the store's
Settings). Read it FIRST, at the start of Phase 1, and loop over it:
every noon country in it needs its own `AUDIT_SCOPE.json` combo entry
(step 4) AND its own `## noon <CC>` report section. A country with
genuinely no Live campaigns is still written down — an entry with
`"active_ids": []` and `"total_active": 0`, plus a section saying so;
可以为空，但不能不写。Omitting a declared combo is a `[基线]` gap that
blocks submission.

Phase 1 (Discover) MUST, per country:

1. **Read the true total** from the status chips — the `Live N` / `All N`
   numbers are your completeness target. The chip *label* (`Live` today)
   is English on the noon partner console, but rely on the **number** and
   the **`/campaign/details/` links** — both language-neutral — so this
   works whatever the seller-market locale; don't key on the label word:
   ```bash
   browser-use <<'PY'
   print(js("return JSON.stringify([...document.querySelectorAll('*')].filter(e=>e.children.length<=2 && /^(Live|Paused|All)\\s*\\d+$/i.test(e.textContent.replace(/\\s+/g,' ').trim())).map(e=>e.textContent.replace(/\\s+/g,' ').trim()))"))
   PY
   ```
2. **Scroll the list container to the bottom** (match its class by the
   `CampaignListRevamp_` prefix — the hashed suffix changes per build;
   fall back to any inner `overflow-y:auto` scroller taller than its
   viewport). Repeat until the campaign-link count stops growing:
   ```bash
   browser-use <<'PY'
   for _ in range(12):
       js("[...document.querySelectorAll('*')].filter(e=>{var s=getComputedStyle(e);return (s.overflowY==='auto'||s.overflowY==='scroll') && e.scrollHeight>e.clientHeight+50;}).forEach(e=>e.scrollTop=e.scrollHeight)")
       wait(1)
   print("links:", js('return document.querySelectorAll("a[href*=\\"/campaign/details/\\"]").length'))
   PY
   ```
3. **Extract IDs** with the still-valid `a[href*="/campaign/details/"]`
   read (§ 3) and de-dupe. Only when the distinct count matches the chip
   total (e.g. `Live N`) is the manifest complete. Re-run this whole
   loop **after every country switch** (`/en-{cc}/`).
4. **Persist the scope — required for audits.** Append this combo to
   `./AUDIT_SCOPE.json` at the task root, with the de-duped ids **and**
   the `Live N` chip number:
   ```json
   {"combos": [
     {"platform": "noon", "country": "AE",
      "total_active": 45,
      "total_active_source": "chip:Live 45",
      "active_ids": ["C_DEMO0001", "C_DEMO0002"]}
   ]}
   ```
   The server requires `total_active == len(active_ids)`, and rejects the
   scope when they disagree. That is deliberate: the chip is rendered by
   the server and does **not** depend on how far you scrolled, so a
   half-scrolled list (20 ids, chip 45) is caught as stale instead of
   being accepted as a complete `20/20` audit. If they disagree, keep
   scrolling — don't "fix" it by editing the number down. Every id you
   list must then get its own `### <id> | … ` drill block in the report.
   Full field reference: `amazon-ads/references/audit-quickref.md` Step 1.

   **`total_active_source` is required, and for noon it is the chip
   reading** — `"chip:Live N"`, the number you read in step 1. Without
   it the combo is a `[基线]` gap. Reason: `total_active ==
   len(active_ids)` only proves the two numbers agree, not that either
   was *observed* — trivially true when both come from the same parse
   (observed live: a run declared a 12-campaign marketplace as `4/4`
   because its script silently dropped files it couldn't read, and every
   check passed). The chip form isn't verifiable from disk the way
   Amazon's `"bulk:<file>.xlsx"` is, but writing the reading down turns
   an invented total from an omission into a claim the reviewer can check
   against the live page. Read the chip; don't back-fill it from the id
   count.

## 3. Campaign Detail Page

**URL**: `admanager.noon.partners/en-{cc}/campaign/details/{campaign_id}?mpCode=noon&project=PRJ{project_id}`

Campaign ID format: `C_{alphanumeric}` (10 alphanumerics after the
underscore, e.g. `C_XXXXXXXXXX`).

**Campaign ID extraction** from the campaigns list (IDs are in
`<a href>` attributes, not visible text):
```bash
browser-use <<'PY'
print(js("""
  var links = document.querySelectorAll('a[href*="/campaign/details/"]');
  var data = [];
  links.forEach(function(l) {
    var m = l.href.match(/\\/campaign\\/details\\/([^?]+)/);
    if (m) data.push({name: l.textContent.trim(), id: m[1]});
  });
  return JSON.stringify(data);
"""))
PY
```

> ⚠️ **The link extraction above only returns the rows currently
> rendered.** The list lazy-loads on inner scroll, so a raw read
> captures ~15–20 of what may be many more. **Enumerate the full set
> via the § 2 procedure** (read the `Live N` / `All N` chip totals, then
> scroll the list container until the distinct link count matches).
> Treat a single unscrolled read as a spot-check, never the full set —
> under-counting here fails the completeness gate.

**Campaign names can be misleading.** Verify actual products via
the **Products tab** — do not trust the campaign name. A campaign
named "mouse004 Auto" may target keyboard SKUs, not a mouse.

Header shows: campaign name, Status badge, Budget, **Top-of-Search
boost** (displayed as `Top Slot: N%` between Budget and Bidding
Strategy for manual campaigns with TOS configured), Bidding
Strategy, Running From date, Last Updated. **Top-right action icons:**
a date-range picker, **pause/resume**, **duplicate**, and a round
**blue pencil = Edit** (opens the campaign editor — see § 9 for
adding/removing keywords & negatives).

`target_filter` query param on the Targets tab switches the view:
`target_filter=all` (positive keywords, default) vs
`target_filter=negative` (negative keywords).

**Brand Ads have different CTR/ROAS norms.** Brand Video ads measure
view-through differently — never compare CTR directly to Product Ads.
A `brand video` CTR of 0.23% is not "weak" vs product ad peers at
1.7–6.7%. Compare Brand Ads only against other Brand Ads, or against
the brand ad's own historical ROAS.

**Brand Ad "Creative" row.** The Products tab may show a "Creative"
row (logo/video element) that accumulates clicks with zero attributed
orders — noon can't attribute conversions to the creative. If
Creative spend > 20% of campaign budget, flag it; calculate ROAS
both with and without the Creative row for true product performance.

KPI cards (same 10 metrics as overview, scoped to this campaign).

Performance chart with metric toggles: ROAS, Revenue, Spends, eCPC,
CTR, CvR, Orders, Clicks, Views, ATC.

**4 Sub-tabs on campaign detail:**

| Tab | Purpose |
|-----|---------|
| Products | SKUs in this campaign with per-SKU metrics |
| Placements | Ad placement performance |
| **Targets** | **Keywords** with match types and bids |
| Customer Queries | Actual customer search terms |

**Sub-tab access patterns.** The 4 sub-tabs have no stable selector
between page loads; click them by visible label via `js()`. Scroll
the tab bar into view first — `js("window.scrollBy(0, 800)")` for a
standard page, and ~1500px (roughly 2×) for **brand video pages**
because the embedded video player pushes the sub-tabs further down.
Then click a tab by its text:
```bash
browser-use <<'PY'
js("window.scrollBy(0, 800)")
js("Array.from(document.querySelectorAll('[role=tab],a,button')).find(e=>/^Products$/i.test(e.textContent.trim()))?.click()")
PY
```

**"No SKUs Found" on Products tab.** If a campaign is Live and
spending but Products shows "No SKUs found", the linked SKUs were
deleted or delisted. The Auto system continues to spend but cannot
attribute revenue. Check if some variants show "View Issues" vs
"Buy Box Won" — even one broken variant can tank campaign ROAS.

## 4. Targets Tab — Keywords & Bidding

**Goal**: capture every row, including the 0-view tail (typos,
sub-floor bids, idle keywords).

**Default to scroll+eval.** For typical Manual campaigns
(15–30 keywords) the DOM accumulates all rows on initial render;
a single `eval` walking `document.querySelectorAll('table tr')`
returns the full table. Verified live 2026-05-05: campaigns
with 24–25 keywords returned every row on first eval, no
scrolling needed.

```bash
browser-use <<'PY'
print(js('return JSON.stringify(Array.from(document.querySelectorAll("table tr")).map(r => Array.from(r.cells).map(c => c.innerText.trim())))'))
PY
```

If the captured count looks small (<10 rows on a 14d+ campaign
that should have 15+), scroll the inner table container
(`js("window.scrollBy(0, 600)")`) and re-run — the DOM may still be
virtualizing on a slow render.

**On the TARGETS tab, scroll+eval is fine** — the targeting table is
small (auto campaigns have 2–4 groups; manual ones 10–30 keywords) and
its rows sum to the campaign's own Ad Spend exactly, which is how you
know you got them all. Verified live: 5 spending rows summing to 120.00
against a campaign Ad Spend of 120.00, and 2 auto groups summing to
300.00 against 300.00. **Include PAUSED targets that still have spend in
the window** — an agent that filtered to Live only reported 115.00 and
lost a paused 5.00 row, which then broke its reconciliation.

**But do NOT carry that habit onto Customer Queries** — that tab is
capped at top-15 with no pagination, so scroll+eval structurally cannot
complete it. Use its `Export` button; see § 6. (An earlier revision of
this skill declared Export Data broadly "unreliable in this environment"
after one Targets-tab attempt that produced no file within 10 s. On
Customer Queries it works and is the only complete source — verified
twice, ~15–25 s. Wait ~20 s and diff the downloads directory rather than
concluding failure at 10 s.)

**Tab-activation gotcha.** After clicking the Targets tab (via the
`js()` by-text pattern above), verify by URL — read
`js("return location.href")` and confirm it includes **`tab=target`**
(singular; the current build also appends `&target_filter=all`). The
`aria-selected` state can lag for a second after click and isn't a
reliable activation signal.

**Recommended-Bid cell suffix.** The Recommended Bid column
extracts as e.g. `0.75 0.60-0.90 Apply` — the literal "Apply"
button label is concatenated into the cell innerText. Strip
the trailing `Apply` before formatting in the report.

Columns: Target (keyword text), Bid, eCPC, Recommended Bid,
Verticals, Engagement (Views/Clicks/Orders/CTR/CvR), Status.

**Match types** observed: `exact Match`, `phrase Match` (and likely
`broad Match`).

Per-keyword actions:
- **Bid input**: edit target bid directly
- **Apply** button: applies recommended bid
- **Status toggle**: enable/disable the keyword

## 5. Change Target / Keyword Price

On the Targets tab, the Bid column is directly editable:
```bash
browser-use <<'PY'
print(page_info())                     # confirm the bid input for the target row
fill_input("input.bid-input", "2.50")  # adjust selector to the live bid field
# Confirmation/save happens per-row
PY
```

> **Shadow-DOM bid inputs concatenate — clear and verify first.**
> See `references/ads-tuning.md § Applying changes` for the native-
> setter clear + read-back protocol; a naive `fill_input` on the
> Ant Design shadow input can turn `1.30` into `11.3`.

Or click "Apply" next to Recommended Bid to use noon's suggestion.

## 6. Customer Queries Tab

> ### ⚠️ USE **Export**, NOT the table. The tab shows only the top 15.
>
> The Customer Queries tab renders a **fixed top-15** and has **no
> paginator, no load-more and no rows-per-page control** — verified live:
> the row count stays at 15 across repeated inner-container scrolling of
> every scrollable element on the page. Scroll+eval therefore CANNOT get
> the full query set here, no matter how patiently you scroll.
>
> **The `Export` button on this tab does work** (verified twice, file
> landed in ~15–25 s) and returns the complete set. An earlier revision
> of this skill said "Export Data is unreliable in this environment";
> that was wrong, and following it is what produced years of truncated
> captures. Measured on two live campaigns:
>
> | campaign | targeting spend | via 15-row tab | via Export |
> |---|---|---|---|
> | Auto | 300.00 | 80.00 (0.265, 15 rows) | **300.00 (1.000, ~10k rows)** |
> | Manual | 120.00 | 95.00 (0.786, 15 rows) | **120.00 (1.000, 404 rows)** |
>
> So noon does **not** "attribute only part of spend to queries" — that
> belief was an artifact of reading the tab. With the export the two
> layers agree EXACTLY, and the server now holds noon to the same
> reconciliation floor as Amazon (85%). A low ratio means your capture is
> incomplete, not that noon is being noon.
>
> **How to use it:**
> 1. Open the campaign detail, click the **Customer Queries** tab.
> 2. Click **`Export`** (a plain button; match on its exact text).
> 3. Wait for `~/.vibe-seller/downloads/<slug>/` to gain
>    **`_OVERVIEW_ALL_Report_<start>_<end>.xlsx`**. Snapshot the directory
>    listing BEFORE clicking so you can diff, rather than guessing.
> 4. **Rename it immediately, per campaign** — the filename carries only
>    the date range, so the next campaign's export OVERWRITES it.
> 5. Read it with openpyxl. It is scoped to the campaign you were on and
>    contains BOTH layers, so read them from this ONE file and the
>    reconciliation holds by construction:
>    - **`(Product) Queries`** — the full query set. Columns include
>      `Campaign Name`, `Sku`, `Query`, `Views`, `Clicks`, `Orders`,
>      `Spends`, `Revenue`, `ROAS`.
>    - **`(Product) Target`** — the targeting layer (`Target Value`,
>      `Targeting Type`, `Bid`, `Spends`, …).
>    - also `(Product) Campaign`, `(Product) Sku`, `(Product) Placement`.
>
> The scroll+eval walk below is still the right tool for the **Targets**
> tab (§ 4), and it remains a fallback for a quick eyeball of the top
> queries — just never as the source for the 搜索词对账 line.

```bash
browser-use <<'PY'
# After clicking the Customer Queries tab, verify activation by URL
print(js('return location.href.includes("tab=customerQuery")'))
# Then walk the table rows
print(js('return JSON.stringify(Array.from(document.querySelectorAll("table tr")).map(r => Array.from(r.cells).map(c => c.innerText.trim())))'))
PY
```

**Rendering delay.** After clicking the Customer Queries tab, the
first `eval` may return only an empty header row (no data). This is
a rendering delay — the table is in the DOM but data hasn't
populated. A second eval ~2 seconds later returns full data.

The on-screen state shows only the top spenders; the long-tail
/ 0-order queries where harvest, brand-negate, and waste
decisions live are below the fold. The eval above pulls the
full table on most campaigns; if you see fewer than ~15 rows on
a 14d+ campaign, scroll the inner table container and re-eval.

Shows the actual search terms customers used that triggered your ads.
Columns: Customer Query Term, Target, Match Type, Target Bid, eCPC,
Spends, Verticals, Engagement.

**Auto campaign query routing per-product.** On Auto campaigns,
the Customer Queries tab shows queries scoped to the product
currently selected/highlighted in the Products tab — NOT the full
campaign. If a campaign has 2+ products, switch the highlighted
product to see each product's queries. Always check Customer Queries
while each product is individually selected to capture all routes.

**Auto campaigns: Customer Queries IS the tuning surface.**
Auto campaigns have no Targets tab, so the Customer Queries tab
is where most of the actionable items live (brand-negates,
wrong-category waste, harvest candidates). Allocate equal time
on Customer Queries for Auto as you would on Targets for
Manual — don't treat Auto sections as "lighter" just because
the spec template doesn't show a Targets table.

> **In an audit report, that means an Auto campaign's targeting
> table is one row per Customer-Query-derived target — not one row
> restating the campaign total.** A table whose only row is
> 合计 / 总计 / 汇总 / 整体活动 / 定位层汇总 / overall / total is
> rejected as `[定向层]`：出价、暂停、加投都是逐个定向做的决策，
> 汇总行里没有可执行的对象。合计 may only be a trailing footer row.
> 该活动确实没有数据时写「无数据」。

> **These totals feed the `搜索词对账` line — TWO checks, and they are
> not symmetric.** Query spend can only ever be a PART of the campaign's
> targeting / top-tile spend (每个查询的花费已经计在定向层里了).
> **Below 40% of it** → `[对账]`, an incomplete capture — noon's floor is
> deliberately low (`noon_reconcile_floor`; this page genuinely attributes
> only part of campaign spend, measured median 0.779 across 13 live
> campaigns), so under it means you really did miss rows: re-read both
> layers on the SAME 30-day window. **Above `1.02×` it** →
> `[对账·不可能]`, a contradiction rather than imprecision (实测同窗口下
> 这个比值上限就是 1.00) — usually the two layers came from different
> campaigns. **That direction does NOT fail open on a stall**: either
> re-take both layers for the same `C_…` id, or write in that campaign's
> block ONE line carrying BOTH halves — data unreliable AND do not act on
> it: `⚠️ 数据不可信：本活动两层对账矛盾，请勿执行本活动的出价建议`
> (「数据有偏差，仅供参考」 by itself does not count), or the
> server prepends a warning banner to the delivered report naming that
> campaign. Full rule: `../amazon-ads/references/output-spec.md`.

Use this to discover high-performing queries (add as keywords) or
low-performing queries (add as negatives).

## 7. Export Data

**Two distinct exports — opposite reliability. Do not conflate them.**

| | `Export all campaigns` (list level) | `Export Data` (per tab) |
|---|---|---|
| Where | Campaigns tab, top-right of the list (§ 2) | Products / Targets / Customer Queries tabs |
| Reliability | **Works** — but ASYNC, takes ~1–5 min | **Unreliable here** — often a silent no-op |
| On no file | keep waiting (§ 7.1) | give up immediately, use DOM eval |

### 7.1 `Export all campaigns` — the per-SKU ad-spend source

This is the only practical way to get **ad spend per SKU**. It honours
the list's **date-range filter**, so set the range first.

**Async, and the button is your progress indicator:**

1. Click it once. It flips to **`disabled`** while noon builds the file.
2. The file lands in `~/.vibe-seller/downloads/<slug>/` as
   **`_OVERVIEW_ALL_Report_{from}_{to}.xlsx`** (e.g.
   `_OVERVIEW_ALL_Report_2026-06-01_2026-06-30.xlsx`), typically after
   1–5 min for a few dozen campaigns.
3. **`disabled: true` means "generating", not "broken".** Poll the
   download dir; do NOT re-click — and do NOT apply § 4's
   "don't retry the export" rule here, that one is about the *per-tab*
   button.

> ⚠️ **The filename carries the date range but NOT the country.** An SA
> and an AE export for the same range produce the **same filename**.
> Rename on arrival (`ads_overview_{CC}_{YYYY-MM}.xlsx`) before starting
> the other country's export.

Country comes from the `en-{cc}` URL segment, same as everywhere else.

**Setting a custom month range** (the presets are Last 30 days / Last 7
days / Yesterday / Today):

```bash
browser-use <<'PY'
import time, json
def rect(expr):
    r = js("(function(){%s})()" % expr)
    return json.loads(r) if r and r.startswith('{') else None
def click_text(t, lo=0, hi=99999):
    r = rect("""
      var want=%s, lo=%d, hi=%d;
      var el=Array.from(document.querySelectorAll('div,li,span,button,a,p')).filter(e=>
        e.children.length===0 && (e.textContent||'').trim()===want
        && e.getBoundingClientRect().height>4
        && e.getBoundingClientRect().y>lo && e.getBoundingClientRect().y<hi);
      if(!el.length) return 'nf';
      var b=el[0].getBoundingClientRect();
      return JSON.stringify({x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)});
    """ % (json.dumps(t), lo, hi))
    if not r: return False
    click_at_xy(r['x'], r['y']); time.sleep(2); return True

# 1. open the range dropdown (the button showing the current preset), 2. Custom range
r = rect("""var b=Array.from(document.querySelectorAll('button')).find(x=>
             /Last 30 days|Last 7 days|Custom|20\\d\\d/i.test(x.textContent||'')
             && x.getBoundingClientRect().y<260);
           if(!b) return 'nf'; var q=b.getBoundingClientRect();
           return JSON.stringify({x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2)});""")
click_at_xy(r['x'], r['y']); time.sleep(2)
click_text("Custom range")

# 3. page the calendar back with the  <  arrow (y 350-395, x 580-620) until the
#    header reads the month you want, then click the first day then the last day,
#    then Apply. Verify the range button text before exporting.
click_text("Apply")
print(js("""(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>
  x.getBoundingClientRect().y<260 && /20\\d\\d|Last|Custom/i.test(x.textContent||''));
  return b?b.textContent.trim():'?'})()"""))
PY
```

Then click the export (the handler is on the **`<button>`**, not the
label `<span>` inside it — clicking the span does nothing):

```bash
browser-use <<'PY'
import time, json
r = js("""(function(){
  var d=document.querySelector('[class*=CampaignStatusTabs_headerExportAction]');
  if(!d) return 'nf';
  var b=d.querySelector('button')||d, q=b.getBoundingClientRect();
  return JSON.stringify({x:Math.round(q.x+q.width/2), y:Math.round(q.y+q.height/2), dis:String(b.disabled)});
})()""")
c=json.loads(r); print("export btn:", c)
click_at_xy(c['x'], c['y'])          # ONCE. then poll the download dir.
PY
```

### 7.2 What's inside the workbook

Ten sheets — `(Product)` and `(Brand)` families:

| Sheet | Grain | Columns |
|---|---|---|
| `(Product|Brand) Campaign` | campaign | Campaign Name, Views, Clicks, Orders, ATC, Spends, Revenue, CTR, ROAS, CPC, CPS, CVR |
| **`(Product|Brand) Sku`** | **campaign x SKU** | as above **+ `Sku`** |
| `(Product|Brand) Target` | keyword / target | + Target Value, Targeting Type, Bid, Strategy |
| `(Product|Brand) Placement` | placement | + Placement Type |
| `(Product|Brand) Queries` | search term | + Sku, Query |

**Per-SKU ad spend** = `Spends` from `(Product) Sku` **+** `(Brand) Sku`,
grouped by `Sku`. Verified live: the SKU sheets sum **exactly** to the
Campaign sheets, so this is a complete decomposition — no residual.

```python
frames = [xl.parse(s)[['Sku','Spends','Orders','Clicks','Views','Revenue']]
          for s in ['(Product) Sku', '(Brand) Sku']]
per_sku = pd.concat(frames).groupby('Sku', as_index=False).sum()
```

Three things to handle:

- **`header` is not a SKU.** Brand-ad banner spend is booked against a
  literal `Sku` value of `header` (a few % of spend). It is real spend
  attributable to no SKU — keep it as an explicit "unattributed"
  bucket; don't silently drop it or let it pollute a SKU.
- **Parent vs variant SKUs.** `(Product) Sku` mixes noon-internal
  variant (`Z…Z-<n>`) and parent (`Z…Z`) forms; `(Brand) Sku` is
  mostly variant. These are noon-internal keys, **not** the seller
  codes in the Transaction View's `Partner SKUs` — bridge via that
  export's `SKUs` column (see
  `noon-fbn/references/fee-reports.md` § 5).
- **`Queries` sheets are capped at 30,000 rows.** Exactly 30000 means
  truncated, not complete. Narrow the range if you need full
  search-term coverage.

### 7.3 Reconciling ad spend against the statement

Ad spend does **not** tie exactly to the Transaction View's
`Advertising Fee` (`statement_fee` rows, `Non-Order Fees`):

```
sum(Spends over the calendar month) x (1 + VAT)  ~=  sum(statement_fee Advertising Fee)
```

within a couple of percent (observed ~2–3%). The gap is structural, not
an error: **ad statements are issued on a weekly cycle** whose periods
straddle month boundaries, while the export is filtered on *performance*
date. For per-SKU attribution use the **export** (so the per-SKU parts
sum to the reported total); use `statement_fee` only when you need the
amount actually invoiced.

### 7.4 Per-tab `Export Data`

Exports the current filtered view on Products / Targets / Customer
Queries. **Unreliable in this environment** — see § 4 caveat. Prefer DOM
eval extraction. ⚠️ **If the file doesn't land within ~10 s, do NOT
re-click or retry** — a no-op export button is an environment quirk, not
a transient miss. Switch to DOM eval extraction (§ 4 / § 5) immediately;
retrying just burns steps.

```bash
browser-use <<'PY'
# click the "Export data" button (by text) — downloads CSV (may not land)
js("Array.from(document.querySelectorAll('button')).find(b=>/export data/i.test(b.textContent))?.click()")
PY
```

Campaign detail also has Export Data for the Products tab.

## 8. Create Campaign Flow

**URL**: `admanager.noon.partners/en-{cc}/campaign/start?mpCode=noon&project=PRJ{project_id}`

### Step 1/3 — Ad Type

Two radio options:
- **Product Ads** — Increase product visibility by targeting
  relevant search terms and browsing categories
- **Brand Ads** — Boost brand discovery with ads that showcase
  your logo, brand name and products

Click Continue.

### Step 2/3 — Product Selection + Bidding + Targeting

**1. Product Selection**: Manual Selection OR Bulk Upload
- Search by SKU name input
- Selected products shown in right panel

**2. Bidding Strategy** (choose one):

| Strategy | Behavior |
|----------|----------|
| **Dynamic Bid Up & Down** (New) | Scale up for top placements, down during low conversion. Auto Targeting only. |
| **Dynamic Bid Down Only** | Only lowers bid when conversion is low. Auto + Manual Targeting. |
| **Fixed** | Set default bid amount; no dynamic adjustment. |

**3. Targeting**:
- **Auto Targeting**: noon automatically matches ads with relevant
  parameters. Configure **Default Bid Amount** and **Minimum Bid**.
- **Manual Targeting** (with supported strategies): pick keywords.

**4. Negative Targeting (Optional)**: Exclude specific keywords to
prevent your ad from appearing in irrelevant searches. Limits:
**30 Days negative targets** and **30 Phrase negative targets**.

**5. Top Of Search Placement Bidding (Optional)**: Increase chances
of appearing at top of search results. Bid Percentage boost up to
**900%** to compete for premium placements.

**6. General Settings**:
- Campaign Name (required)
- Marketplace (auto: NOON)
- Start Date / End Date (checkbox "No end date")
- **Budget Details**:
  - Shared Budget — distribute across multiple campaigns
  - Campaign Budget — dedicated to this campaign
- **Maximum Daily Budget** input

Action buttons at bottom: **Cancel & Go Back**, **Save As Draft**,
**Launch Campaign**.

## 9. Edit an Existing Campaign — Add / Remove Keywords & Negatives

**There is no in-place "Add target" / "Add negative" button on the
Targets tab** (verified live 2026-07-21) — the tab only *reads* and
inline-edits bids (§ 5). To change the keyword or negative SET of a
live campaign you re-enter the campaign editor:

1. On the campaign-detail header (top-right, beside the **pause** and
   **duplicate** icons) click the round **blue pencil = Edit** button.
   It opens the same builder as § 8 in edit mode:
   `…/campaign/v2?project=PRJ{project_id}&mpCode=noon&campaignCode={campaign_id}&mode=edit`
2. Scroll to the numbered sections:
   - **§ 5 Targeting → Manual Targeting Settings** — add positive
     keywords / category / product targets, or remove existing rows.
   - **§ 6 Negative Targeting (Optional)** — add or remove negative
     keywords (limits: 30 day + 30 phrase negatives, § 8).
3. **Save** to apply. Deleting a keyword/negative is the same flow:
   open the editor, remove the row, Save. (The Targets-tab Status
   toggle only *pauses* a keyword; it does not remove it.)

> **State-changing — confirm first.** Adding/removing keywords or
> negatives re-saves a live campaign (per the "surface, don't
> auto-execute" rail below). Present the proposed change (current vs
> proposed vs reason) and get user confirmation before you Save; do a
> round-trip (add → verify → remove → verify) only on an explicitly
> designated test/paused campaign.

## 10. Vantage Analytics

**URL**: `https://vantage.noon.partners/en/?project=PRJ{project_id}`

First visit asks to select the marketplace country and account.
Provides deeper analytics across campaigns.

## 11. Reference catalog — "what to do" thinking

The mechanics in §1–§10 above are click paths. The actual
*thinking* — when to create a campaign, when to tune an existing
one, how to research keywords — lives in three reference files:

| Reference | Load when |
|---|---|
| [`../amazon-ads/references/output-spec.md`](../amazon-ads/references/output-spec.md) | **The report contract for every audit** (shared across noon + Amazon — same shape for both platforms). 进度 line, per-campaign drill blocks (Targets table + Customer-Queries table + `搜索词对账` reconciliation line, same date window), bid rules, TSV naming. Before finishing you MUST pass BOTH the **coverage floor** (deterministic, at `set_task_result`) AND the **`ads-report-review` reviewer loop** (active verification — spawn the reviewer per `../amazon-ads/references/reviewer-loop.md`; it opens the live console/export and cross-checks your report, looping until `Status: ok`; Stop-hook enforced). A report is done only when verified against the live console, drilled to the word level. **Submit the FILE — `vibe_seller_set_task_result("./AD_AUDIT_<date>.md")`, the path, never a chat summary of the report**: the reviewer grades whatever string you pass it, and a summary has no `##` combo sections. |
| [`../amazon-ads/references/audit-quickref.md`](../amazon-ads/references/audit-quickref.md) | **The audit procedure, one page** (shared). Enumerate ALL pages → two-layer drill per campaign (Targets + Customer Queries, same window, reconcile) → build the report with Read+Edit via INSERT markers → converge with the server reviewer. |
| [`../amazon-ads/references/format-anchor.md`](../amazon-ads/references/format-anchor.md) | _Legacy detail._ Exact per-campaign table layouts; load only if you need the precise column shape. Superseded as a contract by `output-spec.md`. |
| [`references/ads-creation.md`](references/ads-creation.md) | Creating a new campaign. Covers targeting choice, bidding strategy, per-keyword bid heuristic, match-type strategy, negative scoping, TOS boost rules, budget choice, the Save-as-Draft → Launch UI quirk, naming convention, post-launch verification cadence. |
| [`references/ads-tuning.md`](references/ads-tuning.md) | **Any task that reads existing campaigns and proposes changes** — phrasings like "review all ads", "audit the campaigns", "give me an improvement plan", "weekly ad review", "tune ads", "fix ACOS / ROAS". Defines the steps and noon-specific click paths (Customer Queries tab, Targets tab, etc.); the **output contract** lives in `output-spec.md` (shared with Amazon — same shape for both platforms). |
| [`references/ads-keyword-research.md`](references/ads-keyword-research.md) | Building the initial keyword list for a Manual campaign. Covers buyer-vs-seller language, storefront autocomplete (English + Arabic), peer-listing reading, cross-checking against existing campaigns to avoid self-competition, parallel negative-list build, match-type assignment. |

Safety rails:

- **Compare same-country with same-country.** Buyers in different
  countries behave differently; one country's peer data isn't a fair
  baseline for another country's campaign.
- **Surface, don't auto-execute.** Recommendations are presented
  to the user with current value, proposed value, and reason. The
  user confirms before any state-changing click.
- **Per-run captures → `/tmp/<run-slug>/`.** Live data captures
  go to a temp dir, never under `~/.vibe-seller/knowledge/`.

## Tips

- **Ad Manager is per country** (e.g. `/en-<cc1>/` vs `/en-<cc2>/`).
- **Campaign Detail tabs**: Products / Placements / Targets / Customer Queries.
- **ROAS** = Revenue / Spends, target > 1.0 minimum (but real
  scale-target depends on margin — see `ads-tuning.md`).
- **Export Data** buttons exist on Products, Targets, Customer Queries tabs.
- **Negative keyword limits**: 30 Day negatives + 30 Phrase negatives per campaign.
- **Top-of-search bid boost**: up to 900%.
- **Session timeout recovery**: during long audits (7+ campaigns,
two countries), a `page_info()` call may time out. Recovery: pipe a
fresh `new_tab("<any_admanager_url>")` + `wait_for_load()` to
reconnect (the daemon lifecycle is managed by the wrapper). Login
state is preserved.
- **Export Data on Customer Queries WORKS and is REQUIRED** (§ 6) —
  the tab shows only top-15. The note below applies to the other tabs:
- **Export Data may be slow elsewhere**: clicking Export Data on Targets or
Customer Queries tab may not produce a CSV in
`~/.vibe-seller/downloads/<slug>/`. Use DOM eval extraction
instead (§ 4 pattern).

### Don't trust an "empty" Ad Manager that contradicts the store profile

noon's Ad Manager has been observed returning a **transient empty
state** that the UI faithfully renders as
*"No data available / Showing 0 items per page"*, even when the
store has 5+ active campaigns in that country (verified by
re-navigating the same URL ~30 minutes later — same
"Last Updated" timestamp, very different result). This isn't a
client-render race — the page literally says zero campaigns —
but the page is wrong.

**The store profile is the durable ground truth.**
`stores/<slug>/metadata.json` carries
`platform_countries.noon` and `notes.md` documents prior-run
campaigns. When the live Ad Manager for a country listed there
shows zero campaigns, that's a contradiction — treat it as a
transient UI/backend issue, not a fact about the store.

When that contradiction fires, in order:

1. **Re-navigate the same URL** (a fresh `new_tab("<url>")` +
   `wait_for_load()`, not just refresh — open a fresh navigation).
   If campaigns show up: trust them, audit, move on.
2. **Check the on-page filters.** noon's overview has Status and
   Ad Type dropdowns — clear them and re-read.
3. **Open the Campaigns tab directly** — `…/home?…&tab=campaigns`
   (there is no separate `/campaigns` path) — and scroll the list
   container to force a fresh lazy-load, rather than trusting the
   Overview view.
4. Only after all three return zero with a fresh "Last Updated"
   timestamp may you report the country as actually empty —
   and even then, surface the contradiction with the store
   profile so the user can resolve it.

Dropping a country that the store profile lists as active is a
much worse failure than spending 60 extra seconds verifying.

## See also

- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — promote a SKU you've just listed
