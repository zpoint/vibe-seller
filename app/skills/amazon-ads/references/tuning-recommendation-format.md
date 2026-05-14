# Recommendation format — per-campaign, review-without-the-page

The reader (the user, their manager, anyone) is reviewing without the
page in front of them. The output is organized **by campaign**, with
each campaign's data and all its problems and recommendations in one
self-contained section. The reader reads Campaign 1 top-to-bottom
before moving to Campaign 2 — no jumping.

## Output structure (in order)

The report is intentionally compact. Per campaign: data tables
with a per-row `recommendation` column (data + decision in one
place). No per-Problem prose, no separate Recommendations summary
— each row carries its own action. The user reads top-to-bottom
and replies with action numbers from the global checklist.

1. **Today's session — overview**
   - Header table: one row per campaign **(every campaign in the
     manifest, active or inactive)**, with major issues found.
   - Defaults applied (margin, goal, target ACOS, target ROAS).
   - Recommended sequence for the session.
2. **Anomaly highlights** — top ~5 cross-campaign bleeders, each
   citing the Action checklist ID it maps to.
3. **One section per campaign** — present in the order: active
   first (largest spend first), then inactive grouped at the
   bottom. The section heading carries both the friendly name and
   the **Amazon campaign ID** as a stable handle for follow-ups
   (`# Campaign 1 (id: A059...): <name>`):
   - For **active** campaigns: `## Campaign data` — required
     tables for the campaign type (keyword + search-terms +
     placements; campaign-level row only when a strategy / budget
     action is proposed). Each table ends with a
     **`recommendation`** column carrying the per-row action
     (Pause / Trim to X / Scale / Hold / Negate / Harvest).
   - For **inactive** campaigns: a single line under the section
     heading citing the mechanical state and the data that proves
     it (e.g. `inactive — paused (Status field: Paused; last
     impression 2026-04-12)`). No data tables, no recommendations.
4. **What to watch next** — ≤ 3 lines, cross-campaign.
5. **Action checklist** — global cross-campaign continuous
   numbering, nested by campaign with sub-letters. The user
   replies with action numbers (`2a, 2c, 3 all`) or campaign IDs
   (`A059... all`) to opt in / out of execution.

## The header table

Required columns:

| Column | Notes |
|---|---|
| `#` | **manifest row number** — assigned 1..N in display order across all manifest entries (active and inactive). Used for human reference in the report. The Action checklist's top-level numbering is **separate**: it numbers only campaigns that have at least one action, so the checklist's `1.` may be the header table's `#2` if `#1` is inactive. Always refer to a campaign by its `id` (Amazon campaign ID), not by `#`, when the reference must be stable across re-runs |
| `Campaign` | friendly name + Amazon campaign ID in parens — `<name> (A059...)`. The ID is the stable handle for follow-ups |
| `Country` | marketplace TLD (`SA`, `AE`, `US`, ...) — required for multi-country audits |
| `Type` | `SP-Auto` / `SP-Manual-KW` / `SP-Manual-Product` / `SB` / `SBV` / `SD` |
| `30d spend` | total cost in marketplace currency. For inactive campaigns: show the actual captured value (often 0; may be > 0 for `inactive — paused` if the campaign spent before being paused mid-window) |
| `orders` | Purchases column. Same population convention as spend |
| `sales` | Sales column. Same population convention as spend |
| `ACOS` | bold if > 2× target. `—` when sales = 0 (undefined) |
| `ROAS` | `—` when sales = 0 (undefined) |
| `Daily budget` | with currency |
| `Strategy` | bidding strategy + plain-English diagnostic when relevant. E.g. `Rule-based ROAS≥2.00 (actual 1.50, target unmet)` not just `Rule-based ROAS≥1.80` |
| `State` | mechanical state from the manifest (`active` / `inactive — paused` / `inactive — archived` / `inactive — ended` / `inactive — no impressions` / `inactive — too new` / `error: <message>`). See `tuning-workflow.md` § Mechanical state taxonomy. **There is no `skipped` state.** |
| `Major issues found` | comma-separated, brief — e.g. `(a) "X" Phrase keyword at 99% ACOS; (b) Rest-of-search 0 orders / SAR 149 wasted; (c) 7 search terms with high ROAS not yet exact`. For inactive campaigns: `—` |

