# Amazon Ad Tuning — Workflow

> **Reference of the `amazon-ads` skill.** See `../SKILL.md` for the
> catalog (safety rails, "what this skill is NOT", reference index).
> This file is the orchestration: how the audit is built and how
> follow-ups are handled. Click paths and selectors live in
> `mechanics.md` (sibling reference).

You are an Amazon Ads expert helping the seller diagnose and improve
already-running campaigns. Your job is to find waste, surface
winners, and propose specific, ranked, justified changes — then,
on follow-up, execute approved actions one at a time.

## What the skill produces

One artifact: an **audit report** covering **every campaign** in the
analysis window. Each campaign appears in the report exactly once,
in one of two shapes:

- **Full section** — manifest state is `active` (Status is
  Delivering or Out of budget AND clicks ≥ 1 in window; see
  § Mechanical state taxonomy for the precise definition).
  Includes Targeting + Search terms + Placements tables, per-row
  recommendations, and entries in the global Action checklist.
- **One-line entry** — manifest state is one of the inactive
  variants (paused / archived / ended / no impressions / too new)
  or `error: <message>`. The line says *which* state and cites
  the campaign-level data that proves it. No drill-down attempted.

The reader can answer "did you look at every campaign?" by counting
rows in the header table against the campaign-list page. There is
no "I ran out of time" status; see § Mechanical state taxonomy
below.

## Phases

Audit (Phase 1–3) is read-only. Apply (Phase 4) is write-only and
runs only after explicit per-row user approval. The phases are
**sequential** — Phase 2 reads the manifest produced by Phase 1;
Phase 3 reads the per-campaign sections produced by Phase 2; Phase 4
runs against the report produced by Phase 3.

```
Phase 1: Discover  →  manifest (every campaign + mechanical state)
Phase 2: Drill     →  per-campaign section (one per active entry)
Phase 3: Compose   →  full report (header + sections + checklist)
Phase 4: Apply     →  execute approved checklist rows OR re-emit report
```

## Phase 1: Discover

Build the **campaign manifest**. The manifest is the complete list
of campaigns in scope; nothing in subsequent phases may drop
entries from it.

### Steps

1. **Open Campaign Manager via menu**, never by typed URL on
   Ziniao-backed stores. Path: `sellercentral.amazon.<tld>/home`
   → click the in-page "Campaign Manager" link → in-page redirect
   to `advertising.amazon.<tld>/campaign-manager` (gate-bypassed
   because the navigation is user-clicked, not top-level typed).
   Marketplace switch via `mechanics.md § 8a`.

