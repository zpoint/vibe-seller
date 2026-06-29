# Audit format — canonical anchor

> **LEGACY for amazon/noon audits.** The binding contract is now
> [`output-spec.md`](output-spec.md), enforced by the server
> completeness reviewer at `set_task_result` (no `ads-format-review`
> subagent for amazon/noon). Load this file only for the exact table
> column layouts in the example below. Platforms without the server
> reviewer still use this anchor with `reviewer-loop.md`.

The deliverable for any ad-tuning audit is one Markdown file plus
one TSV per active campaign. The Markdown's shape is fixed by
this anchor. **Do not invent your own structure.** When in doubt,
mirror the example below.

## Why the Recommendation column lives in the table

Every row is one decision the user opts in or out of. When data
and decision share a row, the reader makes the call with the
evidence right next to it — no mental jump between "the table
says 75 clicks / 6 orders / ROAS 1.01" and "the recommendation
paragraph five lines later says negate this search term". One
row = one atomic action; the **Action checklist** at the bottom
just references rows by their Recommendation verb so the user
can reply *"do 1a, 2b, 3 all skip"*.

Separate Problem-1 / Problem-2 subsections force the reader to
re-derive which keyword each problem is talking about, and
quietly encourage the agent to skip rows that don't fit a
narrative — exactly the gap that produced silent-skip audits in
the past. **Recommendation goes in the table, not in a sibling
section.**

## Bid-change rule (one rule, no tiers)

**Any keyword's bid change ≤ 25 % in a single audit session,
regardless of direction.** A `Trim to <X> (−25 %)` is the
strongest downward move allowed; `Trim to <X> (−12 %)` is fine;
`Trim to <X> (−40 %)` is a defect the reviewer rejects. Same
ceiling for raises (`Raise to <X> (+25 %)` max). Stepping a bid
across multiple sessions is how you get to a target without
collapsing the keyword's traffic in one shot.

This replaces the old multi-tier (Soft / Standard / Aggressive)
classification. Single rule, easy to check mechanically, easy
for the reviewer to enforce.

## PROTECT — two simple cases

A keyword or target gets `Hold (PROTECT)` (no bid change, no
pause) if either:

1. **Efficiency PROTECT**: `ROAS ≥ 2 × target_roas` with at least
   1 order. The keyword's converting at twice the target —
   touching it risks losing efficiency for no upside.
2. **Workhorse**: this row carries `≥ 50 %` of the campaign's
   orders. Trim cap drops to `−15 %` for workhorse rows
   regardless of how high the bid drifts; pause is forbidden.

Both can apply to the same row. No other PROTECT flavors.

## Example (canonical structure — follow this shape)

The example below uses fictional data on a fictional store
(wireless earbuds, US marketplace). Real audits substitute the
store's actual data; the **shape, columns, and decision
language** are the contract.