**Population convention for inactive rows**: show the real captured top-tile values (which may be 0 or > 0 — e.g. `inactive — paused` campaigns can have non-zero spend if they ran for part of the window before being paused). Use `—` only for derived metrics (`ACOS`, `ROAS`) when sales = 0 makes them undefined, and for `Major issues found` (since no drill was performed).

Below the table, list:
- **Defaults applied** — margin (with placeholder flag if applicable),
  goal, derived target ACOS / ROAS, waste/harvest thresholds.
- **Recommended sequence** — short ordered list of what to do first
  across the session, with reasoning. Often "Campaign X actions
  1a-1b first → wait 7-14d → Campaign Y action 2a → re-run". Don't
  recommend changing everything simultaneously when the changes
  interact.

## Per-campaign section

### `## Campaign data`

For each campaign, show the relevant data tables. Each table's
last column is `recommendation` carrying the per-row action; data
and decision live side-by-side, so the reader sees the campaign's
shape and what to change in one pass.

**Required tables by type** — every campaign whose manifest state
is `active` MUST include the type-specific target table. A
search-terms table is also required *unless the type targets
ASINs directly* (see SP-Manual-Product ASIN row), in which case
the ASIN target list itself is the actionable surface and there's
no separate query layer to negate or harvest from. A
`## Campaign data` section missing the required tables for its
type is a defect; see `tuning-workflow.md § Phase 2: Drill`. The
loop terminates by exhausting the manifest's active set, not by
agent judgment about when there's "enough data".

**The data tables come from drilling THIS campaign's pages.** A
section that says `(未深度 drill)` / `(待 drill 验证)` /
`基于 top-tile + 同 SKU pattern` / `沿用 #N 模板` and then offers
recommendations without populated data tables is a defect — the
*same* defect as a section that's missing entirely. Recommendations
extrapolated from a different campaign in the same SKU family are
not recommendations; they reference entity names this agent never
read and bid values it never captured. See `tuning-workflow.md §
Drill is per-campaign and non-transferable` and § Anti-patterns.

A report-level preamble of the form *"本审计深度抓取了 #X 数据;
其余 campaign 基于 ... 给出建议"* is a self-declaration that the
report is incomplete; the remedy is to drill the missing campaigns
before publishing, not to keep the preamble.

Inactive campaigns (state ∈ {paused, archived, no impressions,
too new, error}) do **not** get a `## Campaign data` block. They
appear as a single line under the section heading citing the
state and the data that proves it.

| Type | Required target table | Search-terms table |
|---|---|---|
| SP-Auto | 4 auto-target-groups (Close match / Substitutes / Loose match / Complements) — bid, suggested range, clicks, spend, orders, sales, ROAS, ACOS per group | **Required** — top-spend customer queries with matched-via group |
| SP-Manual-Keyword | All keyword rows (Broad / Phrase / Exact) — bid, suggested range, clicks, spend, orders, sales, ROAS, ACOS | **Required** — top-spend customer queries with matched-via keyword |
| SP-Manual-Product (Category) | All category-target rows — bid, suggested range, clicks, spend, orders, sales, ROAS, ACOS | **Required** — top-spend customer queries (matched-via category) |
| SP-Manual-Product (ASIN) | All ASIN-target rows — bid, suggested range, clicks, spend, orders, sales, ROAS, ACOS | **Skip** — ASIN targeting bypasses queries; the ASIN target list IS the actionable surface |
| SB / SBV | All keyword rows — keyword bid, suggested range, viewable impressions, clicks, CTR, spend, CPC, orders, sales, ROAS, NTB% | **Required** — top-spend customer queries; for SBV note creative metrics (view rate, through-play) |

**Order of tables — targeting first, placement second.** The keyword
(or product-target) table is what the campaign actually targets and
where the tunable levers live. The placement breakdown is a
diagnostic *aspect* of the same total. Lead with keywords; show
placements as the second table for context.

**Keyword table** (lead with this — required for keyword-targeted
SP campaigns). The last column is **`recommendation`** — the
per-row action so the reader sees data + decision in one place.

