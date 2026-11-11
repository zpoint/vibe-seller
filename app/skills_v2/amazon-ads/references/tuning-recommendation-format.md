# Deciding each row's action

Shared by `amazon-ads` and `noon-ads`. This is the one home for the
decision rules; other references link here. The thresholds named below
(`acos_no_lower`, `cpc_floor_multiplier`, `scale_roas`,
`change_cooldown_days`, `margin`) are defaults listed in
[`data-contract.md`](data-contract.md) and overridden per store by
`key: value` lines in `stores/<slug>/notes.md`. `target_roas` and the
waste / harvest cutoffs are derived per store in
[`tuning-thresholds.md`](tuning-thresholds.md). The decisions land in
`CHANGES.tsv` (mapping in [`audit-quickref.md`](audit-quickref.md) §5).

- [The optimizer's bar](#the-optimizers-bar)
- [Zero-sales rows](#zero-sales-rows)
- [PROTECT](#protect)
- [Bid floor and step cap](#bid-floor-and-step-cap)
- [Scale winners](#scale-winners)
- [Cooldown](#cooldown)
- [Missing margin](#missing-margin)
- [Search-term actions](#search-term-actions)
- [Precedence when several signals fire](#precedence-when-several-signals-fire)
- [Campaign-level calls](#campaign-level-calls)

## The optimizer's bar

Every decision should be one a careful human optimizer would sign,
matched to that row's own data rather than a template:

- Spend or clicks with no result → negate the waste or pause the target.
- Poor efficiency on enough data → lower the bid (within the floor) or
  pause.
- Good efficiency → keep, or raise when there is room to win more.
- Too little data → find out why before deciding. A bid far below the
  competitive range (0.10 against a suggested 1–3) will never collect
  data, so "keep and watch" is inaction dressed as advice: raise it to a
  level that can win traffic, or pause it if the target isn't wanted.
  When the bid is reasonable and volume is simply low or the campaign is
  new, "keep and watch" is honest — say why.
- The first word of the decision is the decision. A row that should be
  paused says pause; don't write "keep" and hide "consider pausing" in
  the reason, because the person scanning the table acts on the first
  word.

## Zero-sales rows

When `spend > 0` and `sales = 0`, Amazon prints ACOS 0.00 (and ROAS 0).
That is the worst case — every unit of spend wasted, effective ACOS
infinite — not a healthy row under the ACOS threshold. The ACOS-based
rules below apply only to rows with at least one order; a zero-sales row
with material spend is a candidate for pause or for negating the queries
that spent. In the TSVs, leave `acos` empty and write `roas` `0`.

## PROTECT

A keyword, target or search-term row is **PROTECT** when either holds:

1. **Efficiency** — row ROAS ≥ `target_roas` × 2 with at least 1 order.
2. **Workhorse** — the row carries ≥ 50% of the campaign's orders.

What it means:

- No pause, and no cut that would put the row's orders at risk: a
  workhorse may be trimmed at most 15% in a session.
- PROTECT blocks cuts, not growth. An efficiency row is often the best
  candidate to raise (see [Scale winners](#scale-winners)).
- Don't pause a campaign that contains PROTECT rows; act on its
  offending rows individually instead.

In the report, mark it `Hold (PROTECT) — N orders, ROAS X` (add
`efficiency` or `workhorse`).

## Bid floor and step cap

**Floor for any new bid:**

```
target_bid ≥ max(actual CPC × cpc_floor_multiplier, suggested low)
actual CPC = spend / clicks          # cpc_floor_multiplier defaults to 1.1
```

The bid is a ceiling; Amazon's auction charges the actual CPC. A target
already clears the auction at its CPC, so a new bid below it stops the
target serving and its orders go with the spend. Amazon's suggested range
is meant for targets with no history — where the target has clicks, its
observed CPC wins. Where the platform shows no suggested range (noon),
the CPC term alone sets the floor. With no clicks there is no CPC, and
no `lower`.

**Step cap:** move a bid by at most 25% per session, up or down (15% down
for a workhorse). Step toward a target across audits so you can see
traffic respond before the next step. A one-shot jump to the suggested
midpoint from far above it is a bid collapse, not a trim.

**No lower under `acos_no_lower`.** A row with at least one order and ACOS
under the threshold (ROAS above `100 / acos_no_lower`) is converting
profitably: keep or raise it. A bid above the suggested range is not by
itself a reason to trim.

**When the floor leaves no room.** If the floor is at or above the
current bid, a trim is impossible. Pause the row if it is losing money
(and not PROTECT), or keep it with that reason.

Worked example: bid 2.00, 30 clicks, spend 99.00, 1 order, ACOS 265%,
suggested 0.60–1.00. CPC = 3.30, floor = max(3.30 × 1.1, 0.60) = 3.63 —
above the current bid, so no lower is possible. It's not PROTECT, so:
`pause` — "ACOS 265%, 30 clicks / 1 order; floor 3.63 ≥ bid 2.00".

Another: bid 3.00, 200 clicks, spend 500.00, 30 orders (80% of the
campaign), ACOS 42%, suggested 0.45–0.75. CPC = 2.50, floor = 2.75.
It's a workhorse, so the cap is 15%: no lower than 2.55. Both
constraints together: the lowest allowed bid is 2.75 — `lower` to 2.75,
then look again after a week. Dropping to
0.80 would lose the campaign's main order source.

In the report, show the numbers that justify a bid change: current →
target, the change in %, the floor and where it came from, and the
row's ACOS or ROAS.

## Scale winners

A row with ROAS above `scale_roas` and at least one order is a proven
converter. Raise it (up to the step cap, toward the top of the suggested
range) unless there is a concrete reason not to, and when you hold it,
write the reason: the bid is already at or above the suggested high,
impression share is already high, the campaign is budget-capped (raise
the budget instead), or search volume for the term is genuinely low. A
bare hold on a winner leaves profitable demand unclaimed.

## Cooldown

A target changed within `change_cooldown_days` (its `applied_at` in the
targeting TSV) has too little data since the change to judge. Hold it,
with the change and its age in the reason — e.g. "raised 2 days ago,
waiting for a week of data". Moving it again would react to noise and
destroy the evidence for the first change.

## Missing margin

Margin sets breakeven ACOS and with it every target. When neither the
store notes nor the user give one, use `margin` (default 25%) and state
in the report: `margin assumed 25% (default) — confirm`. Don't stop to
ask; the user corrects it on the next reply, and if they state a margin,
record it as `margin: <n>` in `stores/<slug>/notes.md`.

## Search-term actions

A search term is what the customer typed; it has no bid of its own (the
bid lives on the target that caught it). So search-term rows take
search-term actions, never bid verbs.

- **Negate** a term that spent past the waste cutoff with no orders.
  Aggregate first: the same term can appear under several targets, and a
  negative blocks it everywhere in the campaign or ad group, so negate
  only when the term has zero orders across all its rows. If any row
  converts, keep the term and act on the bleeding source target instead.
  Choose the match type: exact blocks that query only; phrase blocks
  every query containing it. A recurring irrelevant word ("free",
  "kids", a competitor's brand) is a phrase negative on the word.
- **Product / category placements** (an ASIN, `Category`, `Subcat`) have
  no exact-vs-phrase variant: negate the target, or keep it. Don't invent
  a match type.
- **Harvest** a converting term into its own exact keyword when you need
  separate bid control for it: its source target is being cut or paused
  (save the good traffic first), or it comes from an auto or broad source
  whose bid you can't raise for this term alone. Suggested bid for the new
  keyword ≈ the term's CPC × 1.1–1.25, and negate it as exact in the
  source so the two don't compete. When the source is healthy and staying
  as it is, it already catches the term — leave it. A term identical to
  its source keyword is already a keyword; don't harvest it.
- **Keep** a converting term whose source is healthy, or a term with too
  little data — say which.

**Judge the source after the waste is gone.** A broad or phrase keyword's
ACOS is often high because of junk queries you are negating in the same
audit. Remove those queries' spend and sales from the keyword's totals
before deciding its bid: if it is healthy without them, keep it and say
so; only if it is still poor, lower or pause it. When a source's action
changes, re-check the terms that depend on it — a paused source no
longer catches anything.

## Precedence when several signals fire

Resolve top-down and use the first match; mention the lower signals in
the reason, never as a second action.

| # | Signal | Decision |
|---|---|---|
| 1 | Inside cooldown | `hold`, with the change date |
| 2 | PROTECT | never `pause`; efficiency → `hold` or `raise`; workhorse → `hold`, or `lower` by ≤ 15% when ACOS ≥ `acos_no_lower` |
| 3 | Bleeder: 0 orders and spend ≥ the waste cutoff | `pause` the target / `negate` the term |
| 4 | ACOS ≥ `acos_no_lower` with orders | `lower` within floor and step cap, or `pause` if no room |
| 5 | Scale winner (ROAS > `scale_roas`, ≥ 1 order) | `raise`, or `hold` with the reason |
| 6 | Bid far below the competitive range, no traffic | `raise` to a level that can win, or `pause` |
| 7 | none of the above | `hold` |

## Campaign-level calls

- **Auto campaigns: prune, don't pause.** If an auto group's search terms
  include converters, negate the zero-order waste and keep the group
  running; low group ROAS is usually the waste dragging it down. Pause
  the whole group only when none of its terms convert, or it still loses
  money after the waste is removed. If you do pause it, harvest its
  converters first — otherwise the queries it was catching are lost.
- **A losing campaign that row actions can't fix** (floors block every
  trim, or the main keyword is being paused) needs a campaign-level call
  — pause, restructure, or merge — not a column of holds.
- Keep the summary consistent with the rows: what the summary says to
  negate, the rows negate.