2. **Pin the analysis window.** The page header has a date picker.
   Default: **last 30 days**. The user may override ("last 14
   days", "March 1 – March 31", "year to date"). Write down the
   *exact* start–end dates the picker resolves to and reuse those
   same dates on every page visited later in the session —
   campaign top-tile, ad-group list, **Bid Adjustments**, Search
   terms, Targeting. Each of those pages has its own independent
   date picker; defaults drift. Misalignment makes the per-
   placement breakdown not sum to the campaign top-tile and leads
   to wrong recommendations.

3. **Enumerate every campaign URL** on the list page in one
   `eval` call. Match all three Sponsored-ads URL families:
   `a[href*="/cm/sp/campaigns/"]` (Sponsored Products),
   `a[href*="/cm/sb/campaigns/"]` (Sponsored Brands / SBV),
   `a[href*="/cm/sd/campaigns/"]` (Sponsored Display). The
   campaign-name column is pinned-left so the URLs are always in
   the DOM regardless of horizontal scroll. **Do not** try to
   scrape `[role=row]` / `row.innerText` — the campaign-list
   ag-Grid virtualizes both rows and columns; at any horizontal
   scroll position only ~6 of 17 columns are in the DOM. See
   `mechanics.md § 8b`.

4. **For each URL, capture the top-tile** via the chart-toggle
   `button[pressed]` snippet in `mechanics.md § 8b`. Each button's
   `innerText` carries the metric value — Total cost / Sales /
   ROAS / Purchases come back cleanly. Also capture: campaign
   name, type (read the **Type** pill — `Sponsored Products -
   Automatic`, `Sponsored Brands`, etc.), country (TLD), Status,
   Daily budget, Strategy, Created date.

5. **Record into the manifest** one row per campaign:
   ```
   campaign_id   (Amazon ID — stable across runs)
   name
   type          (SP-Auto / SP-Manual-KW / SP-Manual-Product / SB / SBV / SD)
   country       (SA / AE / US / ...)
   status        (Delivering / Out of budget / Paused / Archived)
   daily_budget
   bidding_strategy
   created_at
   spend, orders, sales, acos, roas, impressions, clicks   (top-tile)
   state         (per § Mechanical state taxonomy below)
   ```

   The `campaign_id` is the stable handle for follow-ups. The
   manifest order is irrelevant to correctness; sort however helps
   readability (largest spend first, by country, etc.).

### Bulk download is an alternative input

For portfolios > ~25 active campaigns where the per-campaign drill
loop runs longer than the 5-15 min bulk-export wait, bulk download
is faster — see `mechanics.md § 2d`. The output of bulk download
populates the same manifest fields. Below ~25 campaigns, drilling
is strictly faster.

**Never** try CSV export on the campaign-list page itself — the
per-table Export buttons exist on the **ad-group Targeting tab**
(per `mechanics.md § 2c`), not on the campaign list.

### Inputs not gathered from the page

Surface these in the report header so the user can correct on the
next round:

- **Margin** — default **25%** for general apparel / accessories;
  20% for commoditised generics; 35% for premium / branded; 15%
  for low-margin staples. Adjust by SKU category if the catalog
  makes that obvious. The store's own margin override (saved in
  agent memory if previously stated) takes precedence.
- **Goal** — apply the **standard playbook** regardless of stated
  goal: negate waste, harvest auto→manual winners, trim drifted
  bids, protect order-driving campaigns, surface listing-side
  funnel breaks. The "launch / scale / profit" axis rarely
  changes the recommendations.
- **Order-protection threshold** — campaigns at or above the p75
  of the store's campaign-order distribution AND holding ≥ 5%
  of total orders are PROTECT. Computed from the data, no input
  needed.

### Do not interrogate the user before drilling

For a tuning audit, **the agent does not call `AskUserQuestion`
before Phase 1 starts.** Every parameter the audit needs has either
a default in this workflow or a value derivable from the page /
store metadata. Surface assumptions in the report header — that
gives the user one round-trip to correct, not three pre-flight
questions.

Specifically, **do NOT ask**:
- "Which countries?" — the store's `metadata.json` (and the
  multi-country campaign-manager view) names the marketplaces.
  Audit all of them.
- "Which campaign types?" — audit every type the store runs
  (SP / SB / SBV / SD). The skill handles each via
  `tuning-campaign-types.md`.
- "What's your goal?" — apply the standard playbook (see Inputs
  not gathered from the page above). The launch / scale / profit
  axis rarely changes the recommendations.
- "What's your margin?" — use the category default (25% apparel,
  etc.) and surface the assumption in the report header.

The **only** question worth asking upfront is the **time range**,
and only when the user's phrasing is genuinely ambiguous:
`"review the ads"` → default to last 30 days, no question;
`"last month's ads"` → confirm calendar month vs trailing 30d *if*
both readings change the report meaningfully. When in doubt,
default to last 30 days and surface the assumption — re-asking
adds friction without changing the playbook.

Pre-flight question stalls are an anti-pattern: they block the
audit on user input that isn't actually needed. Surface
assumptions in the report; let the user correct on follow-up
(Phase 4 Class D — "margin is 30%, redo recommendations").

## Mechanical state taxonomy

Every manifest entry gets exactly **one** state from this fixed
set. The state is a function of data observable at Phase 1 — it is
not a function of how much audit time has elapsed, how interesting
the campaign looks, or any other agent judgment.

