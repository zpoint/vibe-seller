---
name: noon-ads
description: "Noon Ad Manager — campaigns (Auto / Manual / Brand), tuning audits, keyword research, negatives, Vantage analytics. Covers Targets / Customer Queries / Export Data flows. Load for any noon ads work — review, audit, tune, create. References under references/ for the tuning playbook (ads-tuning.md), applying approved changes (applying-changes.md), exports (exports.md), the campaign editor (campaign-editor.md), creation guide (ads-creation.md), and keyword research (ads-keyword-research.md)."
requires: [noon-shared]
gates: [ad_changes, ad_coverage]
---

# Noon — Ad Manager

Load the `noon-shared` skill first (Skill tool): login, project id,
page structure and the shared UI patterns all live there.

## 0. Declare what the task is for

Before any browser action, call `vibe_seller_declare_ad_task` (already
in your tool list as `mcp__vibe-seller__vibe_seller_declare_ad_task`;
ToolSearch only lists deferred tools, so it will not show it). The
declaration decides what the run owes, so read it off what the person
asked for, not off what you expect to find:

| They asked for | `kind` | `scope` |
|---|---|---|
| "审计一下广告" / "review our ads" | `audit` | omit `combos` — whole store |
| "复核 widget-006 在 noon AE 的广告" | `audit` | that platform+country, those campaign ids |
| "把这三个词的出价降下来" | `audit` | those campaigns |
| "帮 widget-006 建关键词广告" | `create` | the market you are creating in |
| "执行刚才确认的调整" | `execute` | the campaigns being changed |
| "我们 AE 的 ROAS 大概多少" | `investigate` | the market asked about |

- **Figures or changes?** A question whose answer is a number (spend,
  ROAS, how last month went) is `investigate`, however many markets it
  spans: read the figures, answer with `vibe_seller_set_task_result`,
  done — no drill, no TSVs, and no proposals. A request about what to
  change, or "is this right?", is `audit`. There is no `edit` kind: a
  request to change specific bids is an `audit` scoped to those campaigns,
  so the user still approves the change before it is applied.
- **The scope trap: omitting `combos` means every marketplace the store
  sells on.** Omit it only for a genuinely store-wide request.
- **A product, not a campaign?** Declare the market first, enumerate,
  find the campaigns carrying that SKU, then declare again with the same
  kind and markets plus `campaigns: [...]`. Inside a turn a declaration
  may only narrow, or move `investigate` → `audit` at the same or a
  narrower scope (it adds the coverage you owe). Anything wider needs a
  new message from the user; if you think the scope is wrong, say so in
  the result.
- **A new user message that changes the work is a new phase** — declare
  again (`create` → "now review them" is `audit`; "apply 2a, 3b" is
  `execute`). Don't drop a recommendation the person asked for just to
  fit a declaration.

## What an audit produces

The server checks an ad task from fixed-header TSVs, not from the
report's prose. The files, their columns, where they go and the store
rules on `CHANGES.tsv` are defined once in
[`references/data-contract.md`](references/data-contract.md); the
procedure is
[`../amazon-ads/references/audit-quickref.md`](../amazon-ads/references/audit-quickref.md)
and the human report is
[`../amazon-ads/references/output-spec.md`](../amazon-ads/references/output-spec.md).
Both are shared with Amazon. What is different on noon:

- **Countries.** `./AUDIT_TARGETS.json` (written by the server) lists
  every marketplace the store is configured for; you owe the ones you
  declared. A declared country with no campaigns still gets its
  `CAMPAIGNS.tsv` row with a `note`.
- **`CAMPAIGNS.tsv` comes from the scrolled list** (§ 2), with the
  `All N` chip as the count to reach. `state` is the console's status
  word in lower case (`live`, `paused`, `out of budget`). Drill `Live`
  and `Out of budget` campaigns — both are spending.
- **`ad_group` is the SKU.** noon has no ad groups; its export groups by
  `SKU`. Don't invent one.
- **`source_target` is `—`.** noon's `Queries` sheet carries only
  campaign, SKU and query, so a query cannot be attributed to a target.
