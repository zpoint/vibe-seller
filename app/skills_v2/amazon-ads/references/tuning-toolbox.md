# Tuning toolbox — the levers

Every recommendation uses one of nine standard levers (1–9) or, when the
user opts in, one of two advanced ones (10–11). This page says what each
lever touches, where it lives in the UI, and when it fits. The rules
that bound a lever — PROTECT, the bid floor and step cap, scale winners,
cooldown, search-term actions — are in
[`tuning-recommendation-format.md`](tuning-recommendation-format.md);
cutoffs in [`tuning-thresholds.md`](tuning-thresholds.md). Refer to
levers by these numbers from other files.

- [Scope: surgical to blanket](#scope-surgical-to-blanket)
- [Cut waste: 1–4](#cut-waste)
- [Manage bids: 5–7](#manage-bids)
- [Grow what works: 8](#grow-what-works)
- [Budget: 9](#budget)
- [Advanced, off by default: 10–11](#advanced-off-by-default)
- [Combination patterns](#combination-patterns)

## Scope: surgical to blanket

| Scope | Levers | What it touches |
|---|---|---|
| One query | 1 (negative keyword), 4 (negative ASIN) | that query; other keywords unaffected |
| One target | 2 (pause), 3 (lower bid), 5 (raise bid) | one keyword or target in one ad group |
| One query promoted | 8 (harvest to exact + negate in source) | adds a keyword, adds a negative |
| One placement | 6 (placement modifier, increase-only) | biases bidding toward a placement |
| Whole ad group | default bid (3 / 5 generalised) | every target in the group |
| Whole campaign | 9 (budget), 7 (bidding strategy), pause / archive | everything in the campaign |

Reach for the most surgical lever that resolves the diagnosis. Surgical
changes are reversible with one click and easy to attribute; blanket
ones ripple through the campaign, so when metrics move next week nobody
can say which change did it.

## Cut waste

### 1. Negative exact / negative phrase keyword

- **What**: stop the campaign showing for a customer query.
- **Where**: Search terms tab → row → Add as ⌄ → `Add as negative exact`
  / `Add as negative phrase`; or Negative targeting → Negative keywords →
  Add. Many at once: `ads_bulk.py negate` ([`bulk-operations.md`](bulk-operations.md)).
- **When**: the term passes the waste cutoff with 0 orders across all its
  rows; or several irrelevant terms share a word → phrase-negate the word.
- **Match types**: exact, phrase. Amazon SP has no negative broad.
- **Watch for**: a negative also blocks relevant variants — check which
  other terms it would match before adding.

### 2. Pause a keyword / target

- **Where**: Targeting tab → row → Active toggle (blue on, grey off).
- **When**: about 14 days of data, losing money, no order signal, not
  PROTECT; or a trim is impossible because the floor is at or above the
  current bid.
- Reversible. Many paused keywords clutter a campaign; archive them now
  and then.

### 3. Lower a target's bid

- **Where**: Targeting tab → row → bid cell (`kat-numberinput`) → type →
  Save. Many at once: `ads_bulk.py bid-update --from-changes CHANGES.tsv`.
- **When**: the row converts but at ACOS ≥ `acos_no_lower`; worth keeping
  at a lower bid.
- **Bounds**: floor and step cap per
  `tuning-recommendation-format.md` § Bid floor and step cap; at least
  the marketplace minimum bid (read it from the campaign — it depends on
  the currency).
- **Watch for**: rule-based and dynamic bidding can move the bid back;
  read the cell again after a few minutes.

### 4. Negative ASIN / product target

- **Where**: Negative targeting → Negative products → Add → ASIN list.
- **When**: the Product pages placement spends with low CTR or CVR —
  the ad is showing on unrelated detail pages. Find the ASINs in the
  search terms (rows that are `B0…`).
- **Watch for**: blocking many ASINs cuts reach; start with the obvious
  mismatches.

## Manage bids

### 5. Raise a target's bid

- **Where**: same cell as lever 3.
- **When**: a scale winner (`tuning-recommendation-format.md` § Scale
  winners), especially with low top-of-search impression share — it wins
  when it shows but doesn't show enough.
- **Bounds**: step cap per session; step across audits to go further.

### 6. Placement bid modifier (Top of search / Rest of search / Product pages)

- **Where**: Campaign → Bid adjustments → % cell → value → Save.
- **Range**: 0% to +900%, increase-only (Amazon's own popup: "Choose a
  percentage between 0 and 900%"). It cannot suppress a placement — for
  that use levers 1, 3, 4 or 7.
- **When**: a placement converts at or above target ROAS with clearly
  higher CTR than the others, and Amazon shows a `Recommended: X%` bump —
  apply up to about twice the recommendation.

### 7. Bidding strategy

- **Where**: Campaign settings → Campaign bidding strategy.
- **Options**: Fixed bids; Dynamic bids – down only (Amazon lowers when a
  conversion looks unlikely; conservative); Dynamic bids – up and down
  (up to ±100%; aggressive); Rule-based (pursues a target ROAS; needs
  enough conversion data to learn).
- **When to switch**: rule-based missing its target → lower the target or
  switch to down-only; down-only on a proven winner → consider up and
  down; fixed on a volatile campaign → consider dynamic. Don't open with
  a strategy change when a target-level lever fixes the problem.

## Grow what works

### 8. Harvest a search term to exact

- **What**: promote a converting term to its own exact keyword, and add
  it as a negative exact in the source so the two don't bid against each
  other. When to do it at all: `tuning-recommendation-format.md`
  § Search-term actions.
- **Manual campaigns**: Search terms → row → Add as ⌄ → `Add as keyword`
  → Exact only (adds to the same ad group); then the same row → `Add as
  negative exact`.
- **Auto campaigns**: auto ad groups can't hold keywords. Add the keyword
  to a harvest ad group in a manual campaign for the same SKU (name it
  `<campaign-name-slug>-harvest-kw`; reuse it if it exists), and add the
  negative exact at the auto campaign's campaign level. Click path:
  `mechanics-edit.md` §6.
- **Bid**: the new exact keyword starts cold; begin near the term's CPC ×
  1.1–1.25 or the suggested midpoint, not the broad bid that found it.
- Before adding, run the harvest preflight in
  [`execution.md`](execution.md) § Harvest preflight.

## Budget

### 9. Daily budget

- **Where**: Campaign settings → Budget (`kat-numberinput`) → Save.
- **Raise when**: status shows `Out of budget`, the campaign is
  profitable, and its targets have already been tuned (ACOS at target) —
  tune before you scale. Use Amazon's `Recommended` value if shown, else
  about +30%. The bid step cap doesn't apply to budgets.
- **Lower when**: you want to limit a campaign's exposure without pausing
  it — it reduces spend but doesn't fix ACOS; fixing the targets is
  usually better.
- **Note**: daily spend of 1.0–1.25 × budget is normal (Amazon balances
  overspend over the month); spend equal to budget doesn't by itself
  mean throttled.

## Advanced, off by default

### 10. Dayparting / schedule rules

Campaign settings → Schedule rules. Use only when the user asks and the
store has the data to see an hourly or weekday pattern (≥ 30 days,
≥ 50 orders); bid and search-term tuning usually matters more and
dayparting is hard to evaluate.

### 11. Structural splits

Moving a high-spend keyword into its own campaign for budget control or
testing. Recommend it; the user usually does it faster in the UI.

## Combination patterns

**Throttled but inefficient** (Out of budget, ACOS above target): levers
1 + 8 on the search terms, then 3 on the poor rows; wait 7–14 days;
re-measure. Raise the budget (9) only once ACOS is at target.

**Placement imbalance** (one placement carries the orders, another
wastes spend): modifiers can't suppress, so: Dynamic down-only (7) if on
fixed bids; negative ASINs (4) for product-page waste; negatives (1) on
the queries that route to Rest of search; a positive modifier (6) on the
placement that converts.

**Funnel broken** (CTR or CVR well below the store median): not an ad
problem. Tell the user it is the listing (image, title, A+, price,
reviews) and that lowering bids only reduces spend. Pausing while the
listing is fixed is a fair interim step; a bid trim presented as the fix
is not.

**Winner needing fuel** (a scale winner — ROAS > `scale_roas` — with
low impression share and healthy CTR): raise bids (5), add a modifier
on the converting placement (6), raise the budget if constrained (9),
harvest related converting terms (8).
