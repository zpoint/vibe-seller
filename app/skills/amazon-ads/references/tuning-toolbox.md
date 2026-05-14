# Tuning toolbox — the levers

Every recommendation the skill makes is one of **9 standard levers**
(detailed below as Lever 1–9) plus **2 advanced levers disabled by
default** (Lever 10–11). Use this catalog to pick the right lever
for the right diagnosis.

When cross-referencing levers from other skill files, **use the
detailed Lever IDs (1–11) below** — never the preference-tier
ordinals from the next section.

## Lever scope (surgical → blanket)

The levers below are listed by their **scope of effect** — from
the most surgical (changes one search term) to the most blanket
(changes the whole campaign or pauses it). The agent picks based
on the diagnosis; this catalog just exposes how broad each lever
reaches and which other levers carry similar surgical power.

| Scope | Levers | What it touches |
|---|---|---|
| Search-term level | 1 (negative kw), 4 (negative ASIN) | One query; surrounding keywords unaffected |
| Single keyword | 2 (pause), 3 (lower bid), 5 (raise bid) | One keyword in one ad group |
| Single search-term harvest | 8 (harvest to exact + back-negate) | Adds a new keyword + sets one negative |
| Placement modifier (+ only) | 6 | Biases bidding toward one placement |
| Ad-group bid (whole group) | (generalisation of 3 / 5) | All keywords in the group at once |
| Daily budget | 9 | Whole campaign's spend cap |
| Campaign bidding strategy | 7 | All bids in the campaign re-flexed by Amazon's engine |
| Pause / archive | (no separate Lever ID) | Whole campaign |

Surgical levers are more reversible (one row, one click to undo).
Blanket levers ripple through the whole campaign. A campaign-level
bidding-strategy change isn't *wrong* — it's just blanket, and
blanket changes are harder to attribute when the next read shows
metrics moved.

## Cut-waste levers

### 1. Negative exact / negative phrase keyword

- **What**: a search term flagged as a customer query the campaign
  shouldn't appear for again.
- **Where**: Search terms tab → row → Add as ⌄ → `Add as negative
  exact` or `Add as negative phrase`. Or campaign-level Negative
  targeting tab → Negative keywords sub-tab → Add.
- **When**: search_term_cost ≥ 1.5 × AOV with 0 orders (waste
  threshold). OR a pattern of irrelevant terms sharing a word
  ("free", "kids", competitor brand) → negate the word as phrase.
- **Direction**: add (creates a new exclusion rule).
- **Match types**: exact, phrase. **No negative-broad on Amazon SP.**
- **Pitfall**: aggressive negatives can suppress relevant variants;
  always check what other search terms might match the negative
  before adding.

### 2. Disable / pause keyword

- **What**: pause an existing keyword target so Amazon stops bidding
  on it.
- **Where**: Targeting tab → row → Active toggle (blue=on, gray=off).
- **When**: keyword has 14+ days of data, ROAS < 1, no order signal,
  and not protected.
- **Direction**: state change (active → paused). Reversible.
- **Pitfall**: lots of paused keywords clutter the campaign; archive
  them periodically.

### 3. Lower per-keyword bid

- **What**: reduce the bid Amazon places for a specific keyword.
- **Where**: Targeting tab → row → bid cell (kat-numberinput) → type
  new value → Save.
- **When**: per-keyword ROAS < target_roas, but the keyword still
  produces some orders (worth keeping at lower bid).
- **Direction**: decrease, in steps.
  - Soft trim: -15% (1× target → 0.85× target)
  - Standard: -25 to -30%
  - Aggressive: -50% (consider pausing instead at this scale)
- **Range**: positive currency, ≥ marketplace minimum bid (typically
  SAR 0.50 / AED 0.50 / USD 0.10 depending on marketplace).
- **Pitfall**: Amazon's auto-bid (rule-based, dynamic strategies)
  may revise the bid back up after you set it; verify by reading
  back the cell after a few minutes.

### 4. Negative ASIN / product target

- **What**: prevent the ad from showing on a specific competitor's
  product detail page.
- **Where**: campaign Negative targeting → Negative products tab →
  Add negative product → ASIN list.
- **When**: Product-pages placement has high spend with low CTR /
  CVR, suggesting the ad is being placed on irrelevant PDPs.
  Identify which ASINs from the search-terms report (rows starting
  `B0...` in the Customer search term column).
