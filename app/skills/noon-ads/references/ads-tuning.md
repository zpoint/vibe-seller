# Noon Ads — Tuning Audit

How to run an ads-tuning audit on a noon store. The skill defines
the **steps**, the **data to capture**, and the **output format**.
Analytical decisions (which lever, which keyword, which bid, when to
escalate to listing-side) are yours.

Pair with `../SKILL.md § 3–7` for click paths and `§ 9` for
negatives. Catalog read lives in `../../noon-shared/SKILL.md § 3`.
Capture goes to `/tmp/<run-slug>/`, never under
`~/.vibe-seller/knowledge/`.

## What the audit reads

The audit reads two data sources and produces the canonical report
below.

**One country per report block** — en-ae and en-sa are separate
auctions; don't mix in the same header table. When the user asks for
multiple countries (e.g. "review all noon ads"), produce one full
report block per country under top-level headings `## Country 1:
<code>` / `## Country 2: <code>` / … (see *Multi-country audits*
below). Numbering of campaigns restarts within each country.

**Active campaigns only.** The audit covers campaigns that are
currently spending — Status `Live` or `Out of budget`. Skip `Paused`
and `Draft`; they don't need tuning and only add noise. If the user
explicitly asks about a paused campaign ("should we reactivate X?")
that's a separate request — handle it in its own section, not the
main audit.

**Cross-check "empty" against the store profile.** When the
audit covers multiple countries, do NOT report one of them as
"empty" / "no active campaigns" if `stores/<slug>/metadata.json`
lists that country under `platform_countries.noon` (or
`notes.md` documents prior campaigns there). The Ad Manager has
been observed returning a transient empty state that the UI
faithfully renders as *"No data available / Showing 0 items per
page"* even when the store has 5+ active campaigns — see
`../SKILL.md § Don't trust an "empty" Ad Manager that
contradicts the store profile` for the verification protocol
(re-navigate, clear filters, try `/campaigns` instead of
`/home`). Surface a contradiction with the store profile rather
than silently omitting the country. Dropping a country that
actually has active campaigns is a much worse failure than
spending 60 extra seconds verifying.

**Catalog (per ad-SKU health)** — `../../noon-shared/SKILL.md § 3`.
Capture per SKU referenced by any active campaign:

| Field | Where on My Catalog |
|---|---|
| Product title (English + Arabic) | catalog row title |
| Real category (gender, type) | from product title or click into edit page |
| Seller Status | toggle column |
| Live Status (`Offer Created` / `Unavailable` / `Buy Box Won` / `View Issues`) | catalog row |
| Active Net Stock | inventory column |
| Performance (Views, Units Sold, Sales) | catalog row |
| Rating count | by clicking through to the public PDP, only when a LISTING-FIRST diagnosis is on the table |

SKU codes lie — a code may belong to a different category than its
name suggests. Catalog tells you what the ad is actually showing.

**Campaigns (per-campaign metrics)** — Ad Manager
(`../SKILL.md § 2`). Per campaign, capture: ID, name, type (Auto /
Manual), status, daily budget, strategy, 30-day KPIs (Spend,
Revenue, ROAS, CTR, Orders, Clicks, Views).

**MANDATORY drill-in for every active campaign.** For every
campaign with non-zero spend in the window, open the Campaign
Detail page (`../SKILL.md § 3`) and capture:

- **Funnel KPIs** from the top tiles: Views, Clicks, ATC, Orders,
  Spend, Revenue, CTR, CvR.
- **Targets table** (Manual only — Auto has no Targets tab): use
  the scroll+eval capture from `../SKILL.md § 4`. The DOM
  accumulates all rows on initial render for typical-sized
  campaigns; `document.querySelectorAll('table tr')` returns the
  full table. The Export Data → CSV path is unreliable in this
  environment — see `../SKILL.md § 4`.
- **Customer Queries**: same scroll+eval default
  (`../SKILL.md § 6`).

Sums of clicks/spend/orders should reconcile to the campaign
top-tile. If they don't, the date range is misaligned; fix before
drawing conclusions.

