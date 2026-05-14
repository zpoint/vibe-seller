# Threshold derivation — how to compute store-specific cutoffs

The tuning skill never hardcodes thresholds. Every cutoff is computed
from this store's own distribution **over the session's pinned
analysis window** (default last 30 days; user can override with any
window — last 14 days, year-to-date, custom calendar) plus the user's
margin/goal. Examples below say "30d" for readability; substitute the
actual window when the user picked something else.

## Inputs to gather at session start

| Input | Source | Notes |
|---|---|---|
| Total spend | sum of `Total cost` column across campaigns | display in marketplace currency |
| Total sales | sum of `Sales` column | |
| Total orders | sum of `Purchases` column | |
| AOV | total_sales / total_orders | undefined if 0 orders |
| Per-campaign orders | each row's `Purchases` value | array, sort desc |
| Per-campaign spend | each row's `Total cost` value | array, sort desc |
| Margin % | ask user once per session | drives breakeven ACOS |
| Goal | ask user once per session | launch / scale / profit |

## Derived thresholds

### Breakeven ACOS

```
breakeven_acos = margin_pct
```

Spending more than the margin on ads loses money on the marginal order
(ignoring lifetime value, halo, organic-rank value).

### Target ACOS (varies by goal)

```
launch_target_acos  = breakeven_acos × 1.0   # tolerate breakeven
scale_target_acos   = breakeven_acos × 0.7   # leave margin
profit_target_acos  = breakeven_acos × 0.5   # protect margin
```

Default: `scale_target_acos`. User can override.

### Target ROAS

```
target_roas = 1 / target_acos
```

ROAS is just the inverse — using whichever the campaign UI surfaces.

### Protect-zone (don't auto-cut these)

A campaign is **protected** if BOTH conditions hold:

1. `campaign_orders ≥ p75(per_campaign_orders)` — top quartile by
   order volume.
2. `campaign_orders ≥ 0.05 × total_orders` — material share.

The two-filter rule prevents both:
- A small-volume store from over-protecting (where p75 might be 1
  order, and a campaign with 1 order shouldn't be untouchable).
- A large store from under-protecting (where 5% of total may be a
  big absolute number that easily clears p75 anyway).

The user may override the formula with their own preferred rule
("protect anything with ≥ 30 orders", "top 5 campaigns by orders only",
etc.) — store the override for the session.

### Waste threshold (auto-negate candidate)

A search term is a **negative-exact / negative-phrase candidate** if:

```
search_term_cost ≥ 1.5 × AOV   AND   search_term_orders == 0
```

The 1.5 × AOV multiplier means "we've spent enough to have produced an
order at average value, and we got none". Tighter (≥ 1 × AOV) for
profit-goal sessions; looser (≥ 2 × AOV) for launch-goal sessions.

### Harvest threshold (exact-match candidate)

A search term is a **harvest-into-exact-match candidate** if:

```
search_term_orders ≥ 1   AND   search_term_roas ≥ target_roas × 1.5
```

The × 1.5 buffer means "comfortably above target, not on the edge" —
edge-of-target ROAS could regress when promoted to a higher-bid exact-
match keyword.

### Bid drift threshold

A keyword's bid is a **bid-realign candidate** if:

```
current_bid > 1.5 × suggested_bid_midpoint
   OR
current_bid < 0.5 × suggested_bid_midpoint
```

Amazon shows a `Suggested bid` range for each keyword on the Targeting
tab. The midpoint is a decent proxy for competitive market price.

### Budget headroom (spend-cap detection)

A campaign is **budget-throttled** if `Status` shows `Out of budget`
in the campaign list. This signal is given by Amazon directly — do not
re-derive from spend/budget arithmetic (Amazon allows up to 25%
overspend on individual days, balanced across the month, so simple
spend ≥ budget arithmetic gives false positives).

## Worked examples

### Small store

- 30d totals: spend $250, sales $1,200, **8 orders**.
- AOV = $150. Margin 35%.
- Per-campaign orders distribution: [4, 2, 1, 1, 0, 0, 0, 0] across 8 campaigns.

Derived:
- breakeven_acos = 35%
- target_acos (scale) = 24.5%
- target_roas = 4.08
- p75 of orders = 1.75 → use 2 (round up); 5% of total = 0.4 → use 1.
  Both filters: protect campaigns with ≥ 2 AND ≥ 1 orders → **only the
  top campaign (4 orders) and second (2 orders) are protected.**
- waste threshold: search term cost ≥ $225 (1.5 × $150) with 0 orders.
- harvest threshold: search term ≥ 1 order AND ROAS ≥ 6.12 (4.08 × 1.5).

### Medium store

- 30d totals: spend $4,200, sales $10,500, **320 orders**.
- AOV = $33. Margin 45%.
- Per-campaign orders: [85, 60, 45, 40, 30, 25, 18, 12, 4, 1].

Derived:
- breakeven_acos = 45%
- target_acos (scale) = 31.5%
- target_roas = 3.17
- p75 of orders = 47.5 → 48; 5% of total = 16. Protect: ≥ 48 AND ≥ 16
  → top 3 campaigns (85, 60, 45 orders if we round 47.5 to 48 strict;
  at lower tail use 47). Top 3 are protected.
- waste threshold: cost ≥ $49.50 with 0 orders.
- harvest threshold: ≥ 1 order AND ROAS ≥ 4.76.

### Large store

- 30d totals: spend $80,000, sales $260,000, **5,400 orders**.
- AOV = $48. Margin 30%.
- Distribution flatter — top 5% spread across many campaigns.

Derived:
- breakeven_acos = 30%
- target_acos (scale) = 21%
- target_roas = 4.76
- p75 of orders, 5% of total = 270 → much higher absolute floor. The
  protect set is smaller in count but larger in absolute orders.
- waste threshold: cost ≥ $72 with 0 orders.

## Notes on overrides

- **High-AOV stores** (luxury, B2B, durables): waste threshold based
  on 1.5 × AOV may be too generous. Use 1.0 × AOV or even 0.7 × AOV.
- **Brand-new campaigns** (< 14 days): all thresholds should be
  loosened or skipped entirely — too little data.
- **Seasonal campaigns**: thresholds drift across seasons. Pull a
  prior comparable period as baseline, not just the trailing 30d.
- **Out-of-stock SKUs**: campaign metrics drop to zero through no
  fault of the ad. Check `Inventory` → `In stock` before tuning.