- **Direction**: add.
- **Pitfall**: blocking too many ASINs reduces reach significantly;
  start with a handful of obvious mismatches.

## Bid-management levers

### 5. Raise per-keyword bid (offensive)

- **What**: increase bid on a winning keyword to capture more impressions.
- **Where**: same UI as lower bid (Targeting tab → bid cell).
- **When**: per-keyword ROAS > target_roas × 1.5, AND impression
  share is below ~50% (Amazon shows `Top-of-search impression share`
  in default columns). Indicates the keyword is winning but not
  showing enough.
- **Direction**: increase, in steps.
  - Soft: +15-20%
  - Standard: +30-50%
  - Aggressive: 2× (only on proven winners with ≥ 30 days of data)

### 6. Placement bid modifier (Top of search / Rest of search / Product pages)

- **What**: bump bid for a specific placement type when that
  placement converts well.
- **Where**: Campaign → Bid adjustments tab → click % cell → enter
  value → Save.
- **Range**: **0% to +900%, INCREASE-only**. Negative not allowed —
  Amazon's UI rejects it. Source: Amazon's own popup text "Choose a
  percentage between 0 and 900%".
- **When**: a placement (e.g. Top of search) shows ROAS ≥ target_roas
  AND CTR much higher than other placements, AND Amazon shows a
  `Recommended: X%` bump — apply the recommended or up to 2× the
  recommended.
- **Cannot do**: cannot use this lever to suppress a bad placement.
  For that, use lever #1, #3, #4, or change bidding strategy (#7).

### 7. Bidding strategy change

- **What**: change how Amazon's bid engine handles auto-adjustments.
- **Where**: Campaign settings → Campaign bidding strategy section.
- **Options** (radio):
  - **Fixed bids** — Amazon never adjusts; you typed it, that's it.
  - **Dynamic bids - down only** — Amazon lowers in real time when
    conversion looks unlikely. Conservative default.
  - **Dynamic bids - up and down** — Amazon raises *or* lowers up
    to ±100% based on real-time conversion likelihood. Aggressive.
  - **Rule-based bidding** — pursue a target ROAS; Amazon adjusts
    bids to hit it. Best when you have a clear ROAS goal AND enough
    conversion data for the rule to learn.
- **When to switch**:
  - Rule-based ROAS not being hit (actual ROAS < target ROAS) →
    lower the rule target OR switch to Dynamic - down only (more
    honest with thin data).
  - Dynamic - down only on a proven winner → consider Up and down.
  - Fixed on a high-variance campaign → consider Dynamic to flex.
- **Direction**: mode change.

## Grow-what-works levers

### 8. Search term harvest → exact

- **What**: take a search term that converted (in broad/phrase/auto
  match), promote to **exact match** with a controlled bid, AND add
  the same term as **negative exact** in the source ad group to
  prevent cannibalization.
- **Where (manual campaigns)**: Search terms tab → row with
  order(s) → Add as ⌄ → `Add as keyword` → select Exact match only.
  This adds to the **same ad group**.
- **Where (auto campaigns)**: Auto ad groups cannot have keyword
  targets. Harvesting from auto campaigns requires finding or
  creating a dedicated **harvest ad group** in a manual campaign.
  The ad group is named `<campaign-name-slug>-harvest-kw`
  — create it if it doesn't exist, reuse it if it does. Full
  workflow in `mechanics.md` § 8f (auto→manual harvest workflow).
- **When**: search_term_orders ≥ 1 AND ROAS ≥ target_roas × 1.5.
- **Direction**: add (positive keyword, exact).
- **Step 2 (negative back)**: For manual campaigns: same row →
  Add as ⌄ → `Add as negative exact`. For auto→manual harvest:
  add the term as negative exact at the **campaign level** (not
  the auto ad group). **Critical** — without this, the new
  exact-match keyword bids against your own auto targeting on the
  same query.
- **Pitfall**: the new exact-match starts cold (no Amazon historical
  data on it as exact); set bid at suggested-bid midpoint, not at
  the broad-match bid that produced the order.

## Budget lever

### 9. Daily budget change

