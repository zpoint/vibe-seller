---
name: noon-ads
description: "Noon Ad Manager — campaigns (Auto / Manual / Brand), tuning audits, keyword research, negatives, Vantage analytics. Covers Targets / Customer Queries / Export Data flows. Load for any noon ads work — review, audit, tune, create. References under references/ for the tuning playbook (ads-tuning.md), creation guide (ads-creation.md), and keyword research (ads-keyword-research.md)."
requires: [noon-shared]
gates: [ad_completeness_review, ad_negation_allowlist, ad_execution_fidelity]
---

# Noon — Ad Manager

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, and common patterns.

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

Phase 1 (Discover) MUST, per country:

1. **Read the true total** from the status chips — the `Live N` / `All N`
   numbers are your completeness target:
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
Strategy, Running From date, Last Updated.

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

**Export Data → CSV is unreliable in this environment.** Field-
verified: clicking the Export Data button on a campaign-detail
Targets tab produced no CSV in `~/.vibe-seller/downloads/<slug>/`
within 10 s; the download monitor only catches bulk-sheet
exports, not per-campaign tab exports. Use Export Data only as
a last resort for campaigns with ≥ 50 keywords AND only after
verifying a fresh file appears in the downloads dir.

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

Same scroll+eval default as § 4 (Export Data is unreliable in
this environment — see § 4 note).

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

Use this to discover high-performing queries (add as keywords) or
low-performing queries (add as negatives).

## 7. Export Data

Both Targets tab and Customer Queries tab have **Export Data** button
at top-right. Triggers CSV download of the current filtered view.
**Unreliable in this environment** — see § 4 caveat. Prefer DOM eval
extraction. ⚠️ **If the file doesn't land within ~10 s, do NOT re-click
or retry** — a no-op export button is an environment quirk, not a
transient miss. Switch to DOM eval extraction (§ 4 / § 5) immediately;
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

## 9. Add Negative Keywords to Existing Campaign

Open Campaign Detail → Targets tab. The Targets tab manages positive
keywords. For negatives, look for a "Negative Targets" section or
sub-tab on the same page (noon UI varies; explore the tab headers).

When creating a new campaign, use step 4 "Negative Targeting" above.

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
| [`../amazon-ads/references/output-spec.md`](../amazon-ads/references/output-spec.md) | **The report contract for every audit** (shared across noon + Amazon — same shape for both platforms). 进度 line, per-campaign drill blocks (Targets table + Customer-Queries table + `搜索词对账` reconciliation line, same date window), bid rules, TSV naming. Before finishing you MUST pass BOTH the **coverage floor** (deterministic, at `set_task_result`) AND the **`ads-report-review` reviewer loop** (active verification — spawn the reviewer per `../amazon-ads/references/reviewer-loop.md`; it opens the live console/export and cross-checks your report, looping until `Status: ok`; Stop-hook enforced). A report is done only when verified against the live console, drilled to the word level. |
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
- **Export Data is unreliable**: clicking Export Data on Targets or
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