```markdown
# 广告优化建议 — <store-slug> — <YYYY-MM-DD>

分析窗口：<from> ~ <to>（30 天）
店铺：<store-slug>
范围：<platform/country list>

阈值（毛利率 <X>%）
- Breakeven ROAS = <1/margin>；Target ROAS = <breakeven / 0.7>
- 单次会话每个 keyword bid 调整 ≤ 25 %（无论方向）
- PROTECT：ROAS ≥ 2 × target → Hold；workhorse (≥ 50% campaign orders) → soft trim max −15 %

---

## Amazon US

### A11111111 — wireless earbuds manual keyword US
**SP-Manual-Keyword** | 预算 USD 20/天 | Dynamic up-and-down
**Top-tile**: spend USD 480.00 | sales USD 1,440.00 | orders 36 | ROAS 3.00 | ACOS 33.3%
**状态**: 20 keyword，4 active / 16 paused（活跃-only ROAS ≈ 4.20）

#### Targeting

| Keyword | Match | Status | Bid | Suggested | Clicks | Spend | Orders | Sales | ACOS | ROAS | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wireless earbuds | Broad | Delivering | USD 2.00 | 0.40–0.70 | 180 | USD 360.00 | 24 | USD 960.00 | 37.5% | 2.67 | **Trim to USD 1.70 (−15 %)** — workhorse (67 % of orders), soft-trim cap |
| bluetooth earbuds | Broad | Delivering | USD 1.80 | 0.45–0.75 | 40 | USD 72.00 | 8 | USD 320.00 | 22.5% | 4.44 | **Trim to USD 1.40 (−22 %)** — within 25 % cap |
| noise cancelling earbuds | Phrase | Delivering | USD 1.20 | 0.80–1.20 | 18 | USD 36.00 | 3 | USD 144.00 | 25.0% | 4.00 | Hold — bid inside suggested range |
| earbuds for running | Exact | Delivering | USD 0.80 | 0.50–0.90 | 6 | USD 12.00 | 1 | USD 64.00 | 18.8% | 5.33 | **Hold (PROTECT)** — ROAS > 2 × target |
| (16 paused rows) | — | Paused | — | — | — | — | — | — | — | — | Hold (paused) |

#### Search Terms (top spenders)

| Term | Matched via | Clicks | Spend | Orders | Sales | ROAS | Recommendation |
|---|---|---|---|---|---|---|---|
| cheap wireless earbuds | wireless earbuds (Broad) | 60 | USD 120.00 | 3 | USD 60.00 | 0.50 | **Negate Exact** — ROAS < breakeven, drains workhorse |
| best wireless earbuds 2026 | wireless earbuds (Broad) | 20 | USD 40.00 | 4 | USD 160.00 | 4.00 | **Harvest → Exact** — high intent, lock in |
| airpods alternative | wireless earbuds (Broad) | 12 | USD 22.00 | 2 | USD 80.00 | 3.64 | Hold — at breakeven, watch 7d |
| bluetooth earbuds waterproof | bluetooth earbuds (Broad) | 8 | USD 14.40 | 2 | USD 80.00 | 5.56 | **Harvest → Phrase** — clean specifier, scale-able |

---

## Noon US

### C_DEMO0001 — Earbuds Auto US ⭐ TOP PERFORMER
**Product Ad / Auto** | 预算 USD 30/天 | Fixed bidding
**Top-tile**: spend USD 180 | revenue USD 1,260 | orders 35 | ROAS 7.00 | CTR 1.20% | CvR 16%

#### Targets

| Target | Type | Clicks | Spend | Orders | Revenue | ROAS | Recommendation |
|---|---|---|---|---|---|---|---|
| SKU-EXAMPLE-A (Earbuds Pro) | Product | 200 | USD 150.00 | 30 | USD 1,050.00 | 7.00 | **Hold (PROTECT)** — ROAS > target × 2 |
| SKU-EXAMPLE-B (Earbuds Pro variant) | Product | 50 | USD 30.00 | 5 | USD 210.00 | 7.00 | **Hold (PROTECT)** — efficient |

#### Customer Queries (top spenders) — REQUIRED for every noon campaign

| Query | Type | Clicks | Spend | Orders | Revenue | ROAS | Recommendation |
|---|---|---|---|---|---|---|---|
| <category-id-A> (Auto Category) | Category | 20 | USD 16.00 | 4 | USD 160.00 | 10.00 | **Harvest → Manual exact** — high ROAS, isolate bid |
| earbds (typo) | Keyword (typo) | 10 | USD 8.00 | 0 | 0 | 0 | **Negate Exact** — 0 orders, drains spend |
| airbuds (typo) | Keyword (typo) | 5 | USD 4.00 | 0 | 0 | 0 | **Negate Exact** |
| <category-id-B> (Auto Category) | Category | 8 | USD 6.40 | 2 | USD 80.00 | 12.50 | **Harvest → Manual exact** |

---

## 汇总建议（按优先级）

| # | Platform | Campaign | Action | Expected Impact |
|---|---|---|---|---|
| 1 | Amazon US | A11111111 | Trim workhorse bid −15 %, negate "cheap wireless earbuds" search term | Save ~USD 120/month + ACOS down |
| 2 | Noon US | C_DEMO0001 | Negate 2 typo queries, harvest 2 high-ROAS categories | Save ~USD 12/month + scale |

---

## TSV records

每个 active campaign 一个 TSV，commit 到 git；下次审计读这些文件做 diff，
标记 OBSERVED_DRIFT。仅在末尾列一次路径：

- `stores/<slug>/ads/amazon/us/A11111111.tsv` (20 rows)
- `stores/<slug>/ads/noon/us/C_DEMO0001.tsv` (6 rows)
```

