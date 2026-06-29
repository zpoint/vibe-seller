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

Protection is checked at **two scopes**: campaign-level (don't
recommend pausing / blanket-budget-cutting the whole campaign) and
row-level (don't recommend pausing / cutting bid on the individual
keyword, target, or search term).

#### Campaign-level PROTECT

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

#### Row-level PROTECT (keyword / target / search term)

A row is **protected from cuts** if ANY of these hold:

1. **Volume PROTECT**: `row_orders ≥ p75(per_row_orders within campaign)`
   AND `row_orders ≥ 0.05 × total_row_orders` (same two-filter rule as
   campaign-level, applied within the campaign's row set).
2. **Efficiency PROTECT**: `row_orders ≥ 1` AND `row_roas ≥ target_roas × 2`.
   A keyword with ROAS = 35.99 on USD 1.50 / 1 order isn't a volume
   driver, but it's an efficiency outlier — pausing it loses pure
   margin. The ≥ 1 order filter excludes noise (0-click rows with
   undefined ROAS).
3. **Self-stabilized campaign PROTECT**: if the row is among the
   *only* active rows in a campaign where most of the campaign has
   already been paused (≥ 50% of rows in Paused state), the active
   rows are by definition what survived prior tuning. Treat as
   PROTECT until evidence the row has *itself* turned negative.

The skill must NOT recommend pausing or cutting bid on a
PROTECT-tagged row, and must NOT recommend pausing the parent
campaign if any of its active rows are Efficiency-PROTECT or
Self-stabilized-PROTECT — those orders would be lost to the
blanket action.

When the campaign-level signal would say "pause this campaign"
but a row-level PROTECT exists, downgrade the campaign action to
"keep campaign live; surface only the offending rows for individual
action". Trail the report with the per-row recommendations under
the campaign heading; do not emit a campaign-level pause verb.

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

### ACOS bid-direction lock (HARD RULE — overrides bid drift)

A keyword with a **measurable ACOS below 30 %** may only have its bid
**raised or held — NEVER lowered**, no matter how far `current_bid`
sits above `suggested_bid_midpoint`. Rationale: ACOS < 30 % means the
keyword is converting profitably and is usually carrying real order
volume; the bid is a *ceiling*, not spend — Amazon's second-price
auction already charges the actual CPC, which is typically far below
the bid. Lowering the ceiling on such a keyword discards profitable
volume for no real saving. **Bid drift alone (`current_bid > 1.5 ×
suggested`) is NOT a trim trigger** — it only justifies a trim when the
keyword is *also* inefficient (ACOS ≥ 30 %).

- **ACOS < 30 %** → `Hold`, or `Raise` if under-bidding
  (`current_bid < suggested_low` and the keyword is volume-constrained).
  Never `下调` / lower.
- **ACOS ≥ 30 %** → a bid trim is allowed (step cap −25 %/session),
  but the proposed new bid must **never be ≤ actual CPC** — floor it at
  `max(actual_CPC × 1.1, suggested_low)`, else you kill the volume the
  keyword currently wins.
- **noon tables report ROAS, not ACOS.** `ROAS > 3.33` is exactly
  `ACOS < 30 %` → same lock: raise/hold only.
- Zero-order waste is handled by **negation** of the search term, not
  by cutting a converting keyword's bid.

This is enforced server-side at `set_task_result`: a report containing
a bid-lowering recommendation on any row whose ACOS < 30 % (ROAS > 3.33)
is **rejected** until fixed. See `app/ai/stop_gates/ad_bid_floor.py`.
Every per-keyword trim cell must state the ACOS (or ROAS) it was based
on so the rule is auditable.

### Scale winners (raise-bid) — the upside side of the lock

The lock above forbids *cutting* a profitable keyword; this rule is the
**positive** obligation: a high-ROAS keyword that is volume-constrained
should be **raised**, not parked on `Hold`. "Hold everything that's
good" leaves money on the table — a keyword with ROAS 24 on 2 clicks is
not "protected", it is **under-bid**: you are winning a sliver of
obviously-profitable demand.

> **Bid-rule thresholds — single source of truth.** The two enforced
> numbers live in ONE place: `app/ai/stop_gates/ad_rules.py`
> (`DEFAULT_RULES`): `scale_roas = 5` (raise-or-justify above this) and
> `acos_no_lower = 30` (never lower a bid below this ACOS%). Don't
> restate them elsewhere — cite this. A **store may override** either by
> writing a line in `stores/<slug>/notes.md` (matched anywhere,
> case-insensitive): `scale_roas: 6` or `acos_no_lower: 28`. The
> `set_task_result` gates resolve defaults-then-notes per audit.

A keyword is a **scale (raise-bid) candidate** when ALL hold:

- `row_roas > scale_roas` (default 5, or the store's notes.md override;
  noon reports ROAS directly, Amazon: `1 / ACOS`) — harvest-grade
  efficiency,
- `row_orders ≥ 1` — a proven converter, not 0-click noise,
- it is **volume-constrained** — ANY of: `current_bid ≤ suggested_bid_high`,
  actual CPC sitting at/below the bid with low clicks, low impression
  share, OR the campaign is NOT `Out of budget`.

For a scale candidate, recommend **Raise** (≈ +15–25 %/session toward
`suggested_bid_high`, or one bid step), NOT `Hold`.

**Efficiency-PROTECT (ROAS ≥ 2 × target) means "never CUT" — it does
NOT mean "never raise".** A 2×-target ROAS row is the *best* raise
candidate, not an untouchable `Hold`. Re-read the PROTECT section in
that light: PROTECT blocks pauses and cuts, not growth.

Legitimate reasons to `Hold` a high-ROAS keyword — **state the reason
in the cell**: bid already at/above `suggested_bid_high` (no profitable
headroom), impression share already high (little volume left to win),
campaign budget-capped (raise the *budget* instead), or genuinely low
search volume for the term. A bare `Hold` / `保持不动（受保护）` on a
`ROAS > scale_roas` converter with none of these reasons is a defect —
enforced server-side by `app/ai/stop_gates/ad_scale_winners.py`.

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
