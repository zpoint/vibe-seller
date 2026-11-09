# Noon Ads — tuning audit

noon-specific judgement for an audit. The procedure is shared:
[`../../amazon-ads/references/audit-quickref.md`](../../amazon-ads/references/audit-quickref.md);
the files the server reads are in [`data-contract.md`](data-contract.md);
the report is
[`../../amazon-ads/references/output-spec.md`](../../amazon-ads/references/output-spec.md);
row-level rules (PROTECT, bid floor and step cap, missing margin,
search-term actions) are in
[`../../amazon-ads/references/tuning-recommendation-format.md`](../../amazon-ads/references/tuning-recommendation-format.md).
Click paths are in `../SKILL.md`. Analytical decisions — which lever,
which keyword, which bid, when to send a problem to the listing side —
are yours.

- [Scope](#scope)
- [What to read](#what-to-read)
- [Drill every live campaign](#drill-every-live-campaign)
- [Head terms vs modified terms](#head-terms-vs-modified-terms)
- [Category (Subcat) targets](#category-subcat-targets)
- [Other things worth checking](#other-things-worth-checking)
- [Report shape on noon](#report-shape-on-noon)
- [When the user replies](#when-the-user-replies)

## Scope

**Which countries you cover comes from your declaration.**
`./AUDIT_TARGETS.json` lists every marketplace the store is configured
for; it is a menu, and your `vibe_seller_declare_ad_task` call picks what
you owe. A whole-store declaration (no `combos`) owes every noon country
in it; a country outside your declaration is out of scope. Each `en-<cc>`
country is a separate auction, so keep countries in separate report
sections and never use one as the baseline for another.

**Live campaigns.** Drill campaigns whose status is `Live` or `Out of
budget` — both are spending. `Paused` and `Draft` campaigns are listed in
`CAMPAIGNS.tsv` as `inactive` and not drilled. A question about a paused
campaign ("should we reactivate X?") is its own section.

**"Empty" countries.** Don't record a country as having no campaigns
while `stores/<slug>/metadata.json` lists it under
`platform_countries.noon` (or `notes.md` mentions campaigns there) until
you have run the check in `../SKILL.md` § 2 — the Ad Manager sometimes
renders a transient empty state.

## What to read

**Catalog — what each advertised SKU really is**
(`noon-shared` § 3). For each SKU in a live campaign:

| Field | Where on My Catalog |
|---|---|
| Title (English + Arabic) | row title |
| Real category (gender, type) | title, or the edit page |
| Seller Status | toggle column |
| Live Status (`Offer Created` / `Unavailable` / `Buy Box Won` / `View Issues`) | row |
| Active Net Stock | inventory column |
| Performance (Views, Units Sold, Sales) | row |
| Rating count | public PDP — only when a listing-first diagnosis is on the table |

SKU codes and campaign names mislead; the catalog says what the ad
actually shows.

**Campaigns.** From the list: id, name, type (Auto / Manual), ad type,
status, daily budget, strategy, window KPIs (Spend, Revenue, ROAS, CTR,
Orders, Clicks, Views). These are your `CAMPAIGNS.tsv` rows.

## Drill every live campaign

For each live campaign open the detail page and capture:

- **Funnel KPIs** from the top tiles: Views, Clicks, ATC, Orders, Spend,
  Revenue, CTR, CvR.
- **Both layers from the Customer Queries `Export`** (`../SKILL.md` § 6):
  the `Targets` sheet is the targeting layer (manual keywords/categories,
  or an auto campaign's `auto keyword` / `auto category` groups — one row
  each, never a single campaign-total row), and the `Queries` sheet is
  the full search-term set. Write both per-campaign TSVs.
- **SOI** from the Targets tab DOM (`../SKILL.md` § 4) — the export does
  not carry it, and it is the best single signal of how much reach a
  target leaves unclaimed.

**Why every campaign.** A campaign that looks healthy from the overview
can still carry idle keywords at the bid floor or one query bleeding a
third of its spend to the wrong audience; the overview can't show
either. Drilling and finding nothing is fine. A verdict on a campaign
you didn't open is a guess.

If time is short, drill in this order, and still drill them all:

1. **Waste candidates** — ROAS at or below breakeven; most trims and
   negates live here.
2. **Big spenders above breakeven** — confirm they really are healthy.
3. **Expansion candidates** — highest ROAS regardless of spend. A
   high-ROAS campaign spending 2/day against a 15/day cap is often the
   best budget move in the portfolio, and only its query data can
   justify the raise. Low spend is not low priority.

A drill takes ~45–60 s per campaign plus the export wait. Past ~15 live
campaigns across countries, suggest splitting the work into one task per
country rather than racing.

**Vague actions mean the drill was skipped.** "Check Customer Queries
for negatives" or "trim keywords above recommended" asks the user to do
the work. Go capture the data and name the keyword, the current bid and
the target bid.

## Head terms vs modified terms

When a campaign has good ROAS but little volume, the reflex is "add
keywords" or "raise bids". First read **SOI** and split the targets:

- **Bare head term** — the category noun alone (`<category>`).
- **Modified term** — category plus a qualifier (`<category>
  <audience>`, `<attribute> <category>`).

Compare SOI and conversion between the groups in the same campaign at
comparable bids:

| Pattern | Reading | Action |
|---|---|---|
| Modified terms hold decent SOI; bare head term near 0% at a similar bid | The auction doesn't consider the listing a valid answer for the broad query — relevance, not price | Don't bid up the head term; grow the modified terms |
| Both groups low SOI | Underbid or budget-capped | Raise bids / budget, re-read SOI in 3 days |
| Head term has SOI but converts far below the modified terms | Buying unqualified traffic | Move spend to modified terms; negate the qualifiers you don't serve |

A bare category query mixes audiences, form factors and price tiers. A
listing serving one slice converts worse on it *and* gets shown less, so
bidding the head term harder pays more for traffic you convert worse.
Qualified variants often sit in the same `searches/month` bucket as the
bare noun ([`ads-keyword-research.md`](ads-keyword-research.md) § Step 1),
so check before concluding you need the head term for volume.

Head-term results don't transfer between stores: two stores in the same
category and country can see opposite results on the same keyword
because the query's dominant intent matches one and not the other. A
sibling store's winning setup is a hypothesis to test on this store's own
SOI and Customer Queries; when they disagree, this store's data wins.

## Category (Subcat) targets

Category targets appear as `<path> Category Match`. Before drawing a
lesson from one, record which **ad type** it ran on:

1. **Don't pool across ad types.** Brand and Product ads buy different
   placements; a Brand-Ad category ROAS doesn't forecast a Product-Ad
   category target. A store may have run category targets only on Brand
   Ads, in which case "category targeting works here" is unproven for
   Product Ads.
2. **Don't pool across campaigns of very different health.** One failing
   campaign can make category targeting look worthless store-wide; split
   by campaign and the picture can reverse.

When proposing one, state the depth (department vs leaf are not
comparable) and start with a bid low relative to the campaign's keyword
bids so the category can't drain the keyword budget before you can read
it.

## Other things worth checking

- **Keyword anomalies** (typos, sub-floor bids, literal duplicates) —
  when a clean form exists in the same campaign, prefer deleting the
  anomaly (editor) over pausing it, so later audits don't keep re-reading
  clutter.
- **Bid moves** — noon's suggested range for a target is in the editor
  (`campaign-editor.md` § The editor as a read-only research tool); read
  it before proposing a raise or a lower, then apply the floor and step
  cap from `tuning-recommendation-format.md`.
- **Cross-campaign overlap** — after reading every campaign in a country,
  look for the same keyword and match type in two or more campaigns. They
  bid against each other at the same auction; a trim has to be
  coordinated across all of them, since trimming one just hands the win
  to another.
- **Thresholds** come from the data contract's store rules; a store's
  `stores/<slug>/notes.md` may override them, and a standard the user
  states belongs there too.

## Report shape on noon

The shared report rules are in `output-spec.md`; on noon:

- **ROAS, not ACOS, in the human tables** — it is the console's metric.
  Read the currency from the campaign rather than assuming one.
- **Lead each campaign with a funnel snapshot** (Views → Clicks → ATC →
  Orders, with CTR, click→ATC and ATC→order rates, Spend, Revenue,
  ROAS). It shows at a glance whether a problem is the ad or the listing.
- **Targets table with SOI**, so the reader sees reach as well as
  efficiency. For Auto campaigns also show the settings (default bid,
  strategy).
- **Brand Ads** are compared only with Brand Ads.
- **Defaults line** under each country's summary: margin (see
  § Missing margin in `tuning-recommendation-format.md`), target ROAS,
  waste/harvest cutoffs, and which came from the store's notes.
- **An action checklist the user can answer by number** — nested by
  campaign, one sub-letter per change (`2a`, `2b`), numbered
  continuously across countries. Each sub-item names the keyword, query
  or budget value and corresponds to one `CHANGES.tsv` row, so a reply
  like `2a, 3 all` maps to rows without guessing. Things to watch over
  the next weeks are not checklist items; list them separately.

## When the user replies

A reply that picks checklist items is a new phase: declare `execute`
scoped to those campaigns and apply exactly the chosen `CHANGES.tsv` rows
([`applying-changes.md`](applying-changes.md)). Read the checklist from
the task's earlier messages rather than replanning. Parse `2a, 2c`,
`2 all`, or "1–3 don't, 4–7 do" as written; when a reply is genuinely
ambiguous, ask with `AskUserQuestion` rather than in prose. Finish with a
one-line summary of what was applied, what was skipped and why.