**No drill = no recommendation.** A "PROTECT — healthy" verdict
on a campaign you didn't open is not acceptable — *you don't
know* it's healthy until you've seen the keywords and the
queries. The single biggest efficiency leak in a noon portfolio
is a healthy-looking-from-overview campaign that has 5 idle
keywords burning bid floor and one query bleeding 30% of spend
to a wrong-audience match. The overview can't show those.
Drilling-and-finding-nothing is fine; not-drilling is a defect.

If audit time is genuinely tight, drill in *priority order*:

1. **Waste candidates first** — campaigns with ROAS ≤ breakeven.
   Most actionable bid-trim and negate items live here.
2. **PROTECT confirmation next** — biggest-spending campaigns
   above breakeven. Verify they really are healthy by reading
   their keywords/queries.
3. **Expansion candidates** — highest-ROAS campaigns regardless
   of spend level. A high-ROAS campaign spending AED 2/day
   against a AED 15/day cap is the highest-ROI budget move in
   the portfolio; budget-raise decisions require verified
   Customer Queries data, not overview metrics.

**Do not skip expansion candidates as "low spend, low priority"** —
that's how a high-ROAS, under-budgeted Auto campaign gets ignored.

Either way, **do not stop mid-list and write generic
recommendations for the rest.** Either drill every active
campaign, or explicitly mark the remainder as
`Skipped — incomplete data (out of audit budget)` and surface
that gap to the user.

**Time-budget reality.** Each drill takes ~45–60 s (navigate +
tab click + eval + a Customer-Queries eval). For a portfolio of
~12 active campaigns across AE+SA, expect 9–12 minutes for data
capture alone, plus 10–15 minutes for analysis and writing. If
the combined portfolio exceeds ~15 active campaigns, split into
separate AE and SA tasks rather than racing both in one.

**Vague action items are evidence the drill was skipped.**
Phrases like *"Open Targets tab and trim any keyword bid
exceeding Recommended max by ≥ 20%"* or *"Check Customer
Queries for negative candidates"* in your output mean you're
asking the user to do the drill you should have done. If you
catch yourself writing one of those, go back and capture the
data — then write the specific keyword names, current bids,
and target bids in the recommendation.

## Notes

- **Cover every active campaign — no silent skipping.** Every
  active campaign gets a per-campaign section. A healthy campaign
  with no actionable issues still gets one — header row + a single
  Problem subsection ("No actionable problems this round" + data
  table + watch-next metric). `Skipped — incomplete data` is
  acceptable only when the agent attempted the capture and it
  failed for an infra reason (stated in the section).

- **Surface, don't auto-execute.** Recommendations include current
  value, proposed value, and reason; the user confirms before
  state-changing clicks.

- **Keyword anomalies (typos, sub-floor bids, literal duplicates)**
  — prefer deletion over pause when a clean form already exists in
  the same campaign. "Pause-and-leave" turns one-time data slop
  into permanent Targets-tab clutter that future audits re-read
  forever.

- **Bid trim/raise** — noon's `Recommended` column is the peer
  reference. Read it before recommending a bid move.

- **Cross-campaign keyword overlap.** After capturing all
  Targets tables for a country, check for the same keyword (and
  match type) appearing in multiple campaigns. *Same keyword +
  same match type across 2+ campaigns = internal bid war* — all
  of them clear at the same auction price. Surface this in the
  header table's *Major issues* column (e.g.
  `(d) 3 campaigns competing on "socks for women" Phrase`) and
  resolve in the relevant per-campaign Problem subsections (the
  trim must be coordinated across all conflicting campaigns;
  trimming one in isolation just shifts the win to another).

## Output format

### Structure (in order)

The report is intentionally compact. Per campaign: data tables
with a per-row **`recommendation`** column (data + decision in one
place). No per-Problem prose, no separate Recommendations summary
— each row carries its own action verb. The user reads top-to-
bottom and replies with action numbers from the global checklist.

1. **Today's session — overview**: header table + defaults +
   recommended sequence.
2. **One section per campaign**: `# Campaign <#>: <name>` →
   `## Campaign data` (funnel snapshot + Targets/settings table +
   Customer Queries table; each table ends with a
   **`recommendation`** column carrying the per-row action —
   Pause / Trim to X / Scale / Hold / Negate / Harvest).
