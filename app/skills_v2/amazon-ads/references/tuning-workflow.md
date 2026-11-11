# Reading and diagnosing a campaign

What to read on each campaign and how to turn it into a diagnosis, plus
how to handle the user's replies to an audit. The order of work is in
[`audit-quickref.md`](audit-quickref.md); the decision rules in
[`tuning-recommendation-format.md`](tuning-recommendation-format.md);
click paths and selectors in [`mechanics.md`](mechanics.md).

- [Before you start: inputs and defaults](#before-you-start-inputs-and-defaults)
- [Campaign states](#campaign-states)
- [Each campaign is its own drill](#each-campaign-is-its-own-drill)
- [Per-campaign reading](#per-campaign-reading)
- [Follow-up replies](#follow-up-replies)

## Before you start: inputs and defaults

An audit has a default for everything it needs, so start drilling
without pre-flight questions and state the assumptions in the report —
that gives the user one round trip to correct them instead of three
questions up front.

- **Window**: last 30 days. Ask only when the phrasing is genuinely
  ambiguous and the readings would change the report (e.g. "last month":
  calendar month or trailing 30 days?).
- **Margin**: from `stores/<slug>/notes.md`, else the default
  (`tuning-recommendation-format.md` § Missing margin).
- **Goal**: apply the standard playbook — negate waste, harvest
  converters, correct drifted bids, protect order drivers, flag
  listing-side funnel breaks. Launch / scale / profit rarely changes the
  recommendations; the target-ACOS formulas are in
  [`tuning-thresholds.md`](tuning-thresholds.md).
- **Markets and campaign types**: your declaration sets the markets;
  audit every campaign type the store runs.

## Campaign states

Classify every campaign from what the console shows, not from how
interesting it looks; the state decides `drill_status` in
`CAMPAIGNS.tsv`.

| State | Trigger | Drill? |
|---|---|---|
| live | Delivering / Out of budget (noon: Live) with clicks in the window | yes |
| paused, archived, ended | console status says so | no (`inactive`); optional if it spent in the window |
| no impressions | delivering but 0 impressions in the window | no (`inactive`, say so in `note`) |
| too new | created within max(window / 4, 7 days) and < 10 clicks | drill it, and say the data is thin |
| unreadable | the page or export failed after retries | `blocked`, with the literal error in `note` |

A status you don't recognise is `blocked` with that status quoted — don't
guess a classification. "Looks healthy from the overview" is not a
state: idle keywords burning budget and one query bleeding a third of the
spend hide inside healthy totals, and only the drill shows them.

## Each campaign is its own drill

The drill of campaign A is data about A only. Two campaigns with the same
SKU, name pattern, type and budget can have entirely different keywords,
queries and bids. Recommendations extrapolated from A would name
entities in B you never read, with bids you never saw — that is
fabrication, however plausible it looks. If a campaign seems to "look
like" one you already drilled, open it and read it.

Phrases that signal a skipped drill — "enough data", "pattern
inference", "same as #3", "drill recommended", "to be analysed later",
"top-tile only" — mean the campaign isn't ready to be in the report yet.
A long audit is supposed to be long: the unit of completeness is the
list of live campaigns, not a sense of pacing.

## Per-campaign reading

1. **Confirm the type** from the Type pill and load its row in
   [`tuning-campaign-types.md`](tuning-campaign-types.md) — tabs, columns,
   and which levers apply differ by type.
2. **Targeting tab** — every target row. Per row: ROAS vs `target_roas`,
   bid vs Amazon's suggested range (a bid > 1.5× or < 0.5× the suggested
   midpoint has drifted), and whether a poor row has enough data (about
   14 days, a few clicks per expected order) to act on.
3. **Search terms** — where most negate and harvest decisions live.
   Prefer the export (`audit-quickref.md` §4). If you must read the page:
   the search-term column is sticky, so sorting a metric column reorders
   the metrics but not the terms; read cells from each `[role=row]` in
   DOM order rather than visually. The filter chips "Targets with
   conversions" (harvest set) and "Targets with clicks and no orders"
   (waste set) help; reading one ad group at a time keeps the table small
   enough not to virtualize.
4. **Bid adjustments / placements** — set this page's own date picker to
   the session window first, then check that the placements add up to the
   campaign total (`mechanics-reading.md` §8). Modifiers are 0% to
   +900%, increase-only, so they can favour a good placement but never
   suppress a bad one. A placement with real orders at high ACOS is
   refined (negate the queries that route there, trim bids, Dynamic-down
   strategy), not killed.
5. **Negative targeting** — existing negatives; for product-page waste,
   candidate negative ASINs (from `B0…` search terms).
6. **Campaign settings** — bidding strategy: rule-based with actual ROAS
   below its target means the rule is failing (lower the target or switch
   to Dynamic down-only); Dynamic down-only on a proven winner may deserve
   up-and-down; fixed bids on a volatile campaign may deserve dynamic.
   Consider a budget raise only after the campaign's ACOS is at target.
7. **Funnel check** ([`tuning-funnel-diagnosis.md`](tuning-funnel-diagnosis.md))
   — CTR and CVR against the store's medians. Low CTR (image, title) or
   low CVR (detail page, price, reviews) is a listing problem; bids
   cannot fix it, so say "not fixable from the ad side".
8. **Pick levers** ([`tuning-toolbox.md`](tuning-toolbox.md)), most
   surgical first: search-term negates and harvests, then per-target
   bids and pauses, then placement modifiers, then bidding strategy and
   budget. A blanket change is harder to attribute when metrics move.

Then write the campaign's two TSVs before opening the next one.

## Follow-up replies

After the report is delivered, the user's reply is one of:

| Reply | What to do |
|---|---|
| Approves rows ("do 1, 3", "all of campaign X", "execute the plan") | Declare `execute` and follow [`execution.md`](execution.md). |
| Corrects a value or a decision ("2 should be 0.95", "don't pause that one") | They named it: declare `change` scoped to that campaign, update its `CHANGES.tsv` rows and the report in place, and submit again. No new drill is owed — the audit's files are already there. |
| Asks to re-drill ("redo campaign X with 14 days") | Declare `audit` for those campaigns, re-drill only them, replace their TSVs and report sections. |
| Changes an assumption ("margin is 30%") | Record it in `stores/<slug>/notes.md`, recompute decisions from the TSVs on disk without re-reading the console, update `CHANGES.tsv` and the report. |
| Asks a question ("why did you flag X?") | Answer from the TSVs and report. |

Work on the existing report and files — never start a fresh audit in a
follow-up unless asked, because that overwrites what the user already
reviewed.

If answering a question shows the original decision was wrong ("you're
right, that trim was too steep"), the reply has become a correction:
fix the rows in `CHANGES.tsv` and the report and submit again, then tell
the user what changed in the file. Agreeing in chat while the file still
says the old thing leaves the next reader — or the execution phase —
acting on it.
