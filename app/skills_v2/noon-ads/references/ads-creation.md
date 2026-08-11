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

The form pre-fills each target with noon's suggested bid, and offers
a low/high suggested range.

**Do not treat noon's suggested range as the ceiling.** Campaigns
observed winning meaningful impression share in this codebase sit at
or ABOVE the top of the suggested range, and campaigns pinned to the
top of the range still show single-digit SOI. The suggestion is a
floor-ish hint, not a calibrated target.

**Prefer the store's own history over any rule of thumb.** Before
picking a number, read what already works on this account:

```bash
# click-weighted bid distribution from prior Customer-Queries exports
#   stores/<slug>/ads/noon/<cc>/*.searchterms.tsv  → target_bid, clicks
# report P25 / P50 / P75 and the realised eCPC, then start at ~P75
```

That gives an empirically-supported opening bid instead of a guess.
Absent any history, start at the high end of noon's range and treat
the first 3 days as calibration.

**Then verify with SOI, not with ROAS.** Three days in, the question
is "did impression share move?" (`../SKILL.md § 4`). ROAS at that
point is noise on a low-volume site. If SOI did not move, decide
between bid and relevance using
`ads-tuning.md § Head terms vs modified terms` **before** raising
again — bidding into a relevance wall just burns budget.

Set the per-target bids by native-setting every narrow
`input[type=number]` (`step=0.01`) in the targeting block — scroll
that block into view first, or the inputs are not yet in the DOM and
the write silently no-ops. Read the values back to confirm. This is
the path verified live 2026-08-10.

> A bulk path (`Manual Upload` tab → Add Keywords → Select All →
> **Apply Bids to Targets** → **Set Custom bid**) is documented from
> an earlier build, along with a quirk where that field's `step="1"`
> made `1.00` render as `10`/`100` (type the integer form instead).
> **Not re-verified in the current build** — if you use it, confirm
> the resulting per-target bids before launching.

## Match type: choose per keyword, not per campaign

An Exact-heavy campaign starves itself and a Phrase-everything
campaign leaks. Decide per keyword, from where that keyword's volume
actually sits:

| Keyword shape | Match | Why |
|---|---|---|
| Qualified / multi-word (`<category> <audience>`, `<attribute> <category>`) | **Phrase** | The variants (`… <material>`, `… <length>`) are real, same-intent demand. The same term at the same bid can draw **orders of magnitude** more impressions as Phrase than as Exact — measure it on your own account before assuming Exact is "tighter and therefore better". |
| Bare category noun with real volume | **Exact** | Phrase on a bare noun is where wrong-audience traffic enters (opposite gender, kids, competitor brand). Exact matches only the query itself, so the leak is structurally impossible. |
| Proven converter (has orders in your own data) | **Exact**, bid up | You already know the intent converts; buy the exact query specifically. |
| Your own or a competitor brand | **Exact** | Defend / siphon precisely. |

> Bidding a bare category noun at all is a separate decision from
> the match type — for many listings it is not worth buying. See
> `ads-tuning.md § Head terms vs modified terms` first, and verify
> on the store's own data.

Note that `searches/month` is measured per exact string: word order
matters and `<noun> <modifier>` may be several times bigger than
`<modifier> <noun>`. Size both before choosing which to bid
(`ads-keyword-research.md § Step 1`).

## Negative keywords: scoping

Cap is **100 Exact + 100 Phrase** per campaign (the counter under
each box reads `N/100 … Selected`). Spend them on:

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

`0%` to `900%` (integer field, blank = `0`). Boosts the effective bid
for the top-of-search placement only; PDP and category placements use
the base bid.

**Default to `0%`.** An earlier revision of this file recommended
`150–250%` as the "typical starting point" for a new manual campaign
and `400–900%` for a proven listing. That was not grounded in
observed results, and the campaigns in this codebase with the
strongest ROAS in their category run **`Top of Search 0%`** with
ordinary per-keyword bids. Check the account's own winners
(campaign detail header shows `Top of Search N%`) before assuming a
boost is needed.

| Setting | When |
|---|---|
| **`0%`** | **Default.** Start here. Establish base-bid performance first — you cannot tell whether a boost helped if you never measured without it. |
| Small boost (`≤100%`) | Only after ≥14 days show the keyword converts at base bid but sits low in SOI *and* the low SOI is a price problem, not a relevance one (`ads-tuning.md § Head terms vs modified terms`). |
| Large boost | Rarely justified. It multiplies the cost of the placement you understand least. If you reach for this, say why in the recommendation and set a review date. |

**Budget interaction — the reason `0%` matters on a small budget.**
The boost multiplies the bid, so top-of-search clicks cost
`bid × (1 + boost)`. On a small daily budget a large boost concentrates
the whole day's spend into a couple of clicks, which produces no
readable signal: e.g. a `250%` boost on a `1.00` bid makes a
top-of-search click `3.50`, so a `15/day` budget buys ~4 of them.
Boost and budget must be chosen together.

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

## Launching

**Prefer launching directly from the create form** — fill everything,
then click **Launch Campaign** at the bottom. Verified live
2026-08-10 across four consecutive campaigns; the header flips to
`Edit - <name> | Product Ad | Live` on success. You do not need to
save a draft first.

> **The button is below the fold.** On a completed form it lands well
> past the bottom of a standard viewport, and `click_at_xy` on its
> reported coordinates silently hits nothing — the form just sits
> there, which is easy to misread as a validation failure (there is
> no error banner, because nothing was submitted).
> `scrollIntoView({block: "center"})` first, re-read the rect, then
> click. Same applies to the keyword `Exact`/`Phrase` add buttons.

### Save as Draft → Launch — UI quirk (drafts only)

If you do save a draft, noon shows a success modal that *overlays*
the page-level **Launch Campaign** button, making it un-clickable
directly. Three-step recovery to launch a saved draft:

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