- **Both layers come from one per-campaign Export** (§ 6), so the
  search-term spend reconciles with the targeting spend by construction
  (`noon_reconcile_floor` in the data contract). A low ratio means the
  capture is incomplete — usually the 15-row tab was read instead of the
  Export.
- **ROAS is noon's headline metric** (no ACOS column on the console);
  compute `acos = spend / sales × 100` for the TSVs.

Tuning judgement for noon (drill priority, SOI, head vs modified terms,
category targets) is in [`references/ads-tuning.md`](references/ads-tuning.md).

## 1. Ad Manager layout

**URL**: `https://admanager.noon.partners/en-{cc}/home?mpCode=noon&project=PRJ{project_id}`

The Ad Manager is per country: the `/en-{cc}/` segment selects it. One
advertiser account serves every country, so the `advertiserCode` the
page adds to the URL stays the same when you switch; it is not the
country switch. Add `&tab=campaigns` for the campaign list (without it
the page is the Overview). If a page shows the other country's
campaigns, reopen this URL with the country you want.

The left nav is an icon rail — **Campaigns**, **Budget**, **Billing**,
**Vantage**, **Access Management**, **Settings** — whose labels render as
`role=menuitem` spans, so match them by text rather than by
`ul[role=menu] a`.

`/home` opens an **Overview** dashboard (KPI panels, promos, a **Sale
Event Optimizer** widget and a **Recommendations** panel), not the
campaign list. The Ad Manager card has three tabs — **Overview |
Performance | Campaigns** — and the list lives under
`…/home?…&tab=campaigns`.

Overview KPI cards:

| Metric | Meaning |
|---|---|
| ROAS | Revenue / Spends |
| Revenue | Ad-attributed revenue |
| Spends | Ad spend |
| eCPC | Effective cost per click |
| CTR | Clicks / Views |
| Orders, Clicks, Views, ATC | Ad-attributed orders, clicks, impressions, add-to-cart |
| CvR | Conversion rate (campaign detail only) |

A time-series chart below toggles any of these.

## 2. Campaign list

Filters above the list on the **Campaigns** tab:

- **Search** (campaign name), **Date range** (default `Last 30 days`)
- **Ad Type**: `All types` / `Product` / `Brand` / `Display`
- **Targeting**: `All targeting` / `Auto` / `Manual`
- **Status chips**: `Live N` · `Paused N` · `All N`, plus `More status
  filter`. The numbers are the true totals for the current filter.
- **Export campaigns** (top-right) — the list-level workbook
  ([`references/exports.md`](references/exports.md)).

Columns scroll horizontally: Campaign, Status, Budget, Revenue, ROAS,
Ad Spend, eCPC, Orders, Views, Clicks, ATC, …, Actions.

### Enumerate every campaign

The list is a lazy-loaded table inside its own scroll container: about
15–20 rows render on first paint and `window.scroll` does nothing. A
read without scrolling the container can miss more than half of the
campaigns, and nothing on the page tells you so — only the chip count
does. Enumerate live each time; a leftover `Campaign_*.csv` or
`Export campaigns` file in the downloads folder holds whatever rows were
loaded when it was written.

Per country:

1. **Read the chip totals.** Rely on the number and on the
   `/campaign/details/` links, both language-neutral, rather than on the
   chip label:
   ```bash
   browser-use <<'PY'
   print(js("return JSON.stringify([...document.querySelectorAll('*')].filter(e=>e.children.length<=2 && /^(Live|Paused|All)\\s*\\d+$/i.test(e.textContent.replace(/\\s+/g,' ').trim())).map(e=>e.textContent.replace(/\\s+/g,' ').trim()))"))
   PY
   ```
2. **Scroll the list's container to the bottom** until the link count
   stops growing. Its class starts with `CampaignListRevamp_` (the hashed
   suffix changes per build); the snippet scrolls every inner
   `overflow-y:auto` scroller taller than its viewport:
   ```bash
   browser-use <<'PY'
   for _ in range(12):
       js("[...document.querySelectorAll('*')].filter(e=>{var s=getComputedStyle(e);return (s.overflowY==='auto'||s.overflowY==='scroll') && e.scrollHeight>e.clientHeight+50;}).forEach(e=>e.scrollTop=e.scrollHeight)")
       wait(1)
   print("links:", js('return document.querySelectorAll("a[href*=\\"/campaign/details/\\"]").length'))
   PY
   ```