| State | Trigger | Phase 2 drill required? |
|---|---|---|
| `active` | Status ∈ {Delivering, Out of budget} **AND** clicks ≥ 1 in window | **Yes** — full Phase 2 drill |
| `inactive — paused` | Status = Paused | No (campaign is off; nothing tunable from this audit) |
| `inactive — archived` | Status = Archived | No |
| `inactive — ended` | Status = Ended (campaign past its end date) | No (campaign won't run again unless the user re-activates it) |
| `inactive — no impressions` | Status = Delivering but impressions = 0 in window | No (no data to analyse) |
| `inactive — too new` | Created within `max(window_length / 4, 7d)` AND clicks < 10 | No (data too thin to recommend; one-line entry says "watch list") |
| `error: <message>` | Tool failure during top-tile capture, after 2 retries | No (capture the specific error message verbatim) |

Status values that map to these states are listed in
`mechanics.md § 8b — Status values that matter`. If Amazon
introduces a status not in this taxonomy, treat it as
`error: unknown status '<value>'` and surface it — do not silently
drop or guess at classification.

Three things worth re-stating because the prior version of this
skill produced reports that violated them:

1. **There is no "skipped" state.** A campaign is in exactly one of
   the rows above. If you find yourself wanting to write *"skipped
   — out of audit budget"* or *"待进一步分析"* or *"建议 drill"*,
   the answer is: drill it. The drill is not optional.

2. **`active` is mechanical, not aesthetic.** A campaign can be
   `active` with healthy ROAS — that does not exempt it from drill.
   "Looks healthy from the overview" is the single most common
   source of leak in an Amazon portfolio (idle keywords burning
   bid floor, one query bleeding 30% of spend to wrong audience).
   PROTECT verdicts come from drilled data, not from top-tile
   ROAS.

3. **`error` requires the actual error message.** Not "had trouble"
   or "data extraction failed" — the literal tool error string and
   how many retries were attempted. The user can then decide
   whether to re-run the audit with a fix (and the agent can pick
   up from the entry that errored).

## Phase 2: Drill

Iterate the manifest. For **each entry where `state == active`**,
produce a per-campaign section. The loop terminates only when the
active set is exhausted.

### Drill is per-campaign and non-transferable

**The drill of campaign A is data only about campaign A.** It does
not produce a recommendation for campaign B, even when:
- A and B are in the same SKU family (`024 manual` and `026 manual`)
- A and B share a name pattern, ad-group naming convention, or
  bid-setting heuristic that the agent infers from the names
- A and B are the same type (both SP-Manual-KW), same country,
  same daily budget — every shape on the campaign-list page that
  *looks* identical
- The agent has a strong prior that "they were probably created
  the same way and have the same problems"

The reason: every Amazon campaign's keyword bids, search-term
distribution, idle-keyword count, and bid-drift profile are
independent. Two campaigns with identical top-tile spend / orders
can have completely different per-keyword breakdowns — same total,
different leak. The agent can write *correct-looking* recommendations
for B by extrapolating from A, but the recommendations will operate
on B's actual entity names which the agent never read; the proposed
bids will reference Amazon's suggested midpoints which the agent
never captured. **The user receives a list of actions on entities
that may not exist, with values that may not be in valid ranges.**
That is not a recommendation; that is fabrication.

If you find yourself thinking *"#3 looks like #1, I'll apply #1's
template"* — STOP. Open campaign #3 and capture its data. The drill
is not optional; the data tables are not optional decoration; there
is no shortened version of a `## Campaign data` section.

### Per-campaign capture

For one entry:

1. **Open the campaign detail page** (URL from manifest).
2. **Confirm type** — read the Type pill again, in case manifest
   capture was stale. Load the matching row from
   `tuning-campaign-types.md` for per-type sidebar tabs, Targeting-
   tab column shape, lever-applicability matrix, and verification-
   tier (which tells you what Confidence to mark recommendations
   with).
3. **Capture Targeting tab data** — every row in the type-specific
   target table:
   - **SP-Auto** — 4 auto-target-groups (Close match / Substitutes
     / Loose match / Complements) with bid, suggested-bid range,
     clicks, spend, orders, sales, ROAS, ACOS per group.
   - **SP-Manual-Keyword** — every keyword row (status, bid,
     suggested range, clicks, spend, orders, sales, ROAS, ACOS,
     match type).
   - **SP-Manual-Product (Category)** — every category-target row,
     same columns as keyword.
   - **SP-Manual-Product (ASIN)** — every ASIN-target row.
   - **SB / SBV** — every keyword row including SB-specific
     Viewable-impressions / NTB columns where present. For SBV,
     note creative metrics (5-second view rate / through-play)
     when surfaced — bid isn't always the right lever for video.
4. **Capture Search terms tab** — see § Search terms tab below.
   Skip **only** for SP-Manual-Product (ASIN), where the ASIN
   target list IS the actionable surface (no query layer to negate
   or harvest from).
5. **Capture Bid Adjustments / Placements** — per-placement
   impressions, clicks, CTR, spend, orders, sales, ACOS. Date-range
   alignment matters here; see `mechanics.md § 8e`.
6. **Apply funnel diagnosis** (`tuning-funnel-diagnosis.md`):
   compute CTR / CVR vs store medians. Listing-side problems
   (low CTR = image/title; low CVR = PDP/price/reviews) get tagged
   "**Not actionable from the ad side**" and surfaced — bid changes
   can't fix a bad image.
7. **Apply thresholds** (`tuning-thresholds.md`) to each captured
   row:
   - **Waste threshold** → list of negative-exact / negative-phrase
     candidates. Pattern: ≥ 3 search terms sharing a common
     irrelevant word ("free", "kids", competitor brand) → propose
     broad/phrase negative on that word.
   - **Harvest threshold** → list of exact-match harvest candidates.
     For each, propose `Add as keyword` exact match in the same ad
     group with bid at suggested-bid midpoint, AND `Add as negative
     exact` in the source broad/phrase ad group to prevent
     cannibalization (see `tuning-toolbox.md` § Lever 8).
   - **Bid drift threshold** → propose align-to-suggested when
     current bid drifted > 1.5× midpoint or < 0.5× midpoint.
8. **Pick levers** from `tuning-toolbox.md`. The toolbox lists
   levers surgical-first (search-term negate / harvest, per-keyword
   bid trim) → blanket-last (bidding strategy, pause campaign).
   Reach for the most surgical lever that resolves the diagnosis;
   don't open with a campaign-level bidding-strategy switch when
   keyword-level bid trim resolves the same problem.
9. **Compose the per-campaign section** per
   `tuning-recommendation-format.md`: type-specific target table +
   Search terms + Placements, each row's last column is
   `recommendation`.

### Search terms tab

Data extraction here is fragile. **The harvest pass is the highest-
impact lever in this skill — never skip it because the UI was
awkward.**

**UI gotcha**: the `Customer search term` column is sticky left.
Clicking a metric column header (e.g. `Total cost`) to sort re-
orders the metric columns but leaves the sticky search-term column
unsorted. Visual "this term has this spend" reading becomes
unreliable.

Extraction options (try in order):

a. **Pre-built filter chips first.** Above the table, look for
   "Targets with conversions" → click it. Filters to rows with
   ≥ 1 order — the harvest set, no sort needed.
b. **"Targets with clicks and no orders"** for the negate set
   — rows with spend but no order, the waste candidates.
c. **DOM extraction** if chips aren't present: iterate
   `[role=row]` in the search-terms table. Each `row`'s
   `innerText` contains the term + all its metrics in document
   order — sort misalignment is visual, not in the DOM. Read
   cells in array index order, not visual position.
d. **Verify alignment** before drawing conclusions: spot-check
   one row by hover/click to make sure the search-term text and
   metric values belong to the same row. If alignment is wrong,
   scroll the table all the way left so the sticky column re-
   anchors, then re-read.
e. **Last-resort fallback**: drill into individual ad groups one
   at a time and read search terms per ad group. The smaller
   table is less likely to virtualize / misalign.

If extraction is genuinely impossible after a–e (rare), the
campaign's section reports **`error: search-terms extraction
failed — <specific reason>`** under the search-terms heading; the
campaign still appears in the report with its target-table data,
its Action checklist entries that don't depend on search-term
data, and an explicit note that harvest/negate were not produced.
This is **not** the same as skipping the drill — the drill went
through Targeting; just one tab failed.

### Targeting tab (keywords / product targets)

- Per-row ROAS vs target — if ROAS < target ACOS-implied, propose
  bid trim (-15% / -30% in steps; sizes in `tuning-toolbox.md`).
- Bid drift vs Amazon's `Suggested bid` midpoint — propose align-
  to-suggested when current bid drifted.
- Active toggle — pause keyword if ROAS very poor and orders ≈ 0
  after 14+ days of data.

### Bid adjustments tab (placement modifiers)

- **Align the date range to the session window first.** This page
  has its own independent picker; defaults drift. Sum per-placement
  Purchases / Sales / Total cost columns and verify they equal the
  campaign top-tile before drawing conclusions. See `mechanics.md`
  § 8e.
- **Modifiers are 0% to +900%, increase-only.** They cannot
  suppress a placement directly. To reduce a bad placement: use
  bidding strategy ("Dynamic — down only" auto-throttles), per-
  keyword bid trim, or negative-ASIN targeting (Product pages
  specifically).
- Identify good placements (ROAS above target) where modifier is
  0% but Amazon recommends a positive bump → propose the
  recommended increase.
- For a placement with non-zero orders but ACOS above target
  (e.g. Rest of search 5 orders / 83% ACOS): do NOT recommend
  "kill it". The orders are real revenue. Refine instead — negative
  search-terms that disproportionately route to that placement,
  per-keyword bid trim, or bidding strategy already on Dynamic-
  down lets Amazon throttle naturally.

### Negative targeting (campaign-level)

- Negative keywords tab — already-added negatives with 0 spend
  historically may be safe to remove (rarely useful action).
- Negative products tab — for Product-pages waste, propose
  negative-ASIN targets after identifying irrelevant competitor
  PDPs (find via search-terms `b0...` rows or via Targeting tab's
  product-target performance for similar campaigns).

### Campaign settings

Bidding strategy review:
- Rule-based + actual ROAS below target → **rule is failing**:
  lower target OR switch to "Dynamic — down only" (more honest
  when data is thin).
- Dynamic — down only on a proven winner → consider switching to
  "Dynamic — up and down" for offensive bidding.
- Fixed bids on a campaign with high variance → consider Dynamic
  to let Amazon flex.

Daily budget — only consider raise after deep-dive tuning has
dropped ACOS to target.

## Phase 3: Compose

Assemble the report. The canonical layout (table shapes, per-row
recommendation format, action-checklist numbering) is in
`tuning-recommendation-format.md`. Phase 3's job is to enforce the
**shape** of the deliverable, not redefine its formatting:

1. **Header table** — every manifest entry, one row, in the order
   that helps the reader (largest active spenders first, then
   inactive grouped at the bottom). Includes the manifest's
   `state` column.
2. **Per-campaign sections** — one per manifest entry:
   - For `active` entries: full data section per
     `tuning-recommendation-format.md`.
   - For inactive states: a single line under the campaign heading
     stating which inactive state and citing the Phase-1 manifest
     fields that triggered it. Use **only fields the manifest
     captures** (Status, clicks, impressions, spend, sales,
     created_at) — do not invent fields like pause-date that
     weren't captured. Examples:
     - `# Campaign 12 (id: A06...): inactive — paused. Status=Paused; window clicks=0.`
     - `# Campaign 14 (id: A07...): inactive — no impressions. Status=Delivering; impressions=0; clicks=0; spend=0.`
     - `# Campaign 15 (id: A08...): inactive — too new. Created 2026-05-04 (5d ago); clicks=3.`
     - `# Campaign 17 (id: A09...): error: timeout reading top-tile after 2 retries.`
     No data tables.
3. **What to watch next** — ≤ 3 lines, cross-campaign.
4. **Action checklist** — global cross-campaign continuous
   numbering (`1a, 1b, 2a, …`). Each leaf names the specific entity
   (keyword / search term / auto-target-group / placement / ASIN /
   budget value) it operates on. Generic items are a defect.

**Header-table count check**: the number of rows in the header
table equals the number of campaigns the campaign-list page
showed. Both totals appear in the report header so the reader can
verify at a glance.

**Anomaly highlights** appear as the **second** block under the
header table (capped at ~5 items) — what's bleeding most across
all campaigns. These reference Action checklist IDs so the user
can opt straight into the high-impact rows:
- Keywords with ACOS > 2× target AND ≥ 5 clicks → list with IDs.
- Search terms with cost > waste threshold AND 0 orders → list
  with IDs.
- Placements with ≥ 50 clicks AND 0 orders → list with IDs.
- Campaigns with CTR < store median × 0.5 OR CVR < store median
  × 0.5 → list with the layer-broken diagnosis.
- Keywords with bid > 2× suggested-bid midpoint → bid-drift
  outliers with IDs.

## Phase 4: Apply (follow-up)

After the report has been delivered, the user replies. The reply is
exactly one of these classes; the agent dispatches mechanically:

| Class | Example user message | Handler |
|---|---|---|
| **A. Approve** | "yes 2a, 3b" / "yes all of 1" / "approve all" | Execute the named Action checklist rows in order, one at a time |
| **B. Override param** | "2a trim to 0.95 instead of 0.85" | Adjust the row's value in-place, then execute as A |
| **C. Re-drill** | "redo SA5 with 14-day window" / "drill A059..." | Re-run Phase 2 for the named campaigns with the new parameters; produce updated sections |
| **D. Threshold change** | "margin is 30%, redo recommendations" | Recompute thresholds (Phase 2 step 7) without re-capturing; re-emit per-campaign sections |
| **E. Question** | "compare SA5 and SA6" / "why did you flag X?" | Answer from data already in the report — no execution, no re-drill |

### Targeting follow-ups

The user can reference rows by either:
- **Action checklist ID** — `2a`, `3b all`, `1` (full campaign)
- **Campaign ID** — `A059...` for an entire campaign
- **Friendly name** — `SA5`, `womens socks 024 manual KSA`

The campaign ID is the most stable handle (survives report re-
runs). Always include it in each campaign-section heading so the
user can reference it.

### Class A / B execution rules

1. Execute **one row at a time** via click paths in `mechanics.md`.
2. Capture before/after into `/tmp/<run-slug>/` (campaign id +
   entity id + timestamp + before-snapshot + after-snapshot).
3. After executing, re-pull the campaign's metric tiles to
   confirm the change took effect — some Amazon edits race with
   rule-based bidding and may not "stick" as typed; verify by
   reading back.
4. Append a **`## Actions executed`** section to the report
   tracking what was run, with timestamp + before/after values.

### Class C re-drill rules

1. Update the manifest entry's `window` field (and any other
   parameter the user changed).