3. **What to watch next** — ≤ 3 lines, cross-campaign, with the
   concrete metric and re-check window.
4. **Action checklist** — nested-by-campaign list (one top-level
   item per campaign with at least one action; sub-letters
   `1a`, `1b`, … for each specific action under it). Numbers are
   global across the whole audit. The user uses these numbers
   and sub-letters to opt in/out on follow-up. See full format
   below.

### Multi-country audits

When the request covers more than one country, repeat steps 1–3
above per country under top-level headings, then produce ONE
shared action checklist (step 4) at the very end. Defaults /
PROTECT list / Recommended sequence are listed *per country*;
the action checklist is *cross-country* with one continuous
top-level numbering — country-A campaigns are 1..N, country-B
campaigns continue at N+1..M (so a sub-action like `8a` is
unambiguous).

```markdown
## Country 1: <code-1>
…header table, per-campaign sections, What-to-watch-next for country 1…

## Country 2: <code-2>
…header table, per-campaign sections, What-to-watch-next for country 2…

## Action checklist
### <code-1>
1. **Campaign 1** — …
   1a. …
…

### <code-2>
8. **Campaign 1** — …
   8a. …
```

**Tables, not prose.** All findings and actions live in tables.
The `Why` cell carries the one-line data citation that supports
the action (numbers, not adjectives). Don't expand cell
rationales into paragraphs; if a recommendation needs a longer
justification than a sentence, the data table itself should
carry it (one row per cited entity).

### Header table

Columns (note: **no ACOS** — noon's primary efficiency metric is
ROAS):

| Column | Notes |
|---|---|
| `#` | session number |
| `Campaign` | full name (the trailing `- agent` flag stays) |
| `30d spend` | total cost in marketplace currency (AED for AE, SAR for SA) |
| `orders` | from the campaign top-tile |
| `revenue` | not "sales" — noon labels this Revenue |
| `ROAS` | bold if PROTECT-tier; bold if **0.00** |
| `CTR` | helps spot creative-side breaks at a glance |
| `Daily budget` | with currency. Note `Out of budget` in Status if applicable. |
| `Strategy` | bidding strategy + plain-English diagnostic when relevant (e.g. `Auto / Fixed (shadowed by Auto sibling — ROAS 5.09)`). |
| `Status` | Live / Paused / Out of budget / Draft (per `../SKILL.md § 2`). |
| `Major issues found` | comma-separated, brief — e.g. `(a) "<kw>" Phrase 0 orders / 30 clicks; (b) competitor-brand queries consuming impressions` |

Below the table:
- **Defaults applied** — margin (assumed; user can correct on
  next round), target ROAS, waste/harvest cutoffs. Default
  margin **25%** for general apparel / accessories; adjust by
  category when the catalog makes that obvious. Don't ask the
  user upfront; surface the assumption here. Formulas in
  `app/skills/amazon-ads/references/tuning-thresholds.md`.
  Short form: `breakeven_acos = margin`, `scale_target_acos =
  0.7 × breakeven_acos`, `target ROAS = 1 / target_acos`.
  At margin 25%: target ROAS 5.71. At margin 40%: target ROAS 3.57.
- **PROTECT list** — campaigns/keywords with ≥ 1 order in the window.
  Surface, never auto-cut.
- **Recommended sequence** — ordered list with reasoning. Don't
  recommend changes that interact in the same step.

### Per-campaign section

#### `## Campaign data`

**Funnel snapshot** (lead with this — required):

```
| Stage    | Value      | Rate to next                |
|---|---|---|
| Views    | <n>        | —                           |
| Clicks   | <n>        | CTR = Clicks/Views          |
| ATC      | <n>        | CvR(click→ATC) = ATC/Clicks |
| Orders   | <n>        | CvR(ATC→order) = Orders/ATC |
| Spend    | <ccy x.xx> | —                           |
| Revenue  | <ccy x.xx> | ROAS = Revenue/Spend        |
| CTR      | <n.nn%>    | (against same-country peer median; flag if break) |
```

**Targets table** (Manual campaigns) — the **SOI** column is
noon-specific (Share of Impressions: proportion of available
impressions your bid won for this keyword; low SOI on a high-intent
keyword = competitive slot, raise candidate). The last column is
**`recommendation`** — the per-row action so the reader sees data +
decision in one place.