3. **Extract ids** (§ 3) and de-duplicate. The list is complete when the
   distinct count equals the chip for the status filter you used (`All N`
   for `CAMPAIGNS.tsv`). If it is short, keep scrolling — never adjust
   the number down to match.
4. Re-run the loop after every country switch.

### An "empty" Ad Manager that contradicts the store profile

The Ad Manager sometimes renders a transient *"No data available /
Showing 0 items per page"* for a country that has live campaigns.
`stores/<slug>/metadata.json` (`platform_countries.noon`) and
`stores/<slug>/notes.md` are the durable record. When a listed country
shows zero campaigns:

1. Open a fresh navigation to the same URL (`new_tab(url)` +
   `wait_for_load()`, not a refresh).
2. Clear the Status and Ad Type filters.
3. Open `…/home?…&tab=campaigns` directly and scroll the container.
4. Only if all three still show zero with a fresh "Last Updated" stamp,
   record the country as empty — with the contradiction in the
   `CAMPAIGNS.tsv` `note` and in the result so the user can resolve it.

Dropping a country that has live campaigns is a far worse outcome than
the minute this check costs.

## 3. Campaign detail page

**URL**: `admanager.noon.partners/en-{cc}/campaign/details/{campaign_id}?mpCode=noon&project=PRJ{project_id}`

Campaign ids look like `C_` plus 10 alphanumerics (`C_DEMO0001`). They
live in `<a href>` attributes, not visible text:

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

This returns only the rows currently rendered — run it after the § 2
scroll.

**Navigate by id, not name.** Names in a report often differ from the
live name; the `C_` id does not. Names can also mislead about the
product: check the **Products** tab for what a campaign really
advertises.

Page header: campaign name (with its own pencil), ad-type tag (`Product
Ad` / `Brand Ad`), status badge, *Last updated*. Below it a **Campaign
Settings** strip with **Pause** (or Resume) and **Duplicate**, then the
setting cards:

`DURATION` · `PRODUCTS` · `TARGETING & BIDDING` · `TOP OF SEARCH BID`
(Top of Search N% / Product Detail Page N%) · `NEGATIVE TARGETING` ·
`DAILY BUDGET`

Each card's title button or pencil opens that setting's modal. The full
editor is at `…/campaign/v2?…&campaignCode={id}&mode=edit`
([`references/campaign-editor.md`](references/campaign-editor.md)). The
**Campaign Performance** card has its own date range and **Export**.

Sub-tabs: **Products** (per-SKU metrics) · **Placements** · **Targets**
(keywords, match types, bids) · **Customer Queries** (search terms) ·
**Day on Day Trends**.

The sub-tabs have no stable selector. Scroll the tab bar into view
(~800 px on a standard page, ~1500 px on brand-video pages, where the
player pushes it down) and click by label:

```bash
browser-use <<'PY'
js("window.scrollBy(0, 800)")
js("Array.from(document.querySelectorAll('[role=tab],a,button')).find(e=>/^Products$/i.test(e.textContent.trim()))?.click()")
PY
```

Things to check on this page:

- **Brand Ads have their own norms.** Brand-video CTR is measured
  differently; compare Brand Ads only with other Brand Ads or with their
  own history.
- **A Brand Ad "Creative" row** collects clicks with no attributable
  orders. If it carries more than ~20% of the campaign's spend, flag it
  and show ROAS with and without it.
- **"No SKUs found" on a Live, spending campaign** means the linked SKUs
  were deleted or delisted: the campaign spends without attributable
  revenue. Also check for variants showing "View Issues" instead of
  "Buy Box Won" — one broken variant can sink a campaign's ROAS.

## 4. Targets tab

Verify activation by URL — it contains `tab=target` (plus
`&target_filter=all`; `target_filter=negative` shows negatives).
`aria-selected` lags after the click.

The targeting table is small (auto: 2–4 group rows such as `auto
keyword` / `auto category`; manual: 10–30 targets) and renders all rows
on first paint, so a DOM read is complete:

```bash
browser-use <<'PY'
print(js('return JSON.stringify(Array.from(document.querySelectorAll("table tr")).map(r => Array.from(r.cells).map(c => c.innerText.trim())))'))
PY
```