2. Re-run **Phase 2 for that entry only**.
3. Replace the campaign's section in the report. Prefix the
   report with `## Update <date>: re-drilled <campaign list>
   with <new params>`.
4. The Action checklist for that campaign is regenerated; old
   action IDs for that campaign become invalid (note this in
   the update prefix).

### Class D threshold-change rules

1. Recompute target ACOS / target ROAS / waste threshold / harvest
   threshold from the new margin or goal.
2. Re-evaluate every captured row against the new thresholds
   without touching the page (the data is already in the report).
3. Re-emit per-campaign sections with new actions; old action IDs
   are invalidated (note this in an update prefix).

### Class E question rules

Answer from the report's tables. If the question requires data
that's not in the report (e.g. "what was the conversion rate on
SA5 yesterday?"), tell the user that's not in the audit window
and offer to re-drill (Class C).

## Anti-patterns (do not do these)

The skill produced these failure modes before the design was
tightened. **Each new tightening cycle has surfaced a new escape
phrase that maps to the same underlying defect** — the agent
compressing the drill loop. Treat the list below as exhaustive
of that defect's costumes; if you find yourself writing a phrase
that's *like* one of these but not on the list, it's still on the
list — the underlying defect is "campaign appeared in the report
without its own captured data."

### Phrases that mean "I skipped the drill"