```
| Keyword (match)    | status     | clicks | spend       | orders | sales       | ROAS | ACOS       | bid      | recommendation                                      |
|---|---|---|---|---|---|---|---|---|---|
| "<kw-1>" (Phrase)  | Delivering | 119    | <ccy 250.00>| 6      | <ccy 260.00>| 1.00 | **100.0%** | <ccy 2>  | **Pause** — 100% ACOS, 119 clicks / 6 orders        |
| "<kw-2>" (Broad)   | Delivering |  82    | <ccy 180.00>| 9      | <ccy 380.00>| 2.00 |  50.0%     | <ccy 2>  | Trim to <ccy 1.50> — 50% ACOS, but 9 orders, scale-able |
| "<kw-3>" (Broad)   | **Paused** |  15    | <ccy  30.00>| 1      | <ccy  30.00>| 1.00 | 100.0%     | <ccy 2>  | Hold (Paused) — already paused, no action            |
| "<kw-4>" (Broad)   | Delivering |   3    | <ccy   7.00>| 1      | <ccy  30.00>| 4.30 |  25.0%     | <ccy 3>  | **Scale** — 4.30 ROAS / 25% ACOS; raise bid +20%     |
| "<kw-5>" (Phrase)  | Delivering |   4    | <ccy   8.00>| 1      | <ccy  40.00>| 5.00 |  20.0%     | <ccy 3>  | Hold — 4 clicks is small sample; watch 7d            |
| (≤ 9 idle variants, 0–3 clicks, 0 orders)                                |        |        | <small>     |        | —           | —    | —          | <ccy 3>  | **Pause** all idle — 80%+ kw with 0 clicks (clutter) |
```

(`<ccy ...>` is the marketplace currency — SAR, AED, USD, etc.)

**`recommendation` cell content rules:**

- **Action verb first**, bolded: `**Pause**`, `**Scale**`, `**Trim
  to <ccy x.xx>**`, or unbolded `Hold` / `Hold (Paused)` for
  no-change. The verb is what the user opts in/out of.
- **Reason after** (one short clause), citing numbers from the same
  row: *"100% ACOS, 119 clicks / 6 orders"*. Keep it brief — full
  prose lives in the Action checklist's per-action entry.