If the count looks short, scroll the table and read again. Its rows sum
to the campaign's Ad Spend — that is how you know you have them all.
Keep paused targets that spent in the window: their spend is still in
the campaign total.

Columns, read positionally (the header extracts with two blank spacer
cells):

```
Targets | Status | Bid | (blank) | Revenue | ROAS | Spends | eCPC |
Orders | Views | Clicks | ATC | CTR | CVR | SOI | (blank) | Actions
```

The **Target** cell carries the match type inline — `<keyword> Keyword
Exact Match`, `<keyword> Keyword Phrase Match`, `<path> Category Match`
— and truncates long keywords (`usb c fast charg...`). Split off the
match type; for full keyword text read the editor's selected-target
list ([`references/campaign-editor.md`](references/campaign-editor.md)).

### SOI — how much of the available reach you win

`SOI` (Share of Impressions) is the fraction of available impressions
for that target your ad won. It renders as a percent, so convert first:

```
available pool ≈ views ÷ (SOI_percent / 100)
# views 400, SOI 2.0%  →  400 / 0.02 = 20,000
```

`searches/month` sizes the market; SOI says how much of it you win, net
of bid, relevance and competition. Sum views and pools across a campaign
for a capture rate, and read it next to daily-budget utilisation (spend
÷ budget × days): capture low + budget unspent → bid or relevance
problem; capture high + budget unspent → the keywords are small. To tell
bid from relevance, see
[`references/ads-tuning.md`](references/ads-tuning.md) § Head terms vs
modified terms.

## 5. Changing a bid

The bid is not editable in the row. The row's **Edit Bid** button opens:

- on a manual campaign's keyword/category row, **`BidUpdateModal`** — one
  number input whose placeholder is the current bid, and an **Update**
  button that stays disabled until the value changes;
- on an auto campaign's `auto keyword` / `auto category` row, the
  campaign's **Targeting & BIDDING** modal — the **Bid Amount** box
  (`placeholder="Enter Bid Amount"`), strategy radios, Cancel / Update.

Typing over the old value can concatenate (`1.30` → `11.3`), so clear
with the native setter, read back, set, read back, then Update and
re-read the row. The full protocol, pauses and negatives are in
[`references/applying-changes.md`](references/applying-changes.md).
There is no "apply suggested bid" shortcut; noon's suggested range is
visible in the editor and is a hint, not a target.

## 6. Customer Queries tab — use its Export

The tab renders a fixed top-15 with no paginator or load-more, so a DOM
read cannot be complete however long you scroll. Its **`Export`** button
works (the file lands in ~15–25 s) and returns the full set. On two
measured campaigns the 15-row tab showed 0.27 and 0.79 of the targeting
spend; the Export matched it exactly.

1. Open the campaign, click **Customer Queries** (URL gains
   `tab=customerQuery`).
2. List the downloads folder, then click the button whose text is exactly
   `Export`.
3. Wait for `campaigns-export-<ts>-<id>.xlsx` to appear in
   `~/.vibe-seller/downloads/<slug>/`; diff the listing rather than
   guessing. Give it ~60 s before concluding anything.
4. Rename it per campaign right away — the list-level export shares the
   filename pattern, and the next export overwrites it.
5. Read it with openpyxl. Row 1 of each data sheet is a colour banner,
   row 2 the header. It is scoped to this campaign and holds both layers:
   - `Queries` — `Campaign ID`, `Campaign Name`, `SKU`, `Query`, `Views`,
     `Clicks`, `Orders`, `ATC`, `Spends`, `Revenue`, `CTR`, `ROAS`;
   - `Targets` — Target Value, Target Type, Bid, Spends, …;
   - also `Campaigns`, `SKUs`, `Negative Keywords`, `Placement`,
     `Read me` (row 2 states the date range).

On screen the tab is scoped to the product highlighted in the Products
tab; the Export covers the campaign, and its `SKU` column lets you check
every advertised SKU is present. For Auto campaigns this is where most
actionable items live (brand negates, wrong-category waste, harvest
candidates) — give it the time you'd give a Manual campaign's Targets.