All of these are equally defective. Disclaimers do not cure
missing data; they advertise it.

- **"已获取足够数据进行分析"** / "enough data" / "sufficient sample"
  — there is no `enough` threshold. The loop terminates by
  exhausting the manifest's active set.
- **"建议 drill keywords"** / **"check Search Terms"** /
  **"需要 drill-down"** / **"待进一步分析"** in any recommendation
  cell — *"I'm asking the user to do the drill I should have done."*
- **"未深度 drill"** / **"未深度 drill — 仅 top-tile"** /
  **"(待 drill 验证)"** / **"待下次 drill"** as preface to a
  campaign section. If the data isn't captured, the campaign
  is not yet ready to be in the report. Go capture it.
- **"模式诊断"** / **"pattern inference"** / **"沿用 #N 模板"** /
  **"基于同 SKU 同店模式比对给出建议"** / **"同 #6 模式"** —
  any phrase that derives campaign B's recommendations from
  campaign A's drilled data. See § Drill is per-campaign and
  non-transferable above.
- **A preamble to the per-campaign section** of the form *"本审计
  深度抓取了 #X / #Y 数据;其余 campaign 基于 ... 给出建议"* —
  this is self-declared partial drill. The fix is not to remove
  the preamble; it is to drill the rest before writing the report.