## Mandatory components — what the reviewer checks

The `ads-format-review` reviewer subagent fails the audit on any
of these:

| # | Required component | Failure example |
|---|---|---|
| 1 | Threshold preamble with margin %, breakeven, target, 25 % cap rule, PROTECT rule | Missing "Breakeven ROAS = X" line |
| 2 | Per active campaign: `### <id> — <name>` header | Campaign mentioned in summary table but no per-campaign section |
| 3 | Per active campaign: Top-tile line with spend / sales / orders / ROAS / ACOS | Section has only diagnosis prose, no numbers line |
| 4 | Per active Amazon campaign: `#### Targeting` table with Recommendation column | Recommendation column missing |
| 5 | Per active Amazon campaign: `#### Search Terms` table with Recommendation column | Search Terms table missing |
| 6 | Per active noon campaign: `#### Targets` table with Recommendation column | Targets table missing |
| 7 | Per active noon campaign: `#### Customer Queries` table with Recommendation column | Customer Queries table missing |
| 8 | Every Recommendation cell non-empty | Empty Recommendation cells |
| 9 | Every Trim recommendation: `−P %` where P ≤ 25 | `Trim to X (−33 %)` exceeds cap |
| 10 | Every Raise recommendation: `+P %` where P ≤ 25 | `Raise to X (+30 %)` exceeds cap |
| 11 | 汇总建议 priority table exists with ≥ 1 row | Section missing |
| 12 | TSV records block at the end with one entry per active campaign | Block missing or incomplete |
| 13 | No invented jargon (e.g., `机械状态`, `precedence matrix`, `bidding strategy precedence`) | Any of those terms present |
| 14 | **No placeholder rows on active campaigns.** Every Targeting / Search Terms / Targets / Customer Queries table for an active campaign must show real data rows, NOT excuses like *"drill skipped this cycle"*, *"small campaign — skipped"*, *"virtualized grid — per-row extraction blocked"*, *"data not captured"*. Small spend is not a skip reason; React-Virtualized grids extract row by row via scroll-and-eval. The only acceptable empty case is *"0 search terms in this period"* with a concrete count of 0. | A row that reads "Search terms drill skipped this cycle (small campaign, USD 30 spend)" — defect |
| 15 | **Pause recommendations include alternatives, each row backed by a verifiable evidence file.** See § "Rule 15 — Alternatives source structure" below for the full source list, evidence file paths, and "honest empty" acceptance criterion. **Fabricated rows (citing data the agent did not actually capture) are the worst class of defect — they look authoritative but mislead.** A Pause without Alternatives is also a defect. | `Amazon Brand Analytics (<country>) | "phone stand 24" | category result` — fabricated row (WIDGET-A is an internal SKU code, not a customer query) — defect |
| 16 | **SB-Video / virtualized-grid campaigns drilled like everyone else.** SB-Video (Sponsored Brands Video) uses React-Virtualized for its keyword grid; the agent must extract row-by-row by scrolling the grid, not give up with "extraction blocked". The Targeting table for an SB-Video campaign must have real keyword rows. Excuses like "React Virtualized grid — per-row extraction blocked" fail this rule. See `mechanics.md § virtualized-grid extraction`. | A row reading "13 keywords (React Virtualized grid — per-row extraction blocked)" — defect |

## Rule 15 — Alternatives source structure