```
| Target (match)   | Status     | Bid (ccy) | eCPC | Recommended (range) | SOI    | Views | Clicks | CTR    | Orders | ROAS | recommendation                                       |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `<kw-1>` (Exact) | Delivering | 1.00      | 1.00 | 0.80 (0.70–1.00)    | 10.0%  | 200   | 10     |  5.00% | 1      | 4.00 | Hold — at recommended midpoint, 1 order, ROAS 4.00   |
| `<kw-2>` (Exact) | Delivering | 2.00      | 2.00 | 1.00 (0.50–1.50)    | 30.0%  |  31   |  2     |  5.00% | 0      | 0.00 | Trim to 1.50 — bid 2× rec midpoint, 0 orders         |
| `<kw-3>` (Phrase)| Delivering | 1.00      | —    | —                   |  —     |   0   |  0     | —      | 0      | —    | Hold — 0 views, no signal yet                         |
```

For Auto campaigns, omit Targets and substitute a small settings
table (Default Bid + Bidding Strategy at minimum) with a
recommendation column for each row.

**Customer Queries table** (always — at least the spending queries.
Last column is **`recommendation`** — Negate / Harvest / Hold per row):

```
| Query             | Matched via    | Views | Clicks | CTR    | Spend (ccy) | Orders | recommendation                                  |
|---|---|---|---|---|---|---|---|
| `<wasteful-1>`    | `<kw>` Phrase  | 28    | 3      | 10.0%  | 3.00        | 0      | **Negate Phrase** — 3 clicks, 0 orders          |
| `<harvest-cand>`  | `<kw>` Phrase  | 12    | 2      | 16.7%  | 2.00        | 1      | **Harvest** to Exact + back-negate from Phrase  |
| `<competitor>`    | `<kw>` Exact   |  1    | 1      | 100%   | 1.00        | 0      | **Negate Phrase** — competitor-brand searcher   |
```

**`recommendation` cell content rules** (apply to all entity tables):

- **Action verb first**, bolded for state-changing actions
  (`**Pause**`, `**Negate Phrase**`, `**Harvest**`, `**Trim to <ccy
  x.xx>**`, `**Scale**`); unbolded `Hold` / `Hold (PROTECT)` for
  no-change rows.
- **Reason after** (one short clause), citing numbers from the same
  row: *"3 clicks, 0 orders"*. Numbers, not adjectives.
- **Cite the funnel detail, not the shorthand** — *"9 clicks / 3
  ATC / 0 orders — checkout drops"* beats *"ROAS 0.00"*.
- **No "monitor" / "evaluate" / "wait"** rows — those go in
  *§ What to watch next*, never in the data tables.
- **For non-obvious reverts** (e.g. bidding-strategy switch,
  pausing a PROTECT campaign): append `Revert: <one-line click
  path>` to the recommendation cell. Skip when revert is the
  obvious inverse (raise the bid back, re-enable the keyword).
- **LISTING-FIRST findings**: recommendation = *"Pause until
  listing fix"* with reason citing the funnel break (low CTR /
  low CvR localised to one stage).
- **PROTECT keywords / campaigns** (≥ 1 order in window): `Hold
  (PROTECT) — N orders, ROAS X.XX`.

**Markdown table formatting — pipe count must match.** The
separator row `|---|---|...|` must have exactly the same number of
`|---` cells as the header row's column count. Verified-in-the-wild
failure: header had 6 columns, separator had 7 `---|` cells →
markdown rendered the entire table as inline pipe-text on one
line, NOT as a table. Count the columns in the header and write
exactly that many `---|` cells in the separator.

**Funnel-stage table** (LISTING-FIRST diagnoses — when ATC→order
collapses):

```
| Stage        | Clicks | ATC | Orders | CvR(ATC→Order) | recommendation                            |
|---|---|---|---|---|---|
| <campaign>   | 9      | 3   | 0      | 0%             | Pause until listing fix — checkout drops  |
```

**Campaign-level row** (only when a strategy / budget action is
proposed; otherwise omit this block):