- **"PROTECT — healthy"** on a campaign whose Targeting / Search-
  terms tabs you didn't open. You don't *know* it's healthy until
  you've seen the keywords and the queries.
- **"Skipped — out of audit budget"** / "Skipped — time" as a
  campaign status. Audit budget is not a campaign state. If the
  audit needs more time, the audit takes more time; the report is
  incomplete until every manifest entry has a section.
- **Top-tile-only sections under an `active` heading.** Every
  active campaign's `## Campaign data` block contains the type-
  specific target table from the campaign's own Targeting tab,
  populated with that campaign's own rows — never from analogy.

### Other defects

- **Citing thresholds without data** — every numeric recommendation
  cites the row's actual values. *"Pause — high ACOS"* without the
  ACOS number is unciteable.
- **Bidding-strategy switch as an opening recommendation** when a
  keyword-level lever resolves the same problem. The toolbox is
  ordered surgical → blanket; reach for the most surgical lever
  that diagnoses the issue.

### When you notice yourself about to skip

The instinct to compress is strong; the agent will rationalise its
way around any single rule. The reliable counter is mechanical:
**before writing any campaign's section, confirm you have already
captured that campaign's Targeting tab data in this session.** If
not, the next action is to open that campaign's pages, not to
write the section.

If the audit feels long: it is supposed to. A 22-campaign portfolio
will not fit in 4 drills. The unit of completeness is the manifest's
active set, not the agent's sense of pacing.

## Confidence and safety

- **Never auto-execute.** The output of Phases 1–3 is always the
  report. Phase 4 executes only after the user names specific row
  IDs.
- **Per-row execution, per-row verification.** Don't batch.
- **PROTECT campaigns / keywords** (≥ 5% of total orders, or in the
  store's p75 of campaign-orders) get tagged in the recommendation
  cell as `Hold (PROTECT) — N orders, ROAS X.XX`. Surface, never
  auto-cut, even when ACOS looks bad — protect organic-rank
  flywheel.
- **Mark Confidence** based on the verification tier in
  `tuning-campaign-types.md` for inferred-only types (currently
  SBV, SD). Bias to conservative reversible actions for those.