Every row in a `#### Alternatives` subsection must cite **one of
three permitted sources**, and the cited evidence file must exist
on disk with content that matches the cell text. The Reviewer
mechanically verifies each row.

### Source A — Cross-platform same-SKU TSV

Evidence file: `stores/<slug>/ads/<other-platform>/<country>/<id>.tsv`

Used when the same product (same ASIN / same SKU family) has a
campaign on the OTHER platform that's converting well. The row's
Evidence cell cites the exact file path; the row's Keyword cell
cites a keyword that appears as a row in that file; the row's
Evidence cell may quote the ROAS / spend from that source row.

Reviewer checks: file exists; cited keyword appears as a row in
the file; ROAS in the Evidence cell matches the file's row.

### Source B — Same-platform other-campaign TSV

Evidence file: `stores/<slug>/ads/<platform>/<country>/<other-id>.tsv`

Used when another campaign in the same store/platform targets the
same SKU and has a healthy keyword the paused campaign could
mirror. Same evidence shape as Source A.

### Source C — Amazon Brand Analytics ASIN-keyword report

Evidence file:
`stores/<slug>/ads/brand-analytics/<ASIN>_<YYYY-MM-DD>.tsv`

Used when the agent has actually opened
`sellercentral.amazon.<tld>/brand-analytics/` (the **Top Search
Terms** report, ASIN tab — NOT the legacy `brandanalytics.amazon`
host, which 404s), filtered to the SKU's specific ASIN (not the
category), and captured the result to the file path above. **Category-broad queries are
forbidden** — they produce terms like *"usb cable"* or *"phone
accessories"* that are too generic to act on without ASIN-level
evidence; rows citing such terms get rejected even with a capture
file, because the file's filtered-ASIN column won't match.

See `mechanics.md § 8c — Brand Analytics ASIN-keyword report
capture` for the click path. The capture step is non-optional; the
agent cannot cite Brand Analytics without producing the file this
session (or reusing a fresh one — < 7 days — from a prior audit).

Reviewer checks: file exists at the cited path; first row's
filtered-ASIN matches the ASIN cited in the Evidence cell; cited
keywords appear as rows in the file.

### Readable evidence references — names first, paths second

Raw file paths like `stores/acme-store/ads/noon/<country>/C_FAKE0002.tsv`
mean nothing to the reader at a glance. Every Evidence cell
opens with a **readable reference name** (platform + country +
campaign name + canonical ID), then the file path in parens for
the reviewer to verify against, then the cited keyword and row
data:

```
Noon EG — Brand-X manual keyword (C_FAKE0002):
"matte phone stand" Exact, ROAS 18.73, 3 orders
[file: stores/acme-store/ads/noon/<country>/C_FAKE0002.tsv row 5]
```

vs the unreadable form (rejected by reviewer):

```
stores/acme-store/ads/noon/<country>/C_FAKE0002.tsv row: ROAS 18.73, 3 orders
```

The shape is consistent across all three sources:

- **Source A (cross-platform)**:
  `<Other-Platform> <Country> — <Campaign Name> (<id>): "<keyword>" <match>, ROAS <X>, <orders> orders [file: <path> row <N>]`
- **Source B (same-platform other-campaign)**:
  `<Platform> <Country> — <Campaign Name> (<id>): "<keyword>" <match>, ROAS <X>, <orders> orders [file: <path> row <N>]`
- **Source C (Brand Analytics ASIN report)**:
  `Amazon <Country> — Brand Analytics ASIN report for <SKU-name> (ASIN <BXX...>): "<query>", rank <R>, click_share <X%> [file: <path> row <N>]`

Reviewer reads BOTH:
- Readable name → sanity check (does the platform/country/campaign actually exist? does the SKU name match the ASIN?)
- File path → mechanical verification (open the cited file, find the row, match keyword + ROAS)

### Two ways the Alternatives section passes

**(A) ≥ 3 verified rows** drawn from any combination of Sources
A / B / C. Each row's evidence file exists and contains the cited
keyword(s).

