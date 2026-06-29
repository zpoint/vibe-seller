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
| `Country` | marketplace TLD (`US`, `UK`, ...) — required for multi-country audits |
| `Type` | `SP-Auto` / `SP-Manual-KW` / `SP-Manual-Product` / `SB` / `SBV` / `SD` |
| `30d spend` | total cost in marketplace currency. For inactive campaigns: show the actual captured value (often 0; may be > 0 for `inactive — paused` if the campaign spent before being paused mid-window) |
| `orders` | Purchases column. Same population convention as spend |
| `sales` | Sales column. Same population convention as spend |
| `ACOS` | bold if > 2× target. `—` when sales = 0 (undefined) |
| `ROAS` | `—` when sales = 0 (undefined) |
| `Daily budget` | with currency |
| `Strategy` | bidding strategy + plain-English diagnostic when relevant. E.g. `Rule-based ROAS≥2.00 (actual 1.50, target unmet)` not just `Rule-based ROAS≥1.80` |
| `State` | mechanical state from the manifest (`active` / `inactive — paused` / `inactive — archived` / `inactive — ended` / `inactive — no impressions` / `inactive — too new` / `error: <message>`). See `tuning-workflow.md` § Mechanical state taxonomy. **There is no `skipped` state.** |
| `Major issues found` | comma-separated, brief — e.g. `(a) "X" Phrase keyword at 99% ACOS; (b) Rest-of-search 0 orders / USD 149 wasted; (c) 7 search terms with high ROAS not yet exact`. For inactive campaigns: `—` |

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

> 🛑 **Hard schema requirement — applies to every keyword table in
> every campaign section, every report, no exceptions.**
>
> The **`actual CPC`** column is REQUIRED on every keyword table
> (and every Search-terms table, and every Targets table on noon).
> Compute as `spend / clicks` (cell shows `—` when `clicks == 0`).
>
> Why it's non-negotiable: the proposed-bid floor (`actual_cpc ×
> 0.7`) is the single guardrail that prevents "bid cliff"
> recommendations from being emitted. A keyword table without this
> column lets the floor go uncomputed, and the cliff pattern
> recurs. **Reports observed in the wild that omitted the column on
> some campaigns (but not others) emitted floor-violating Trim
> proposals on exactly the campaigns where the column was missing.**
> The column existing on table A but not table B is the same defect
> as the column missing everywhere — generalize it, or the gate is
> a coin flip.
>
> If you're about to write a Markdown table that begins
> `| Keyword | ... | bid | ... | recommendation |` and is missing
> `actual CPC` between `bid` and `recommendation` (or between
> `bid` and `suggested`), stop. Add the column. Recompute the
> Trim proposals against the floor before publishing.

```
| Keyword (match)    | status     | clicks | spend       | orders | sales       | ROAS | ACOS       | bid      | actual CPC | suggested | recommendation                                      |
|---|---|---|---|---|---|---|---|---|---|---|---|
| "<kw-1>" (Phrase)  | Delivering | 119    | <ccy 250.00>| 6      | <ccy 260.00>| 1.04 | **96.2%**  | <ccy 2>  | <ccy 2.10> | <ccy 1.50–2.20> | **Pause** — 96% ACOS, 119 clicks / 6 orders         |
| "<kw-2>" (Broad)   | Delivering |  82    | <ccy 180.00>| 9      | <ccy 380.00>| 2.11 |  47.4%     | <ccy 2>  | <ccy 2.20> | <ccy 1.40–2.10> | **Trim to <ccy 1.55>** (−23%; floor <ccy 1.54>; Amazon rec 1.40–2.10) — 47% ACOS, 9 orders |
| "<kw-3>" (Broad)   | **Paused** |  15    | <ccy  30.00>| 1      | <ccy  30.00>| 1.00 | 100.0%     | <ccy 2>  | <ccy 2.00> | <ccy 1.40–2.10> | Hold (Paused) — already paused, no action            |
| "<kw-4>" (Broad)   | Delivering |   3    | <ccy   7.00>| 1      | <ccy  30.00>| 4.29 |  23.3%     | <ccy 3>  | <ccy 2.33> | <ccy 2.00–3.00> | **Scale** — 4.29 ROAS / 23% ACOS; raise bid +20%     |
| "<kw-5>" (Phrase)  | Delivering |   4    | <ccy   8.00>| 1      | <ccy  40.00>| 5.00 |  20.0%     | <ccy 3>  | <ccy 2.00> | <ccy 1.80–2.80> | Hold — 4 clicks is small sample; watch 7d            |
| (≤ 9 idle variants, 0–3 clicks, 0 orders)                                |        |        | <small>     |        | —           | —    | —          | <ccy 3>  | —          | <ccy 2.00–3.00> | **Pause** all idle — 80%+ kw with 0 clicks (clutter) |
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
- **No vague phrases** like *"review later"* or *"check"* — every
  cell has a concrete action or `Hold`.