A DOM read of the tab is still fine for a quick look at the top queries.
The first read after the click may return only a header row; read again
~2 s later.

## 7. Exports

| | `Export campaigns` (list) | `Export` on Customer Queries | `Export Data` on Products / Targets |
|---|---|---|---|
| Use for | per-SKU spend, month ranges | the drill (both layers) | rarely — often a silent no-op |
| Timing | async, 1–5 min; the button stays `disabled` while it builds | ~15–25 s | if no file in ~10 s, read the DOM instead |

Calendar driving, workbook layout, per-SKU spend and reconciling against
the statement: [`references/exports.md`](references/exports.md).

## 8. Creating and editing campaigns

- Create form (`/campaign/start`), keyword picker, category search,
  negatives boxes, Launch, post-launch check, and editing an existing
  campaign's keywords/negatives:
  [`references/campaign-editor.md`](references/campaign-editor.md).
- What to choose (targeting, strategy, bids, match types, TOS boost,
  budget, naming): [`references/ads-creation.md`](references/ads-creation.md).
- Building the keyword list:
  [`references/ads-keyword-research.md`](references/ads-keyword-research.md).

## 9. Vantage analytics

**URL**: `https://vantage.noon.partners/en/?project=PRJ{project_id}`

Deeper cross-campaign analytics. The first visit asks for the
marketplace country and account. Vantage is scoped by the sidebar store
switcher, not the URL — read the flag first (`noon-shared` § 2).

## Reference catalog

| Reference | Load when |
|---|---|
| [`references/data-contract.md`](references/data-contract.md) | Any audit or execution: the TSVs the server reads and the store rules. |
| [`../amazon-ads/references/audit-quickref.md`](../amazon-ads/references/audit-quickref.md) | Running an audit — the shared procedure. |
| [`../amazon-ads/references/output-spec.md`](../amazon-ads/references/output-spec.md) | Writing the report and filling the TSV columns. |
| [`../amazon-ads/references/tuning-recommendation-format.md`](../amazon-ads/references/tuning-recommendation-format.md) | Deciding a row: PROTECT, bid floor and step cap, missing margin, search-term actions. |
| [`../amazon-ads/references/execution.md`](../amazon-ads/references/execution.md) | Applying approved changes — the contract. |
| [`references/ads-tuning.md`](references/ads-tuning.md) | noon-specific audit judgement: what to read, drill priority, SOI, head terms, category targets, report shape. |
| [`references/applying-changes.md`](references/applying-changes.md) | noon click paths for bid edits, pauses and negatives during execution. |
| [`references/exports.md`](references/exports.md) | Any noon export, per-SKU spend, statement reconciliation. |
| [`references/campaign-editor.md`](references/campaign-editor.md) | Create form and edit-mode mechanics. |
| [`references/ads-creation.md`](references/ads-creation.md) | Choosing settings for a new campaign. |
| [`references/ads-keyword-research.md`](references/ads-keyword-research.md) | Building and sizing a keyword list. |

## Safety rails

- **Never apply an ad change the user has not approved.** Audit
  proposals go to `CHANGES.tsv`; state-changing clicks happen only in an
  `execute` phase for rows the user picked. A read-only pass through the
  editor never clicks Save.
- **Compare a country only with itself.** Buyers differ between
  countries; one country's figures are not a baseline for another's.
- **Per-run captures go to a subfolder of the task dir (your CWD)**,
  never under `~/.vibe-seller/knowledge/`. The per-campaign TSVs go where the data
  contract says.

## Tips

- **Thresholds** (ROAS-equivalent of `acos_no_lower`, bid floor, scale
  ROAS, cooldown, margin) are defaults in the data contract; a store
  overrides them with `key: value` lines in `stores/<slug>/notes.md`.
  When the user states a different standard, write it there with
  `vibe_seller_write_workspace_file`.
- **Negative keyword limits**: 100 Exact + 100 Phrase per campaign — read
  the `N/100 … Selected` counter rather than trusting a number here.
- **Top-of-search boost**: 0–900%.
- **Session timeout during long audits**: if `page_info()` times out,
  open a fresh `new_tab("<admanager url>")` + `wait_for_load()`; the
  login survives.

## See also

- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — promote a SKU you've just listed