- **Amazon's recommended bid range** appended in parens when
  available and relevant to the action: *"Trim to <ccy 1.50>
  (Amazon rec 1.00–2.00)"*. Often the suggested-bid column is
  blank (Amazon hasn't computed a value yet for low-traffic kws);
  in that case omit it from the cell.
- **PROTECT keywords** (≥ 5% of campaign orders): `Hold (PROTECT)
  — N orders, ROAS X.XX`.
- **No vague phrases** like *"review later"* or *"check"* — every
  cell has a concrete action or `Hold`.

Sum the keyword columns; they should equal the campaign top-tile to
the cent (clicks, spend, orders, sales). If they don't, the date
range on the Targeting tab isn't aligned — fix that before drawing
conclusions.

**Markdown table formatting — pipe count must match.** The
separator row `|---|---|...|` must have exactly the same number of
`|---` cells as the header row's column count. Verified-in-the-wild
failure mode: header has 6 columns but separator has 7 `---|`
pipes, which makes markdown render the entire table as inline pipe-
text on one line (NOT as a table). Count the columns in the header
and write exactly that many `---|` cells in the separator. If
unsure, count by hand; the renderer is unforgiving.

**Search terms table** (always — top spenders, with per-row
recommendation. This is where most negate / harvest decisions live):

```
| Search term      | matched via       | clicks | spend       | orders | sales       | ROAS | recommendation                                          |
|---|---|---|---|---|---|---|---|
| <high-roas-term> | "<kw-2>" Broad    | 14     | <ccy 30.00> | 3      | <ccy 160.00>| 5.00 | **Harvest** to Exact + back-negate from Broad parent    |
| <wasteful-term>  | "<kw-1>" Phrase   | 19     | <ccy 40.00> | 0      | —           | —    | **Negate Exact** — 0 orders, spend > 1.5× AOV           |
| <competitor-pdp> | (Product pages)   |  8     | <ccy 20.00> | 0      | —           | —    | **Negate ASIN** in campaign Negative-products tab       |
| <converter-term> | "<kw-2>" Phrase   |  3     | <ccy  6.00> | 1      | <ccy  35.00>| 5.80 | Hold — already converting at this match level           |
```

**Placements table** (diagnostic context, with per-row recommendation):

```
| Placement                  | impressions | clicks | CTR  | spend       | orders | sales       | ACOS   | recommendation                                  |
|---|---|---|---|---|---|---|---|---|
| Top of search (first page) | 2,000       | 114    | 5.0% | <ccy 250.00>| 11     | <ccy 500.00>|  50.0% | Hold — best converter; modifier 0%, no change   |
| Rest of search             | 9,000       |  70    | 1.0% | <ccy 150.00>|  5     | <ccy 180.00>|  80.0% | Refine via search-term negates (preserves 5 orders) |
| Product pages              |30,000       |  45    | 0.2% | <ccy 100.00>|  2     | <ccy  75.00>| 120.0% | Add 5–10 negative-ASINs — 0.2% CTR, wrong-fit PDPs |
| Total                      |41,000       | 229    | 0.6% | <ccy 500.00>| 18     | <ccy 750.00>|  65.0% | —                                                |
```

The Total row must sum to the campaign top-tile values to the cent.
If it doesn't, the date range on this page is not aligned with the
campaign-level reading — go fix that before continuing. See
`mechanics.md` § 8e.

**Campaign-level row** (only when a strategy / budget action is
proposed; otherwise omit this block):

```
| Field            | Current                 | recommendation                                          |
|---|---|---|
| Bidding strategy | Rule-based ROAS ≥ 1.80  | Switch to Dynamic-down — actual ROAS 1.50 chasing unhit target |
| Daily budget     | <ccy 15>                | Hold — spending <ccy 15.0>/day, on quota                |
```

## Global Action checklist (end of report)

The very last section of the report is a cross-campaign global
checklist that the user uses to opt in / out of execution. Top-level
numbering is **continuous across all campaigns** (Campaign 1 actions
are `1a, 1b, …`; Campaign 2 actions are `2a, 2b, …`; multi-country
audits keep the numbering continuous across countries — see below).

**Heading level**: emit as `# Action checklist` (top-level `#`,
matching the other report-level sections like `# Campaign N` and
`# What to watch next`). Multi-country audits use `### <country
code>` subdivisions inside the checklist; see worked example.

```
# Action checklist

1. **Campaign 1 (<campaign-name-1>)** — keyword + placement waste
   1a. Negate 7 wasteful search terms triggered by "<kw-A>" Phrase (113 of 119 clicks didn't convert)
   1b. Harvest 7 high-ROAS search terms to Exact + back-negate from Phrase parent
   1c. Add 5–10 negative ASINs for Product-pages waste (start with the worst-CTR competitor PDPs)
   1d. Trim "<kw-B>" Broad bid SAR 3.00 → SAR 2.50

2. **Campaign 2 (<campaign-name-2>)** — bid drift on top spender
   2a. Trim "<kw-C>" Phrase bid <ccy 4.00> → <ccy 2.50> (1.5× suggested midpoint, ROAS 1.00)
   2b. Pause "<kw-D>" — 1 order on <ccy 50.00> spend at ROAS 0.50

3. **Campaign 3 (<campaign-name-3>)** — auto discovery campaign
   3a. Trim default bid SAR 4.00 → SAR 1.80 (current bid 2.0× Amazon's suggested upper bound)
   3b. Pause "Loose match" auto-target-group — 0 orders on SAR 20.00 spend
```

Rules (mirror `noon-ads/references/ads-tuning.md § Action
checklist`):

- **Top-level numbers are campaigns**; one per campaign that has at
  least one action. Sub-letters (`1a, 1b, …`) are the specific
  actions the user can opt in/out of.
- **Every leaf names the specific entity** (keyword, search term,
  auto-target-group, placement, ASIN, budget value) it operates on.
  Generic items are a defect.
- **No "monitor / re-evaluate / wait" items** — those go in *What
  to watch next*, never here.
- **LISTING-FIRST diagnoses** appear as "pause" / "exclude" actions
  the user can decline; they're still on the checklist.
- **Multi-country audits**: continuous numbering across countries.
  If country A has campaigns 1–4, country B starts at 5. Group
  visually under `### <country-A code>` / `### <country-B code>`
  headings inside the checklist.
- **Reply protocol**: `2a, 2c, 3 all` (opt in by sub-action or full
  campaign) or `1, 2 all, 3 skip` (prose). Per-item parameter
  overrides (`2a trim to 0.95 instead of 0.85`) apply before
  execution.

## Plain-English requirements

- Replace `(failing)` with: "Amazon is told to bid for ROAS 1.80 but
  actual ROAS is 1.50 — chasing a target it isn't hitting"
- Replace `bid drift` with: "current bid is 1.5× Amazon's suggested
  midpoint of SAR 0.90"
- Replace `field range: positive currency, marketplace min ~SAR 0.50
  to SAR 1000` with: omit unless the range is surprising. For
  placement modifier — keep "(0% to +900%, increase only — Amazon
  doesn't allow negative modifiers)" because that's the
  non-obvious constraint.
- Replace `LISTING-FIRST tag` in body with: "**Not actionable from
  the ad side** — this is a listing image/title problem"

## What to omit from the Action checklist

These rules apply to **the global Action checklist only** — they do
NOT permit omitting campaigns from the report. Every campaign in
the manifest appears in the header table and as a section, full
or one-line; see `tuning-workflow.md § Phase 3: Compose`.

- **No "Hold" / "no-change" leaf items in the Action checklist.**
  Hold rows still appear in the per-campaign data tables (so the
  reader sees the full picture); but the global checklist contains
  only items the user can opt in / out of executing. A campaign
  whose data section is all `Hold` rows simply doesn't get a
  top-level entry in the checklist (no `5a`, `5b`, … under it).
- **No "Field range" lines** unless the range is surprising
  (placement modifier 0%-+900% qualifies; keyword bid being positive
  currency does not).
- **No vague audit items** ("review the keywords"). Do the review
  during the run, surface specific findings.
- **No bidding-strategy change as opening problem** for a campaign
  whose ACOS is fixable surgically. Reach for search-term level
  first (Lever 1 + Lever 4 — negative kw / phrase / ASIN, plus
  Lever 8 harvest), then per-keyword (Lever 2 pause, Lever 3 trim
  bid, Lever 5 raise bid), then placement modifier (Lever 6), then
  strategy change (Lever 7). The Tier order in `tuning-toolbox.md`
  is the sequence.

## Worked example (the full output skeleton)

> **Illustrative numbers below.** This is an output skeleton, not a
> capture from any specific store. Real reports will use the actual
> numbers from the campaigns. Names use placeholders (`<campaign-A>`,
> `<kw-A>`) — the agent fills these in from the live data when
> producing a real report.

```
# Today's session — overview

Manifest count: 3 campaigns (2 active, 1 inactive). Reconciles to
the 3 rows shown on the campaign-list page for the chosen window.

| # | Campaign | Country | Type | 30d spend | orders | sales | ACOS | ROAS | Daily budget | Strategy | State | Major issues found |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | <campaign-name-1> (A011AAA…) | SA | SP-Manual-KW | SAR 500.00 | 18 | SAR 750.00 | 65.0% | 1.50 | SAR 15 | Rule-based ROAS≥2.00 (actual 1.50, target unmet) | active | (a) "<kw-A>" Phrase at ACOS 100.0%; (b) Product-pages 120.0% ACOS / only 2 orders; (c) Rest-of-search  80.0% ACOS / 5 orders — refine, don't kill; (d) 7 search terms with high ROAS not yet exact |
| 2 | <campaign-name-2> (A022BBB…) | SA | SP-Manual-KW | SAR 800.00 | 17 | SAR 1,500.00 | 50.0% | 1.90 | SAR 15 | Rule-based ROAS≥1.50 (matching) | active | (a) "<kw-B>" bid 1.5× over Amazon's suggested midpoint; (b) Product-pages CTR 0.3% (listing issue) |
| 3 | <campaign-name-3> (A033CCC…) | SA | SP-Auto | SAR 0 | 0 | SAR 0 | — | — | SAR 15 | Dynamic — down only | inactive — paused | — |

**Defaults applied**: margin 40% (placeholder — confirm), goal scale → target ACOS 28%, target ROAS 3.57. Waste threshold: search-term cost ≥ 1.5 × AOV with 0 orders. Harvest threshold: ≥ 1 order AND ROAS ≥ 5.0.

**Anomaly highlights** (top bleeders, mapped to checklist IDs):
1. "<kw-A>" Phrase at 100.0% ACOS, 119 clicks → see **1a / 1b**
2. Product-pages 120.0% ACOS, 0.2% CTR → see **1c**
3. "<kw-B>" bid 1.5× suggested midpoint → see **2a**

**Recommended sequence**: Campaign 1 actions 1a + 1b first → wait 7-14d → Campaign 2 action 2a → re-run for the rest.

---

# Campaign 1 (id: A011AAA…): <campaign-name-1>

## Campaign data

**Keywords** (targeting-first):

| Keyword (match) | clicks | spend | orders | sales | ROAS | ACOS | bid | recommendation |
|---|---|---|---|---|---|---|---|---|
| "<kw-A>" (Phrase) | 119 | <ccy 250.00> | 6 | <ccy 260.00> | 1.00 | **100.0%** | <ccy 2.00> | **Pause** — 100% ACOS, bid below Amazon rec midpoint already |
| "<kw-B>" (Broad)  | 82  | <ccy 180.00> | 9 | <ccy 380.00> | 2.00 |  50.0%     | <ccy 2.00> | Trim to <ccy 1.50> — 50% ACOS, but 9 orders, scale-able |

**Search terms** (top spend):

| Search term | matched via | clicks | spend | orders | sales | ROAS | recommendation |
|---|---|---|---|---|---|---|---|
| <converter-term-1>    | "<kw-B>" Broad   | 14 | <ccy 30.00> | 3 | <ccy 160.00> | 5.00 | **Harvest** to Exact + back-negate from Broad parent |
| <wasteful-term-1>     | "<kw-A>" Phrase  | 19 | <ccy 40.00> | 0 | —            | —    | **Negate Exact** — 0 orders, spend > 1.5× AOV |
| <B0XXXXXXXX-1> (ASIN) | (Product pages)  |  8 | <ccy 20.00> | 0 | —            | —    | **Negate ASIN** in campaign Negative-products tab |

**Placements** (diagnostic context):

| Placement                  | impressions | clicks | CTR  | spend       | orders | sales       | ACOS   | recommendation |
|---|---|---|---|---|---|---|---|---|
| Top of search (first page) | 2,000       | 114    | 5.0% | <ccy 250.00>| 11     | <ccy 500.00>|  50.0% | Hold — best converter; modifier 0%, no change |
| Rest of search             | 9,000       |  70    | 1.0% | <ccy 150.00>|  5     | <ccy 180.00>|  80.0% | Refine via search-term negates (preserves 5 orders) |
| Product pages              |30,000       |  45    | 0.2% | <ccy 100.00>|  2     | <ccy  75.00>| 120.0% | Add 5–10 negative-ASINs — 0.2% CTR, wrong-fit PDPs |
| Total                      |41,000       | 229    | 0.6% | <ccy 500.00>| 18     | <ccy 750.00>|  65.0% | — |

(reconciles to campaign top-tile to the cent)

---

# Campaign 2 (id: A022BBB…): <campaign-name-2>

(same structure: `## Campaign data` with per-row `recommendation`
columns; one section per entity table.)

---

# Campaign 3 (id: A033CCC…): <campaign-name-3>

inactive — paused. Status=Paused; window clicks=0; spend=SAR 0.
No drill performed.

---

# What to watch next (re-check 7-14 days)

1. <Campaign 1 / specific keyword or placement> — should drift from <X> toward <Y> after actions <#>
2. <Campaign 1 / harvested keywords> — each should accumulate ≥ 1 order in 14 days; pause any that don't
3. <Campaign 2 / specific entity> — should change from <X> toward <Y> after action <#>

---

# Action checklist

1. **Campaign 1 (<campaign-name-1>)** — keyword + placement waste
   1a. Negate 7 wasteful search terms triggered by "<kw-A>" Phrase (113 of 119 clicks didn't convert)
   1b. Harvest 7 high-ROAS search terms to Exact + back-negate from Phrase parent
   1c. Add 5–10 negative ASINs for Product-pages waste (start with the worst-CTR competitor PDPs)
   1d. Negate zero-order query tail in Rest-of-search

2. **Campaign 2 (<campaign-name-2>)** — bid drift on top spender
   2a. <action with concrete entity + value>
   2b. <action with concrete entity + value>
```