### Precedence — when multiple signals fire on the same row

Most keyword/target rows match one signal cleanly. When more than
one fires (common on top performers — high spend triggers both
PROTECT and bid-drift, for example), resolve **top-down through
this table** and emit exactly the cell text on the first matching
row. Lower-precedence signals that also fired are surfaced in the
trailing narrative ("bid 4× suggested upper but driving 79 % of
campaign orders"), never as a second action verb.

| Precedence | Signal | Cell text |
|---|---|---|
| 1 | **PROTECT** — either: (a) row ROAS ≥ target_roas × 2 AND ≥ 1 order (Efficiency PROTECT), or (b) row carries ≥ 50 % of campaign orders (Workhorse — pause forbidden, trim cap drops to −15 %). Matches `format-anchor.md § PROTECT — two simple cases` (canonical, reviewer-enforced). | `Hold (PROTECT) — N orders, ROAS X.XX` (append `(efficiency)` or `(workhorse)` to indicate which trigger). No other action verb. |
| 2 | **Bleeder** — 0 orders AND spend ≥ 1.5 × AOV | `**Pause** — N clicks / <ccy spend> / 0 orders` |
| 3 | **Bid drift high** — bid > 1.5 × suggested midpoint | `**Trim to <ccy X.XX>** (−P %; Amazon rec L–U; actual CPC <ccy>) — Z % ACOS, N orders` |
| 4 | **Bid drift low + performing** — bid < 0.5 × suggested midpoint AND actual ROAS ≥ target | `**Raise to <ccy>** — under-bid; ROAS X.XX, N orders` |
| 5 | **Scale** — actual ROAS ≥ 1.5 × target AND budget headroom | `**Scale** — ROAS X.XX vs target Y.YY; raise bid / budget` |
| 6 | (none of the above) | `Hold` |

Combining a higher-precedence verdict with a lower-precedence
action verb in the same cell (e.g. `Hold (PROTECT) ... Trim to X`)
is a contract violation and a user-trust violation — the PROTECT
tag exists to suppress those exact actions on order-drivers.

### Bid-trim cell — required content for Precedence 3

Before writing **any** `Trim to <ccy X.XX>` cell, run this 4-step
checklist on paper / in scratch space. Skipping it is the single
most reliable way to produce the "bid cliff" defect.

```
Step 1. actual_cpc      = spend / clicks   (if clicks == 0, do not propose Trim — emit Hold with reason "no traffic")
Step 2. floor           = actual_cpc * 0.7
Step 3. step_cap_floor  = current_bid * 0.5
Step 4. proposed        = max(floor, step_cap_floor)   # both are floors; the larger one wins
        if row_orders >= 0.5 * campaign_orders:
            proposed = max(proposed, current_bid * 0.85)  # workhorse soft-trim cap
        if proposed >= current_bid:
            # no room to trim — promote to Lever 2 (Pause) or Lever 7 (bidding-strategy change), do NOT emit Trim
            emit Pause OR Hold-with-reason, NOT Trim
```

The three constraints (`Step 2`, `Step 3`, workhorse cap) are
each enforced by `tuning-toolbox.md § Lever 3`. The cell text
MUST surface enough numbers for the user to re-derive the floor
in one glance:

```
**Trim to <ccy 2.55>** (−15% Soft; floor <ccy 2.44> from actual CPC; Amazon rec 0.45–0.75) — workhorse (80% of campaign orders)
```

— format: `verb`, `proposed value`, `(delta %; floor source; Amazon
rec)`, `— short reason`. The `floor` number is mandatory; it
proves the proposal is not below `actual_cpc × 0.7`.

### Anti-patterns — recognize-and-stop list

| Anti-pattern in the proposed value | What's wrong | What to emit instead |
|---|---|---|
| `Trim to <suggested midpoint>` when current bid > 1.33× suggested upper | Single-step −25 %+; would violate the canonical 25 % cap. | `Trim to current × 0.75` (one step), or `Pause` if floor > step_cap_floor. |
| `Trim to <ccy X>` where X < `(spend / clicks) × 0.7` | Below actual-CPC floor — keyword loses the auction on next round. | `Trim to actual_cpc × 0.7` (if still < current) or `Pause` if floor ≥ current. |
| `Trim to <ccy X>` on a row with ≥ 50 % of campaign orders (Workhorse), with X < current × 0.85 | Wipes the workhorse; campaign's order base collapses. | `Trim to current × 0.85` (−15 % workhorse cap per `format-anchor.md`) only; full review after 7d. |
| `Trim to <ccy X>` on a row with 0 clicks | No actual_cpc; the proposed value is unanchored. | `Hold — no traffic to size the trim against`. Reconsider why the keyword exists. |

A proposed bid that prints below `actual_cpc × 0.7` is a **defect**
the report must catch before delivery. So is `−P %` > `25 %` in a
single step (the canonical cap from `format-anchor.md § Bid-change
rule` — reviewer rejects anything over 25 %). So is "Trim straight
to suggested midpoint" when current bid is more than 1.33× the
suggested upper. These defects
produce the "bid cliff" pattern; flag them in self-review and
either re-clamp the value, or promote to Pause if no clamp
satisfies all constraints.

### Self-review gate — per campaign section, not deferred to end

The gate runs **inside the per-campaign drill loop**, not as a
single global pass at the end of the report. Concretely, the
agent's per-campaign workflow is:

```
for each active campaign in the manifest:
  1. drill: capture top-tile, keyword/target rows, search terms
  2. write the keyword table — INCLUDING the actual CPC column
  3. for each row that triggered Precedence 3 (bid drift high):
     - compute floor, step_cap_floor, workhorse cap
     - if no clamp passes all three → promote to Pause / Hold
     - else propose the clamped value
     - print the floor source in the cell text
  4. RE-READ the section you just wrote. For every "Trim to <X>" cell,
     verify on the spot:
        delta_cap_ok   : |X − current| / current ≤ 0.25
        floor_ok       : X ≥ (spend / clicks) × 0.7   (or clicks==0 ⇒ not Trim)
        workhorse_ok   : if orders ≥ 0.5 × campaign_orders ⇒ |X − current| / current ≤ 0.15
     If ANY check fails, FIX THE CELL before moving to the next campaign.
  5. only then move to the next campaign
```

Why per-campaign and not global: in observed runs, the agent
applied the gate to the worst campaign in the report but skipped
it on the second-worst — because by the time the second campaign
was being written, the gate had drifted out of working memory.
Anchoring the gate inside each per-campaign loop body keeps it
adjacent to the recommendation it must check.

A report containing even one Trim cell that fails the per-section
gate is not delivered. If the agent notices the omission after
the section is written, the fix is to `Edit` the section in place
(re-clamping the value or promoting to Pause), not to leave the
defect and add a caveat. Caveats are not a substitute for
correct recommendations.

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
| <high-roas-term> | "<kw-2>" Broad    | 14     | <ccy 30.00> | 3      | <ccy 160.00>| 5.33 | **Harvest** to Exact + back-negate from Broad parent    |
| <wasteful-term>  | "<kw-1>" Phrase   | 19     | <ccy 40.00> | 0      | —           | —    | **Negate Exact** — 0 orders, spend > 1.5× AOV           |
| <competitor-pdp> | (Product pages)   |  8     | <ccy 20.00> | 0      | —           | —    | **Negate ASIN** in campaign Negative-products tab       |
| <converter-term> | "<kw-2>" Phrase   |  3     | <ccy  6.00> | 1      | <ccy  35.00>| 5.83 | Hold — already converting at this match level           |
```

**Placements table** (diagnostic context, with per-row recommendation):

```
| Placement                  | impressions | clicks | CTR  | spend       | orders | sales       | ACOS   | recommendation                                  |
|---|---|---|---|---|---|---|---|---|
| Top of search (first page) | 2,000       | 114    | 5.7% | <ccy 250.00>| 11     | <ccy 500.00>|  50.0% | Hold — best converter; modifier 0%, no change   |
| Rest of search             | 9,000       |  70    | 0.8% | <ccy 150.00>|  5     | <ccy 180.00>|  83.3% | Refine via search-term negates (preserves 5 orders) |
| Product pages              |30,000       |  45    | 0.2% | <ccy 100.00>|  2     | <ccy  75.00>| 133.3% | Add 5–10 negative-ASINs — 0.2% CTR, wrong-fit PDPs |
| Total                      |41,000       | 229    | 0.6% | <ccy 500.00>| 18     | <ccy 755.00>|  66.2% | —                                                |
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
   1d. Trim "<kw-B>" Broad bid USD 3.00 → USD 2.50

2. **Campaign 2 (<campaign-name-2>)** — bid drift on top spender
   2a. Trim "<kw-C>" Phrase bid <ccy 4.00> → <ccy 2.50> (1.5× suggested midpoint, ROAS 1.00)
   2b. Pause "<kw-D>" — 1 order on <ccy 50.00> spend at ROAS 0.50

3. **Campaign 3 (<campaign-name-3>)** — auto discovery campaign
   3a. Trim default bid USD 4.00 → USD 1.80 (current bid 2.0× Amazon's suggested upper bound)
   3b. Pause "Loose match" auto-target-group — 0 orders on USD 20.00 spend
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
  midpoint of USD 0.90"
- Replace `field range: positive currency, marketplace min ~USD 0.50
  to USD 1000` with: omit unless the range is surprising. For
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
| 1 | <campaign-name-1> (A011AAA…) | US | SP-Manual-KW | USD 500.00 | 18 | USD 750.00 | 66.7% | 1.50 | USD 15 | Rule-based ROAS≥2.00 (actual 1.50, target unmet) | active | (a) "<kw-A>" Phrase at ACOS 96.2%; (b) Product-pages 133.3% ACOS / only 2 orders; (c) Rest-of-search  83.3% ACOS / 5 orders — refine, don't kill; (d) 7 search terms with high ROAS not yet exact |
| 2 | <campaign-name-2> (A022BBB…) | US | SP-Manual-KW | USD 800.00 | 17 | USD 1,500.00 | 53.3% | 1.88 | USD 15 | Rule-based ROAS≥1.50 (matching) | active | (a) "<kw-B>" bid 1.5× over Amazon's suggested midpoint; (b) Product-pages CTR 0.3% (listing issue) |
| 3 | <campaign-name-3> (A033CCC…) | US | SP-Auto | USD 0 | 0 | USD 0 | — | — | USD 15 | Dynamic — down only | inactive — paused | — |

**Defaults applied**: margin 40% (placeholder — confirm), goal scale → target ACOS 28%, target ROAS 3.57. Waste threshold: search-term cost ≥ 1.5 × AOV with 0 orders. Harvest threshold: ≥ 1 order AND ROAS ≥ 5.0.

**Anomaly highlights** (top bleeders, mapped to checklist IDs):
1. "<kw-A>" Phrase at 96.2% ACOS, 119 clicks → see **1a / 1b**
2. Product-pages 133.3% ACOS, 0.2% CTR → see **1c**
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
| Total                      |41,000       | 229    | 0.6% | <ccy 500.00>| 18     | <ccy 755.00>|  66.2% | — |

(reconciles to campaign top-tile to the cent)

---

# Campaign 2 (id: A022BBB…): <campaign-name-2>

(same structure: `## Campaign data` with per-row `recommendation`
columns; one section per entity table.)

---

# Campaign 3 (id: A033CCC…): <campaign-name-3>

inactive — paused. Status=Paused; window clicks=0; spend=USD 0.
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
