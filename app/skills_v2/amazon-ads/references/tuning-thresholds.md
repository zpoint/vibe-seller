# Store thresholds — how to derive the cutoffs

Cutoffs come from this store's own data over the session's window plus
its margin, not from absolute numbers: a waste line that suits a USD 15
product would let a USD 300 product bleed. The fixed rule thresholds
(`acos_no_lower`, `cpc_floor_multiplier`, `scale_roas`,
`change_cooldown_days`, `margin`) are defaults in
[`data-contract.md`](data-contract.md), overridden by `key: value` lines
in `stores/<slug>/notes.md`. How they are applied:
[`tuning-recommendation-format.md`](tuning-recommendation-format.md).

## Inputs

| Input | Source |
|---|---|
| Total spend, sales, orders | sum over the campaigns in the window |
| AOV | total sales / total orders (undefined with 0 orders) |
| Margin % | `margin:` in store notes, else the default (`tuning-recommendation-format.md` § Missing margin) |
| Goal | [§ Goal](#goal): `scale` unless the store notes or the user plainly name another |

## Derived values

**Breakeven ACOS** = margin %. Spending more than the margin on ads loses
money on the marginal order (ignoring lifetime value and organic-rank
effects).

**Target ACOS**, by goal:

| Goal | Target ACOS |
|---|---|
| launch | breakeven (tolerate breaking even to build rank) |
| scale (default) | 70% of breakeven (leave some margin) |
| profit | 50% of breakeven (protect margin) |

**Target ROAS** = 1 / target ACOS. PROTECT and harvest use it.

**Waste cutoff** (negate candidate): a search term with 0 orders and
spend ≥ 1.5 × AOV — enough spend to have produced an average order, with
none. Tighten to 1 × AOV for a profit goal; loosen to 2 × AOV for a
launch.

**Harvest cutoff** (candidate for its own exact keyword): ≥ 1 order and
ROAS ≥ target ROAS × 1.5 — comfortably above target, since edge-of-target
ROAS often regresses when promoted to its own higher-bid keyword. Whether
to actually harvest: `tuning-recommendation-format.md` § Search-term
actions.

**Bid drift**: bid > 1.5 × or < 0.5 × Amazon's suggested midpoint. Drift
alone is not a reason to lower a converting target — see § Bid floor and
step cap in `tuning-recommendation-format.md`.

**Budget-throttled**: the campaign list shows `Out of budget`. Use
Amazon's signal rather than spend ÷ budget arithmetic: Amazon may
overspend up to about 25% on a single day and balance it over the month.

## Goal

The goal is one of the three in the Target ACOS table. Pick it once,
by this rule, not by weighing the user's phrasing:

1. A `goal: launch|scale|profit` line in `stores/<slug>/notes.md` →
   that goal.
2. Otherwise the user's words, only when they plainly name one: a new
   product, a launch, or "breaking even on ads is fine" → `launch`;
   cut ad spend, protect margin or maximise profit, with nothing about
   growth → `profit`.
3. Everything else is `scale`, including mixed wording such as
   "profitable growth" or "grow without losing money": scale's target
   (70% of breakeven) is growth that keeps margin.

State it once in the report next to the margin — `goal: scale
(default)`, or `goal: profit — you asked to protect margin` — and don't
reopen it later in the task. If the user meant another goal they say so
on the next reply; record it as `goal: <name>` in
`stores/<slug>/notes.md`.

## Worked examples

Illustrative numbers, scale goal.

| Store | Spend / sales / orders | AOV | Margin | Target ACOS | Target ROAS | Waste cutoff | Harvest ROAS |
|---|---|---|---|---|---|---|---|
| Small | 250 / 1,200 / 8 | 150 | 35% | 24.5% | 4.08 | 225 spend, 0 orders | ≥ 6.12 |
| Medium | 4,200 / 10,500 / 320 | 33 | 45% | 31.5% | 3.17 | 49.50 spend, 0 orders | ≥ 4.76 |
| Large | 80,000 / 260,000 / 5,400 | 48 | 30% | 21% | 4.76 | 72 spend, 0 orders | ≥ 7.14 |

## Adjusting

- **High-AOV stores** (luxury, B2B, durables): 1.5 × AOV is generous;
  use 1 × AOV or lower.
- **New campaigns** (< 14 days): loosen or skip the cutoffs — too little
  data.
- **Seasonal products**: compare with the same season last year, not
  only the trailing window.
- **Out-of-stock SKUs**: metrics drop to zero through no fault of the
  ad. Check inventory before tuning.
- **The user states a different standard** ("protect anything with 30+
  orders", "our margin is 40%"): apply it, and if it is one of the keys
  in `data-contract.md`, write it to `stores/<slug>/notes.md` so later
  runs use it.