**(B) "Searched, none found" block** — only when all three sources
were genuinely empty. The block must prove the searches happened:

```markdown
#### Alternatives

> Searched all sources — none returned candidates.

| Source | Evidence | Result |
|---|---|---|
| Cross-platform same-SKU | `stores/<slug>/ads/noon/<country>/` — no campaign targets ASIN B0XXXXXXXX (single-platform store) | 0 keywords |
| Same-platform other-campaign | `stores/<slug>/ads/amazon/<country>/` — no other campaign targets ASIN B0XXXXXXXX | 0 keywords |
| Brand Analytics ASIN-keyword | `stores/<slug>/ads/brand-analytics/B0XXXXXXXX_2026-05-23.tsv` (captured this session) | 0 rows returned for this ASIN |
```

Reviewer verifies:
- Cross-platform: cited directory does NOT contain any TSV with a row matching the ASIN.
- Same-platform other: same check against the cited directory.
- Brand Analytics: the capture file exists; the ASIN filter matches; the data row count is 0.

If any of those checks fails (e.g., the directory does have a TSV with the ASIN; the BA file doesn't exist or has rows), the section fails Rule 15.

The honest-empty case is honored for single-platform stores,
brand-new SKUs with no other-campaign coverage, and genuinely
narrow ASINs that don't appear in Brand Analytics. It is NOT a
shortcut for "I didn't feel like searching" — the proof files
must actually be on disk.

## Pause-with-Alternatives example

When a campaign or major segment is recommended for Pause, the
section MUST include alternatives. Bare "Pause and reallocate"
is not actionable — the merchant has a product they want to
promote; "reallocate" without a target is the agent shifting
the planning burden back to the human.

Concrete shape:

```markdown
### A22222222 — Earbuds brand-video US ⚠ NOT CONVERTING
**SB-Video** | 预算 USD 15/天 | Fixed bidding
**Top-tile**: spend USD 90 | sales USD 30 | orders 1 | ACOS 300% | ROAS 0.33

#### Targeting

| Keyword | Match | Status | Bid | Suggested | Clicks | Spend | Orders | Sales | ACOS | ROAS | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wireless earbuds | Broad | Delivering | USD 1.50 | 0.40–0.70 | 50 | USD 75 | 1 | USD 30 | 250% | 0.40 | **Pause** — 0.40 ROAS, video isn't earning the click-through |
| airpods alternative | Phrase | Delivering | USD 1.20 | 0.50–0.80 | 10 | USD 15 | 0 | 0 | — | — | **Pause** — 0 orders / USD 15 spend |
| (11 other keywords) | — | Delivering | various | — | — | — | — | — | — | — | **Pause** — all under-performing same way |

#### Alternatives — promote this product via instead

| Source | Keyword / Target | Evidence (readable name + file path + row) | Suggested action |
|---|---|---|---|
| Source A — cross-platform | `<category-id-A>` (Auto Category) | **Noon US — Earbuds Auto (C_DEMO0001)**: ROAS 10.00, 4 orders [file: `stores/<slug>/ads/noon/us/C_DEMO0001.tsv` row 5] | **Harvest to new Amazon SP-Manual exact keyword**, bid USD 0.60 (inside suggested range) |
| Source B — same-platform other-campaign | "earbuds for running" Exact | **Amazon US — wireless earbuds manual (A11111111)**: ROAS 5.33, 1 order [file: `stores/<slug>/ads/amazon/us/A11111111.tsv` row 4] | **Mirror keyword into a new SP-Manual**, bid USD 0.80 |
| Source C — Brand Analytics ASIN report | "bluetooth headphones noise cancelling" | **Amazon US — Brand Analytics ASIN report for Earbuds Pro (ASIN B0EXAMPLE7)**: rank 12, click_share 4.2% [file: `stores/<slug>/ads/brand-analytics/B0EXAMPLE7_2026-05-23.tsv` row 12] | **Add as Exact to new SP-Manual**, bid USD 0.90 (Brand Analytics search-volume baseline) |
```

Every Evidence cell opens with a **readable reference name**, then
the file path + row number in brackets for verification. The
reviewer opens each cited file and verifies the keyword and metric
actually appear there.

**Forbidden patterns** (the reviewer rejects):

| Anti-pattern | Why it fails |
|---|---|
| `Source: Brand Analytics; Evidence: "Top in <category>"` | No file path cited; reviewer has nothing to verify; likely fabricated |
| `Source: Brand Analytics; Evidence: "<file_path>"; Keyword: "widget a"` | "widget a" is the internal SKU code (WIDGET-A), not a customer search term — category-broad / SKU-derived hallucination |
| `Source: cross-platform; Evidence: "noon does well"` | No specific file or keyword cited |
| `Source: cross-platform; Evidence: "stores/.../noon/.../X.tsv row: ROAS X"` (path only, no readable name) | Hard to read; agent must lead with platform + country + campaign name + id |
| `Source: cross-platform; Evidence: "stores/.../noon/.../X.tsv"; Keyword: "Y"` where Y is not actually in X.tsv | Reviewer reads the file, doesn't find Y → fail |

## Bid-change edge cases

| Situation | Recommendation cell text |
|---|---|
| Current bid already inside suggested range | `Hold — bid inside suggested range` |
| Current bid below suggested range AND ROAS ≥ target | `Raise to <X> (+<≤25> %)` |
| Current bid above suggested range AND ROAS < target | `Trim to <X> (−<≤25> %)` |
| Current bid above suggested AND PROTECT (efficiency or workhorse) | `Hold (PROTECT)` — no change despite drift |
| 0 clicks, 0 spend | `Hold — no traffic yet` |
| 0 orders, spend ≥ 1.5 × AOV | `Pause — bleeder` |
| ROAS > 1.5 × target with > 50 % top-of-search impression share | `Hold — already winning placement` |
| ROAS > 1.5 × target with low TOS impression share | `Raise to <X> (+<≤25> %) — under-bid winner` |

## Phase 4 — Executing the audit on the live console

After the audit has passed the Phase 3 reviewer (`REVIEW_*_iter*.md`
Status: ok) and the user has instructed the agent to proceed, the
agent applies every actionable Recommendation row to the live
Amazon / Noon console. Phase 4 is gated by its own reviewer loop —
`EXEC_REVIEW_*_iter*.md` — exactly mirroring the Phase 3 design.

The agent does NOT just batch-edit and claim success. It works one
row at a time with **per-action live verification** and only marks
an action ``applied`` after the page reflects the new value.

### Phase 4 workflow (concrete steps)

1. **Create `EXECUTION_LOG.md`** in the task workspace with the
   header below. The Stop-hook gate fires the moment this file
   exists — non-execution tasks (audit-only) never touch it.

2. **For every actionable Recommendation row** in the audit:
   - Open a `TaskList` item with the action verb + campaign id +
     keyword/target so progress is visible mid-run.
   - Navigate to the campaign on the live console.
   - Apply the edit: trim/raise bid, pause keyword, negate search
     term, harvest to new campaign, etc.
   - **Read the field back from the page** to confirm the new
     value. The live re-read is the verification — agent claims of
     "applied" without a re-read are gaps under Rule E1 below.
   - Update the corresponding row in the per-campaign TSV (new bid,
     new status, ISO `applied_at`).
   - Append a row to `EXECUTION_LOG.md` with status `applied`.
   - Mark the `TaskList` item completed.

3. **Hold (no-op) rows** stay OUT of `EXECUTION_LOG.md`. The audit
   already records the decision; logging a Hold as ``applied`` adds
   noise without verifying anything.

### Harvest preflight — reactivate before re-adding

Amazon's keyword-add API enforces a duplicate-detection policy that
rejects new keywords whose token set near-matches an existing keyword
in the same campaign — **even when the existing variant is Paused**.
The rejection comes back as "X of Y keywords failed to add" with no
per-keyword reason, so the agent cannot tell whether the duplicate
match was exact or fuzzy.

Before clicking "Add keywords" / "Save" for a Harvest action,
**preflight against ALL same-platform same-country TSVs for this
SKU's manual campaigns** — not just the immediate target ad group:

1. Find the manual campaigns covering this SKU in
   `stores/<slug>/ads/<platform>/<country>/`. The audit's source
   table usually names them; otherwise list directory and read each
   TSV's product-ad row to find the SKU match.
2. For each keyword you intend to harvest, grep ALL these TSVs for
   the keyword and its obvious variants (token reorderings, plural/
   singular, prefix/suffix). Examples:
   - `braided usb-c cable` matches `usb-c cable braided`,
     `2m braided usb-c cable`, `braided usb-c cable for phone`.
   - `phone stand` matches `stand phone`, `aluminum phone stand`.
3. **Cross-campaign Delivering variant exists** anywhere in the
   sweep → skip harvest as `already_present` with Notes citing the
   other campaign: "Variant '<X>' already Delivering at bid <Y> in
   campaign <other-id>; harvest goal met without modifying target".
   The audit's recommended target ad group may have been chosen
   without knowledge that another campaign already runs the term —
   that's not a defect, just a no-op.
4. **Target campaign Paused variant** (no Delivering elsewhere) →
   reactivate that row (toggle to Delivering, update bid to harvest
   target). Verify by reading the bid back. Log row Notes:
   "Reactivated existing paused variant '<X>' at bid <Y>; harvest
   target satisfied without API duplicate-rejection."
5. **No variant anywhere in the sweep** → modal add will proceed
   normally. (If Amazon still rejects with "X of Y failed", that's
   the rare genuine account-level / brand restriction case worth
   surfacing.)

This rule turns ~15 % of Harvest attempts from `failed` /
API-rejected into clean `applied` reactivations. The audit's source
ROAS data (e.g. `braided usb-c cable` ROAS 18.6 from the auto
campaign) is still actioned — just via the variant that already
exists in the manual campaign, not by adding a duplicate.

4. **After every actionable row is processed**, spawn the
   `ads-execution-review` subagent per `reviewer-loop.md §
   Execution-review mode`. The reviewer reads:
   - `AD_AUDIT_<date>.md` — the source of recommendations.
   - `EXECUTION_LOG.md` — what the agent claims to have done.
   - The per-campaign TSV files — the post-execution state on disk.

   It cross-checks: every actionable Recommendation appears in the
   log, every `applied` row has a TSV row matching the target
   value, every `failed` row has a retry or explicit note. It
   writes `EXEC_REVIEW_<date>_iter<N>.md` with `Status: ok | gaps |
   incomplete`. The Stop-hook reads the latest iter; same loop
   semantics as Phase 3 (max 5 iters; `incomplete` at iter 5 is
   terminal).

5. **Only after `EXEC_REVIEW_*_iter*.md Status: ok`** may the agent
   call `vibe_seller_set_task_result`. The hook denies stop
   otherwise.

### `EXECUTION_LOG.md` shape

```markdown
# Execution Log — <YYYY-MM-DD>

Audit file: ./AD_AUDIT_<date>.md
Started: <ISO timestamp>
Reviewer-loop gate: EXEC_REVIEW_<date>_iter<N>.md

## Actions

| # | Platform | Country | Campaign | Action | Target value | Status | Verified at | Verification | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Amazon | US | A11111111 — wireless earbuds manual | Trim "wireless earbuds" Broad bid | USD 1.70 (was 2.00, −15 %) | applied | 2026-05-24T12:30:00Z | page bid field shows 1.70; TSV row 1 bid=1.70, applied_at=2026-05-24T12:30:00Z | workhorse soft-trim cap |
| 2 | Amazon | US | A11111111 | Negate "cheap wireless earbuds" search term as Exact | negative-keyword added | applied | 2026-05-24T12:33:00Z | negative-keywords list shows new row "cheap wireless earbuds" Exact; TSV negative-keywords block updated | drains workhorse, 60 clicks 0 orders |
| 3 | Noon | US | C_DEMO0001 — earbuds auto | Harvest "<category-id-A>" to new SP-Manual exact keyword | new keyword USD 0.60 | failed | 2026-05-24T12:40:00Z | "create keyword" button disabled — campaign type doesn't support manual conversion | retry: create new SP-Manual-Keyword campaign instead |
| 4 | Noon | US | C_DEMO0001 | Harvest "<category-id-A>" via NEW SP-Manual-Keyword campaign | new campaign created, keyword USD 0.60 | applied | 2026-05-24T12:48:00Z | new campaign in dashboard; keyword field shows 0.60; TSV `stores/<slug>/ads/noon/us/C_NEW0001.tsv` created | retry succeeded |

Total actionable recommendations: N
Applied: M
Failed (unresolved): K
Skipped with reason: J (Hold rows are not logged)
```

Every `Status: applied` row MUST include the **Verification cell**
proving the live read-back happened (e.g., "page bid field shows
1.70"). A row with `applied` status but an empty Verification cell
is a gap under Rule E1 — the reviewer treats it as an unverified
claim.

### Mandatory execution components — what the EXEC reviewer checks

| # | Required component | Failure example |
|---|---|---|
| E1 | Every actionable Recommendation row in the audit has a corresponding row in `EXECUTION_LOG.md`. Hold rows excluded. | `A11111111 "wireless earbuds" Trim` in audit but no row in EXECUTION_LOG → MISSING ACTION |
| E2 | Every `applied` row in `EXECUTION_LOG.md` has a non-empty Verification cell quoting a live read-back. | Row shows `applied` but Verification cell is empty → UNVERIFIED CLAIM |
| E3 | For every `applied` Trim/Raise: the campaign's TSV row's bid column shows the target value (rounding tolerance ±0.01) AND `applied_at` is set to the ISO timestamp. | TSV bid still shows old value → INCORRECT APPLICATION |
| E4 | For every `applied` Pause keyword: TSV row's status column shows "Paused". For every `applied` Pause campaign: ALL targeting rows in that campaign's TSV show "Paused". | TSV row still shows "Delivering" → INCORRECT APPLICATION |
| E5 | For every `applied` Negate: a row exists in the campaign's TSV negative-keywords block matching the negated term + match type. | No new negative-keyword row → MISSING ARTIFACT |
| E6 | For every `applied` Harvest: a new keyword row exists in the target campaign's TSV (existing or newly created). Newly created campaigns get a new TSV file under `stores/<slug>/ads/<platform>/<country>/<new_id>.tsv`. | Harvest claimed but no new keyword row anywhere → MISSING ARTIFACT |
| E7 | No `failed` row left unresolved. Each must either have a follow-up `applied` retry row OR an explicit Notes cell explaining why it cannot be applied (e.g., "campaign type doesn't support this action; deferred to next audit"). | `failed` row with empty Notes and no retry → UNRESOLVED FAILURE |
| E8 | The Action checklist in the audit's 汇总建议 priority table accounts for every actionable recommendation in `EXECUTION_LOG.md`. Reviewer cross-references. | 汇总建议 has 8 items; EXECUTION_LOG has 22 → either the priority table is incomplete, or the agent over-executed beyond what the user approved |

## Where this anchor is read

- The **main task agent** loads this file at end of Phase 2,
  uses the example to structure its Phase 3 output.
- The **reviewer subagent** loads this file to know what to
  check against.
- The **Stop-hook review gate** doesn't read this file — it
  reads the reviewer's output (`REVIEW_*_iter*.md`). The
  anchor's only job is to be the canonical source of truth that
  agent + reviewer both read.

If the anchor changes, every active store's next audit will
follow the new shape; no migration needed because each audit is
written fresh from this template.
