# Noon Ads — Create Campaign Playbook

For new manual-targeting Product Ads on noon Ad Manager. Pair with
`../SKILL.md § 8` for the click-by-click form mechanics; this file
is the *thinking* — when to choose what, and the gotchas that
aren't obvious from the form.

## When to use which targeting type

| Goal | Targeting | Why |
|---|---|---|
| Just-launched listing, no data yet | Auto first | Let noon's algorithm probe match terms; harvest queries from Customer Queries after 1–2 weeks. |
| 2+ weeks live, Auto running but ROAS poor / off-target traffic | Add Manual alongside Auto | Manual locks in high-intent terms with lower CPCs; Auto stays for discovery. |
| Listing has known buyer-side keywords (verified via storefront search) | Manual only | Skip Auto's noise. Faster to optimize, cheaper per click. |
| Rescuing an under-performing listing | Manual + tight keyword list | Auto on a low-quality listing burns budget on bad matches; Manual gives you control. |

## Bidding strategy choice

Three options, but practically:
- **Fixed** — choose this for Manual Targeting. Predictable;
  per-keyword bids honored as-is. Best for diagnosis because you
  can read keyword performance without dynamic-bid noise.
- **Dynamic Bid (Down Only)** — safer with Auto. noon throttles
  bids on low-conversion impressions. Use when you want Auto but
  are bid-shy.
- **Dynamic Bid (Up & Down)** — Auto-only. Aggressive; noon may
  bid up to 2× on top placements. Don't use on a new / unproven
  listing.

## Per-keyword bid: heuristic for first launch

The form suggests a low/high pair per keyword. For a *new* manual
campaign, set bids via the bulk-apply flow at **the high end of
noon's suggested range, or just above**. Reasoning:
- noon's auction is rating- and conversion-weighted; a fresh listing
  loses ties to peers. Bidding at suggestion midpoint usually
  means losing ~half the auctions.
- Per-keyword bids combine with §5 Top-of-Search boost. A 250%
  boost on a too-low base bid is multiplying a small number.
- Rule of thumb: target 1.0–1.5× the high end of the suggested
  range for the first 2 weeks; reduce on keywords that converted
  at the starting bid.

Use Manual Upload tab → bulk-paste keywords → Add Keywords →
Select All → **Apply Bids to Targets** → **Set Custom bid** →
enter value → Apply. Faster than per-row editing for >5 keywords.

**Custom-bid input quirk**: the field's `step` attribute is `1`,
so typing `1.00` may render as `10` or `100`. Type the integer
form (`1`) and the input accepts it; the per-keyword input fields
themselves use `step=0.01` so decimals work there.

## Match type: Phrase > Exact for noon

Default to **Phrase** for most keywords on noon. Reasoning:
- noon is a low-volume site. Exact-only locks out long-tail
  variants that may be the only buyers searching that month.
- Phrase still respects word order and intent; you don't get
  Auto-style random matches.
- Reserve **Exact** for: (a) defending your own brand name,
  (b) siphoning a competitor brand, (c) ultra-high-intent generic
  terms where you want to outbid specifically.

## Negative keywords: scoping

Cap is **20 Exact + 20 Phrase** per campaign. Spend them on:

| Negate | Match type | Why |
|---|---|---|
| Specific gender mismatch (e.g. opposite-gender token) | Exact | Single-token signal, cheap. |
| Adjacent-but-wrong audience (e.g. `kids` on adult product) | Exact | Same. |
| Multi-word irrelevant phrases (`return policy`, `for free`, `review`) | Phrase | Catches variations. |
| Use-case mismatch (`for travel` on home-only product) | Phrase | Catches the concept. |
| Direct competitor brand names | Exact | Stops bidding against direct rivals. |

Add negatives BEFORE launch when you have a clear off-target risk
list. Otherwise, harvest them from Customer Queries post-launch
(see `ads-tuning.md`).

## Top-of-Search bid boost

`0%` to `900%` (integer field). Boosts effective bid for the
top-of-search placement only.

| Setting | When |
|---|---|
| `0%` (default) | Only if listing is highly competitive on price+rating+title and the keyword is super high-intent. Otherwise you'll lose to anyone bidding the placement. |
| `150–250%` | Typical starting point for a new manual campaign trying to gain visibility. Pairs with bids at the high end of the suggested range. |
| `400–900%` | Only if (a) the listing is already proven on Amazon but starved of impressions on noon, AND (b) the daily budget can absorb high CPCs. Re-evaluate after 7 days. |

The boost only fires for the top-of-search placement; PDP and
category placements use the base bid. That's why a 250% boost
doesn't 3.5× your daily spend — it's selective.

## Budget: Campaign Budget vs Shared Budget

Always **Campaign Budget** for new diagnostic campaigns. Shared
Budget pools spend across campaigns and obscures which one drove
what. Diagnosis requires per-campaign isolation. Switch to Shared
only after the campaign is proven and you want portfolio-level
allocation.

Maximum daily budget: the minimum is the marketplace floor (read it
from the form — do NOT hardcode a currency). For a diagnostic manual
campaign, a small budget of ~30–50/day in the marketplace currency
validates keyword performance
within a week. Higher budgets just mean you waste money faster
if the diagnosis was wrong.

## Save as Draft → Launch — UI quirk

After clicking **Save as Draft** on the create form, noon shows a
success modal that *overlays* the page-level **Launch Campaign**
button, making it un-clickable directly. Three-step recovery to
launch a saved draft:

1. In the Save success modal, click **View Campaign**.
2. On the campaign-detail page, click the **blue Edit pencil**
   icon top-right (no text label; it's the colored circular
   button next to the date range picker).
3. The full create form re-opens with all fields populated. Scroll
   to the bottom and click **Launch Campaign**.

This is the only path to launch a saved draft. There is no
Launch / Activate / Enable button on the campaign detail page or
the campaigns list — the detail page only displays the Draft
status badge.

## Campaign naming convention

Use the same scheme across stores so dashboards stay sortable:

```
<sku-or-product-id> <Targeting> <Country> - agent
```

Synthesized examples:
- `STORE-101 Manual US - agent`
- `STORE-102 Auto UK - agent`
- `STORE-103-mix Manual US - agent` (mixed-variant SKU)

The trailing `- agent` flag distinguishes agent-created campaigns
from human-created ones in the campaigns list. Keep the country
code in the name even though noon scopes by project — it protects
against confusion when one user manages multiple country projects.

## After launch — verification cadence

| When | What to verify |
|---|---|
| 30 min after launch | Status = Live (not Draft, not Pending Review). If Pending Review > 1 hour, check the policy notice (some restricted categories require manual review). |
| Day 1 | Views > 0 across most keywords. Zero-view keywords on day 1 = bid likely below auction floor. |
| Day 3 | Per-keyword CTR. < 1% over ≥10 Views = creative/match issue, not bid issue. |
| Day 7 | First Orders. Apply funnel diagnosis (`ads-tuning.md`) before any bid changes. |
| Day 14 | First reliable ROAS read. Anything earlier is statistical noise on a low-volume site. |

Don't tune in the first 7 days unless something is *obviously*
broken (e.g. 1000 views and 0 clicks — broken creative). noon's
auction takes a few days to settle on a new campaign.