```
| Field             | Current         | recommendation                                       |
|---|---|---|
| Bidding strategy  | Manual / Fixed  | Switch to Dynamic-down — no real-time adaptation    |
| Daily budget      | <ccy 15>        | Hold — spending <ccy 12>/day, on quota              |
```

### Style

Every row in the data tables has either an action verb or `Hold`
in its `recommendation` cell. Rows without an action don't belong
in the report — drop them. Recommendations cite numbers visible in
the same row; un-cited recommendations are speculation.

### Action checklist — nested per campaign, keyword-level specifics

The action checklist at the end of the report is the user's
selection menu — they reply with action numbers to run. The
checklist must be **nested by campaign** and every leaf item
must name the specific keyword, query, or budget value it
operates on. Generic items are not allowed.

Format (one block per country, with a continuous top-level
numbering that does NOT restart at each country):

```markdown
### <country-A code>

1. **Campaign 1 (<top-performer Auto>)** — star performer, expand
   1a. Raise daily budget <ccy> 15 → 25 (currently spending ~<ccy> 2.40/day, ROAS 8.20)

2. **Campaign 2 (<sibling Manual>)** — ROAS 2.06, eCPC 41% above Auto sibling
   2a. Trim `<exact-keyword>` Exact bid <ccy> 1.20 → 0.85 (Recommended 0.78, 0 orders / 6 clicks)
   2b. Trim `<phrase-keyword>` Phrase bid <ccy> 1.50 → 1.00 (Recommended 0.92, 0 orders / 4 clicks)
   2c. Add `<wrong-audience-query>` as Negative-Phrase (3 clicks / 0 orders / <ccy> 4.50)
   2d. Delete `<typo-keyword>/` Phrase (bid 0.001, 0 views — clutter)

3. **Campaign 3 (<broken-listing Auto>)** — listing-first
   3a. Pause campaign until variants flagged by catalog are cleared in Seller Center

### <country-B code>

4. **Campaign 1 (<…>)** — …
   4a. …
```

Rules:

- **Top-level numbers are campaigns** (one per active campaign
  that has at least one action). A campaign appears in the
  checklist if it has *any* sub-action — even a PROTECT
  campaign can appear if it has cleanup items (e.g. a brand-
  negate to apply, or a daily-budget raise to capture more of
  a high-ROAS opportunity). The PROTECT label only means the
  core campaign strategy is preserved (no pause, no strategy
  flip); it does not exclude the campaign from the checklist.
  Campaigns with literally zero sub-actions belong in §3
  *What to watch next*, not the checklist.
- **Sub-letters (1a, 1b, 1c, …) are the specific actions** the
  user can opt in/out of. Each sub-action names the exact
  keyword / query / budget number / variant being changed.
- **No generic items.** "Open Targets tab and trim" is a
  defect; if you write that, go drill the campaign first.
- **No monitoring / re-evaluate / wait items.** The checklist
  is for actions the user can execute *now*. Items like
  *"monitor ATC-to-order for 14 days"* or *"re-evaluate
  budget at next check"* don't have do/don't-do semantics —
  the user can't meaningfully *skip* a 14-day watch. Those
  go in §3 *What to watch next* per country, never here. If
  you catch yourself writing "Monitor", "Watch", "Evaluate at
  next check", "If X then Y over N days" — that's not a
  checklist item.
- **Reply protocol:** the user can opt in by sub-action
  (`2a, 2c, 3a`), by full campaign (`2 all`), or with prose
  like `1, 2 all, 3 skip`. Per-item parameter overrides
  (`2a trim to 0.95 instead of 0.85`) apply before execution.

LISTING-FIRST diagnoses still appear in the checklist (as
"pause" / "exclude" actions); the user can decline to pause
and pause-via-listing-fix instead.

### Follow-up execution

If the user's reply is a list of action numbers (with optional
per-item overrides), do **not** replan from scratch. Read the
prior checklist from the same task's preceding messages, parse the
reply, and execute only the selected items.

Reply parsing:

- Bare sub-actions (`2a, 2c, 3a` or `4 5 7`) → execute those, skip the rest.
- Full campaigns (`2 all`) → execute every sub-action of campaign 2.
- *Don't do* / *skip* phrasing (`1 2 3 don't do, 4 5 6 7 do`) →
  execute the *do* set, skip the *don't do* set.
- Per-item override (`2a trim to 0.95 instead of 0.85`) → apply the
  override, then execute that sub-action.
- Ambiguous reply → ask one clarifying question via
  `vibe_seller_ask_user_question` rather than guessing.

After execution, post a one-line summary:
*"Executed: 2a, 2c, 3a. Modified: 2a (bid 0.95 instead of 0.85).
Skipped: rest."*

### Worked example — one complete per-campaign section

The structure below is the contract. Match it section-for-section
when drafting the report. Compare your draft against this example
before submitting; if a piece is missing or out of order, fix it.

```markdown
# Campaign 3: <Generic Manual Sock Campaign>

## Campaign data

### Funnel snapshot

| Stage | Value | Rate to next |
|---|---|---|
| Views | 400 | — |
| Clicks | 20 | CTR = 5.00% |
| ATC | 4 | CvR(click→ATC) = 20.0% |
| Orders | 2 | CvR(ATC→order) = 50% |
| Spend | <ccy 17.00> | — |
| Revenue | <ccy 37.00> | ROAS = **2.18** |
| CTR | 5.00% | (vs same-country peer median 2.00% — strong creative) |

### Targets table

| Target (match) | Status | Bid | eCPC | Recommended (range) | SOI | Views | Clicks | CTR | Orders | ROAS |
|---|---|---|---|---|---|---|---|---|---|---|
| `<kw-1>/` (Exact) | Delivering | 1.00 | 1.00 | 0.80 (0.70–1.00) | 10.0% | 200 | 10 | 5.00% | **1** | **4.00** | Hold (PROTECT) — 1 order, ROAS 4.00, at recommended midpoint |
| `<kw-2>/` (Phrase)| Delivering | 1.00 | 1.00 | 1.00 (0.80–1.20) | 40.0% |  60 |  3 | 5.00% | 0     | 0.00     | Hold — 3 clicks small sample, watch 7d                       |
| `<kw-3>/` (Exact) | Delivering | 2.00 | 2.00 | 1.00 (0.50–1.50) | 30.0% |  33 |  2 | 5.0%  | 0     | 0.00     | **Trim to 1.50** — bid 2× rec midpoint, 0 orders             |
| `<kw-4>/` (Exact) | Delivering | 1.00 | —    | 0.80 (0.70–0.90) | —     |   0 |  0 | —     | 0     | —        | Hold — 0 views, no signal                                    |
| (10 idle keywords, 0 views) | Delivering | 0.50–1.50 | — | varies | — | 0 | 0 | — | 0 | — | **Pause all idle** — clutter, 0 views                  |

(Header has 13 columns; separator must have exactly 13 `---|` cells.)

### Customer Queries (top spend)

| Query             | Matched via         | Views | Clicks | CTR    | Spend (<ccy>) | Orders | recommendation                                    |
|---|---|---|---|---|---|---|---|
| `<query-1>`       | `<kw-1>/` Exact     | 200   | 10     | 5.00%  | 10.00         | 1      | Hold — converter at this match level              |
| `<query-2>`       | `<kw-3>/` Exact     |  31   |  2     | 5.0%   |  4.00         | 0      | **Negate Phrase** — 2 clicks, 0 orders            |
| `<query-3>`       | `<kw-4>/` Phrase    |   4   |  2     | 50.0%  |  2.00         | 0      | Hold — small sample (4 views)                     |
| `<competitor>` … | `<kw-1>/` Exact     |   1   |  1     | 100%   |  1.00         | 0      | **Negate Phrase** — competitor-brand searcher     |
```

Notes on the structure:

- **One section per campaign**: `## Campaign data` only (funnel +
  Targets + Customer Queries). Each entity table's last column is
  `recommendation` carrying the per-row action. No prose Problem
  subsections, no separate Recommendations summary table.
- **Every recommendation cell cites numbers visible in the same
  row.** If you can't, the data is incomplete — go capture it
  before submitting.
- **PROTECT keywords / campaigns** (≥ 1 order in window) get
  `Hold (PROTECT)` in the recommendation cell with the converting
  metric cited (`ROAS 4.00 / 1 order`).