- **What**: raise or lower the campaign's daily spend cap.
- **Where**: Campaign settings → Budget input → kat-numberinput →
  Save.
- **When to RAISE**:
  - Status = `Out of budget` (Amazon's signal that the campaign hit
    its budget cap recently).
  - Campaign ROAS ≥ target_roas (i.e. campaign is profitable).
  - **AND tune-before-scale rule has been satisfied**: the campaign
    has gone through a Phase 3 deep-dive and ACOS is at or below target.
  - Suggested raise: +30-50% if Amazon shows a `Recommended` value
    nearby, follow that; else conservative +30%.
- **When to LOWER**:
  - Wasteful campaign you don't want to pause but don't want to feed
    either; lower budget reduces exposure but doesn't fix the ACOS.
  - Generally prefer fixing the campaign (negatives, bid trims) over
    starving it.
- **Range**: positive currency, marketplace-specific minimum (often
  SAR 5 / AED 5 / USD 1).
- **Pitfall**: Amazon allows up to ~25% overspend on a single day,
  averaged across the month. So `daily_actual ≈ 1.0-1.25 × budget`
  is normal; "actual = exactly budget" doesn't mean throttled.

## Advanced levers (disabled by default — enable if user opts in)

### 10. Dayparting / scheduling (off by default)

- **What**: per-hour or per-day bid modulation.
- **Where**: Campaign settings → Schedule rules.
- **Why off by default**: most stores benefit more from straightforward
  search-term and bid tuning before time-of-day matters; dayparting
  adds complexity that's hard to evaluate.
- **When to enable**: user explicitly asks ("our orders cluster
  around specific hours of the day", "weekend lift on certain
  SKUs"), and the store has enough data to see the pattern
  (≥ 30 days, ≥ 50 orders).

### 11. Structural splits (rarely automated)

- **What**: split a high-spend keyword into its own campaign for budget
  control or A/B testing.
- **Why rarely automated**: structural changes are easier for the human
  to make in the UI directly; the skill recommends but does not auto-
  execute splits.

## Combination patterns (recipes)

### "Throttled but inefficient" pattern

Symptoms: Status = Out of budget, ACOS > target.

Recipe:
1. Lever 1 + 8 — search-term audit, negate losers, harvest winners.
2. Lever 3 — per-keyword bid trim on rows with ROAS < target.
3. Wait 7-14 days.
4. Re-measure ACOS.
5. If ACOS now ≤ target AND still Out of budget → Lever 9 (raise
   budget by Amazon's recommended).
6. If ACOS still > target → repeat steps 1-3, do NOT raise budget yet.

### "Placement imbalance" pattern

Symptoms: aggregate ACOS > target, but per-placement breakdown shows
one placement carrying most orders and another wasting spend.

Recipe:
1. **Cannot use placement modifier to suppress.** Modifier is +%-only.
2. Lever 7 — switch to Dynamic - down only if currently Fixed.
3. Lever 4 — for Product-pages waste specifically, add negative-ASINs
   for irrelevant competitor PDPs.
4. Lever 1 — for Rest-of-search waste, add negatives on the wasteful
   search terms (which usually trigger Rest-of-search placements).
5. Lever 6 — for the well-converting placement, ADD a positive
   modifier (e.g. Top of search +25%) to bias more bid budget there.

### "Funnel-broken" pattern

Symptoms: low CTR or low CVR vs store medians.

Recipe:
1. **Stop**. This is not an ad-tuning case. Surface to the user:
   "Campaign X has CTR 0.14% vs store median 1.8%. This is a listing
   image / title problem, not an ad bid problem. Lowering bids will
   reduce spend but won't fix the underlying issue."
2. Recommend fix the listing first (image, title, A+ content, price,
   reviews) — not an ad-side change.
3. As an interim, can pause the campaign to stop bleeding while
   listing is fixed; do not bid-trim and pretend that's tuning.

### "Winner needing fuel" pattern

Symptoms: ROAS ≥ target × 1.5, low impression share, healthy CTR.

Recipe:
1. Lever 5 — raise per-keyword bid on the winning keywords (+25-50%).
2. Lever 6 — placement modifier on the placement(s) where the winner
   is converting.
3. Lever 9 — raise budget if currently constrained.
4. Lever 8 — harvest any related search terms into more exact-match
   variants to lock in the win.
