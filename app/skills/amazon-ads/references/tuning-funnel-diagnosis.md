# Funnel diagnosis — distinguish listing problems from ad problems

The single most common tuning mistake is **treating a listing problem
as an ad problem**. Bid changes can't fix a bad image. Negatives can't
fix bad reviews. Before reaching for a tool, locate the leak in the
funnel.

## The funnel

```
                       Layer 1                Layer 2                Layer 3
[Customer query] → [Impressions] → [CTR] → [Clicks] → [CVR] → [Orders] → [ACOS / ROAS]
                       ↑                ↑                ↑              ↑
                   "did the ad     "did our         "did our         "is the
                    even show?"    image+title    PDP convert?"      campaign
                                   earn the                          profitable
                                   click?"                           overall?"
                       ↑                ↑                ↑              ↑
                   bid + match      LISTING         LISTING        ad-tuning
                   type levers      problem         problem        applies
                                  (ad can't fix)  (ad can't fix)
```

## Layer-by-layer diagnostic

### Layer 1 — Impressions

Is the ad even showing?

- **Low impressions despite normal bid + budget** → bid is below
  competitive, or keyword is too narrow, or campaign is brand-new
  (cold start, give it 7-14 days).
- **Normal impressions but ad-side levers can't move higher** → ad
  is at impression-share ceiling for the keyword. Consider broader
  match types or new keyword variants.

### Layer 2 — CTR (clicks ÷ impressions)

Is the listing earning the click?

- **Sponsored Products typical CTR ranges**: 0.3% to 1.5% for most
  categories; 2%+ is strong; below 0.2% is concerning.
- **CTR < store median × 0.5** → **listing-side problem, NOT an ad
  problem**. The image, title, price, or rating isn't competing.
  - Ad-side action is theater here. Lowering bid reduces spend but
    doesn't fix the leak.
  - **Recommended action**: surface to user — "this is a listing
    image / title problem; recommend update the listing rather
    than bid changes". Optionally pause the campaign as an interim
    stop-the-bleed measure.

- **CTR healthy on Top of search but terrible on Product pages** →
  the ad is being placed on irrelevant competitor PDPs. This is an
  ad-side problem (negative-ASIN targeting), not a listing problem.

### Layer 3 — CVR (orders ÷ clicks)

Is the PDP closing the sale?

- **Sponsored Products typical CVR**: 3-8% for most consumer
  products; lower for higher-AOV / considered purchases.
- **CVR < store median × 0.5** → **PDP / price / review problem,
  NOT an ad problem**. The customer clicked through but didn't
  buy. Reasons:
  - Price uncompetitive vs the listings shown alongside it
  - Bad reviews or low review count
  - PDP / A+ content doesn't address the question the click implied
  - Out of stock / shipping delay
  - Wrong product for the search intent (but the click was earned)

  **Ad-side action is theater here too.** Lowering bid keeps the
  same broken-CVR clicks coming. Pausing wholesale loses orders.

  **Recommended action**: surface to user — "this is a price /
  review / PDP problem. Recommend fix the listing-side issue
  before tuning the ad." Optionally lower bid to reduce spend
  while listing is being fixed.

## How to compute layer medians

For "store median" comparisons, the agent should pull medians across
all of the store's active campaigns:

```
ctr_median  = median(per-campaign CTR for active campaigns)
cvr_median  = median(orders / clicks for active campaigns where clicks > 50)
acos_median = median(ACOS for active campaigns where orders > 0)
```

Skip campaigns with too little data (< 50 clicks for CVR, < 1 order
for ACOS) when computing medians — they introduce noise.

## When ALL layers look healthy but ACOS is still high

If CTR ≥ median, CVR ≥ median, but ACOS > target:

This is the **ad-tuning applicable** case — the actual case where
bid trims, search-term harvesting, and placement modifiers are
appropriate. The funnel is healthy; the ad is just paying too much
per conversion.

Apply Phase 3 of the main skill (search terms, targeting, bid
adjustments, settings).

## When ALL layers look healthy AND ACOS is at target

This is a **cruiser** campaign. Don't tune. Monitor.

The temptation to "always optimize something" is real and wrong.
A campaign that's hitting target ACOS with healthy funnel is doing
its job; tinkering risks regression for marginal gain.

## Self-stabilized campaign — judge by active-only, not campaign-level

A campaign where **most rows are already in Paused state** is a
campaign that has already been pruned (by prior tuning or by Amazon's
own auction signals). The campaign-level top-tile ROAS / ACOS shown
in the UI is computed over the **entire window's history** —
including the spend / sales of rows that are now paused. That
top-tile number does NOT reflect the current state of the campaign.

**Diagnostic rule**:
- If `paused_row_count / total_row_count ≥ 0.5` (≥ 50% of keyword
  / target rows in Paused state), **do not use the campaign-level
  ROAS / ACOS as the diagnosis input**.
- Instead, recompute over **active rows only**:
  ```
  active_spend  = sum(spend  for row in rows if row.status == 'Delivering')
  active_sales  = sum(sales  for row in rows if row.status == 'Delivering')
  active_orders = sum(orders for row in rows if row.status == 'Delivering')
  active_roas   = active_sales / active_spend   # may be undefined if active_spend == 0
  ```
- The diagnosis (Layer 1 / 2 / 3 funnel call, "is this campaign
  bleeding") must be made off `active_roas`, not the top-tile.

**Worked example — why this matters**: Campaign with 13 keyword
rows; 11 are Paused (carrying old spend SAR 125, old sales SAR
60, ROAS 0.48 — these dragged the historical campaign-level ROAS
to 1.35). The 2 active rows have spend SAR 3.50, sales SAR 53.99,
1 order, active_roas = 15.43. The campaign top-tile shows ROAS
1.35 — alarming if read at face value. But the active-only ROAS
is 15.43 — far above target. **Diagnosis**: the campaign is
self-stabilized, the paused rows did the bleeding, and the
remaining active rows are profitable. **Action**: leave alone,
or only address the active rows individually. Do NOT recommend
pausing the campaign on the basis of the 1.35 number — that
would destroy a 15.43-ROAS source.

This rule composes with the row-level PROTECT rules in
`tuning-thresholds.md § Row-level PROTECT`: in a
self-stabilized campaign, the surviving active rows are
implicitly PROTECT-tagged unless they have themselves turned
negative within the window.

## Pitfalls

- **Sample size**: don't apply layer thresholds to campaigns with
  < 50 clicks or < 14 days of data. Cold-start noise. Wait.
- **Match type confound**: a broad-match keyword's CTR is naturally
  lower than exact's because broad shows on more queries, including
  irrelevant ones. Compare like-for-like (broad vs broad median).
- **Category effect**: CTR / CVR norms vary by category. Apparel
  CTR is much higher than Electronics CTR. Use the store's own
  median, not industry averages.
- **Halo / brand effect**: branded queries convert dramatically
  better than non-branded. If a campaign mixes both, the aggregate
  CVR can hide a non-branded problem. Look at the search-terms
  report to separate.
- **Ad copy assumption**: for Sponsored Products, the listing IS
  the ad. There is no separate ad copy to optimize. CTR / CVR are
  the listing's CTR / CVR — they don't change with bid.
