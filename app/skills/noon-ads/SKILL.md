---
name: noon-ads
description: "Noon Ad Manager — campaigns (Auto / Manual / Brand), tuning audits, keyword research, negatives, Vantage analytics. Covers Targets / Customer Queries / Export Data flows. Load for any noon ads work — review, audit, tune, create. References under references/ for the tuning playbook (ads-tuning.md), creation guide (ads-creation.md), and keyword research (ads-keyword-research.md)."
requires: [noon-shared]
---

# Noon — Ad Manager

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, and common patterns.

Mechanics for noon Ad Manager. The actual *thinking* — when to
create / tune / kill a campaign, how to research keywords — lives
in the three reference files (see § 11).

**URL**: `https://admanager.noon.partners/en-{cc}/home?mpCode=noon&project=PRJ{project_id}`

Left nav (`ul role=menu`): **Campaigns**, **Budget**, **Billing**,
**Vantage** (Beta), **Settings**. Country switcher at bottom.

## 1. Campaigns Overview Metrics

Top-level KPI cards:

| Metric | Formula / Notes |
|--------|-----------------|
| ROAS | Return on Ad Spend = Revenue / Spends |
| Revenue | Total ad-attributed revenue |
| Spends | Total ad spend |
| eCPC | Effective Cost Per Click |
| CTR | Click-Through Rate = Clicks / Views |
| Orders | Ad-attributed orders |
| Clicks | Total clicks on ads |
| Views | Total ad impressions |
| ATC | Add To Cart count |
| CvR | Conversion Rate (shown on campaign detail) |

A time-series performance chart below lets you toggle any metric.

## 2. Campaign List Filters

- Status (Active/Paused/etc.)
- Ad Type
- Date range

Columns: Performance (chart), ROAS, Revenue, Spends, eCPC, CTR,
Orders, Campaign Details, Status, Budget.

## 3. Campaign Detail Page

**URL**: `admanager.noon.partners/en-{cc}/campaign/details/{campaign_id}?mpCode=noon&project=PRJ{project_id}`

Campaign ID format: `C_{alphanumeric}` (10 alphanumerics after the
underscore, e.g. `C_XXXXXXXXXX`).

**Campaign ID extraction** from the campaigns list (IDs are in
`<a href>` attributes, not visible text):
```bash
browser-use eval "var links = document.querySelectorAll('a[href*=\"/campaign/details/\"]'); var data = []; links.forEach(function(l) { var m = l.href.match(/\/campaign\/details\/([^?]+)/); if (m) data.push({name: l.textContent.trim(), id: m[1]}); }); JSON.stringify(data);"
```

**Campaign names can be misleading.** Verify actual products via
the **Products tab** — do not trust the campaign name. A campaign
named "boxer004 Auto" may target women's underwear SKUs, not boxers.

Header shows: campaign name, Status badge, Budget, **Top-of-Search
boost** (displayed as `Top Slot: N%` between Budget and Bidding
Strategy for manual campaigns with TOS configured), Bidding
Strategy, Running From date, Last Updated.

**Brand Ads have different CTR/ROAS norms.** Brand Video ads measure
view-through differently — never compare CTR directly to Product Ads.
A `brand video` CTR of 0.23% is not "weak" vs product ad peers at
1.7–6.7%. Compare Brand Ads only against other Brand Ads, or against
the brand ad's own historical ROAS.

**Brand Ad "Creative" row.** The Products tab may show a "Creative"
row (logo/video element) that accumulates clicks with zero attributed
orders — noon can't attribute conversions to the creative. If
Creative spend > 20% of campaign budget, flag it; calculate ROAS
both with and without the Creative row for true product performance.

KPI cards (same 10 metrics as overview, scoped to this campaign).

Performance chart with metric toggles: ROAS, Revenue, Spends, eCPC,
CTR, CvR, Orders, Clicks, Views, ATC.

**4 Sub-tabs on campaign detail:**

| Tab | Purpose |
|-----|---------|
| Products | SKUs in this campaign with per-SKU metrics |
| Placements | Ad placement performance |
| **Targets** | **Keywords** with match types and bids |
| Customer Queries | Actual customer search terms |

**Sub-tab access patterns.** Element indices for the 4 sub-tabs
are **not stable** — they change between page loads. After scrolling
down ~800px, the Products tab is reliably at a stable index in
`browser-use state` output. For other tabs, look for the tab name
in the state output and click the adjacent index. For **brand video
pages**, scroll 1500px (roughly 2× standard) because the embedded
video player pushes sub-tabs further down.

**"No SKUs Found" on Products tab.** If a campaign is Live and
spending but Products shows "No SKUs found", the linked SKUs were
deleted or delisted. The Auto system continues to spend but cannot
attribute revenue. Check if some variants show "View Issues" vs
"Buy Box Won" — even one broken variant can tank campaign ROAS.

## 4. Targets Tab — Keywords & Bidding

**Goal**: capture every row, including the 0-view tail (typos,
sub-floor bids, idle keywords).

**Default to scroll+eval.** For typical Manual campaigns
(15–30 keywords) the DOM accumulates all rows on initial render;
a single `eval` walking `document.querySelectorAll('table tr')`
returns the full table. Verified live 2026-05-05: campaigns
with 24–25 keywords returned every row on first eval, no
scrolling needed.

```bash
browser-use eval 'JSON.stringify(Array.from(document.querySelectorAll("table tr")).map(r => Array.from(r.cells).map(c => c.innerText.trim())))'
```

If the captured count looks small (<10 rows on a 14d+ campaign
that should have 15+), scroll the inner table container and
re-eval — the DOM may still be virtualizing on a slow render.

**Export Data → CSV is unreliable in this environment.** Field-
verified: clicking the Export Data button on a campaign-detail
Targets tab produced no CSV in `~/.vibe-seller/downloads/<slug>/`
within 10 s; the download monitor only catches bulk-sheet
exports, not per-campaign tab exports. Use Export Data only as
a last resort for campaigns with ≥ 50 keywords AND only after
verifying a fresh file appears in the downloads dir.

**Tab-activation gotcha.** After `browser-use click <targets-tab-idx>`,
verify by URL — `location.href` should include `?tab=targets`.
The `aria-selected` state can lag for a second after click and
isn't a reliable activation signal.

**Recommended-Bid cell suffix.** The Recommended Bid column
extracts as e.g. `0.75 0.60-0.90 Apply` — the literal "Apply"
button label is concatenated into the cell innerText. Strip
the trailing `Apply` before formatting in the report.

Columns: Target (keyword text), Bid, eCPC, Recommended Bid,
Verticals, Engagement (Views/Clicks/Orders/CTR/CvR), Status.

**Match types** observed: `exact Match`, `phrase Match` (and likely
`broad Match`).

Per-keyword actions:
- **Bid input**: edit target bid directly
- **Apply** button: applies recommended bid
- **Status toggle**: enable/disable the keyword

## 5. Change Target / Keyword Price

On the Targets tab, the Bid column is directly editable:
```bash
browser-use state                      # find bid input for target row
browser-use input <bid-input> "2.50"
# Confirmation/save happens per-row
```

Or click "Apply" next to Recommended Bid to use noon's suggestion.

## 6. Customer Queries Tab

Same scroll+eval default as § 4 (Export Data is unreliable in
this environment — see § 4 note).

```bash
# After clicking the Customer Queries tab, verify activation by URL
browser-use eval 'location.href.includes("tab=customerQuery")'
# Then walk the table rows
browser-use eval 'JSON.stringify(Array.from(document.querySelectorAll("table tr")).map(r => Array.from(r.cells).map(c => c.innerText.trim())))'
```

**Rendering delay.** After clicking the Customer Queries tab, the
first `eval` may return only an empty header row (no data). This is
a rendering delay — the table is in the DOM but data hasn't
populated. A second eval ~2 seconds later returns full data.

The on-screen state shows only the top spenders; the long-tail
/ 0-order queries where harvest, brand-negate, and waste
decisions live are below the fold. The eval above pulls the
full table on most campaigns; if you see fewer than ~15 rows on
a 14d+ campaign, scroll the inner table container and re-eval.

Shows the actual search terms customers used that triggered your ads.
Columns: Customer Query Term, Target, Match Type, Target Bid, eCPC,
Spends, Verticals, Engagement.

**Auto campaign query routing per-product.** On Auto campaigns,
the Customer Queries tab shows queries scoped to the product
currently selected/highlighted in the Products tab — NOT the full
campaign. If a campaign has 2+ products, switch the highlighted
product to see each product's queries. Always check Customer Queries
while each product is individually selected to capture all routes.

**Auto campaigns: Customer Queries IS the tuning surface.**
Auto campaigns have no Targets tab, so the Customer Queries tab
is where most of the actionable items live (brand-negates,
wrong-category waste, harvest candidates). Allocate equal time
on Customer Queries for Auto as you would on Targets for
Manual — don't treat Auto sections as "lighter" just because
the spec template doesn't show a Targets table.

Use this to discover high-performing queries (add as keywords) or
low-performing queries (add as negatives).

## 7. Export Data

Both Targets tab and Customer Queries tab have **Export Data** button
at top-right. Triggers CSV download of the current filtered view.
**Unreliable in this environment** — see § 4 caveat. Prefer DOM eval
extraction.

```bash
browser-use state                      # find "Export data" button
browser-use click <export-data-btn>    # downloads CSV (may not land)
```

Campaign detail also has Export Data for the Products tab.

## 8. Create Campaign Flow

**URL**: `admanager.noon.partners/en-{cc}/campaign/start?mpCode=noon&project=PRJ{project_id}`

### Step 1/3 — Ad Type

Two radio options:
- **Product Ads** — Increase product visibility by targeting
  relevant search terms and browsing categories
- **Brand Ads** — Boost brand discovery with ads that showcase
  your logo, brand name and products

Click Continue.

### Step 2/3 — Product Selection + Bidding + Targeting

**1. Product Selection**: Manual Selection OR Bulk Upload
- Search by SKU name input
- Selected products shown in right panel

**2. Bidding Strategy** (choose one):

| Strategy | Behavior |
|----------|----------|
| **Dynamic Bid Up & Down** (New) | Scale up for top placements, down during low conversion. Auto Targeting only. |
| **Dynamic Bid Down Only** | Only lowers bid when conversion is low. Auto + Manual Targeting. |
| **Fixed** | Set default bid amount; no dynamic adjustment. |

**3. Targeting**:
- **Auto Targeting**: noon automatically matches ads with relevant
  parameters. Configure **Default Bid Amount** and **Minimum Bid**.
- **Manual Targeting** (with supported strategies): pick keywords.

**4. Negative Targeting (Optional)**: Exclude specific keywords to
prevent your ad from appearing in irrelevant searches. Limits:
**30 Days negative targets** and **30 Phrase negative targets**.

**5. Top Of Search Placement Bidding (Optional)**: Increase chances
of appearing at top of search results. Bid Percentage boost up to
**900%** to compete for premium placements.

**6. General Settings**:
- Campaign Name (required)
- Marketplace (auto: NOON)
- Start Date / End Date (checkbox "No end date")
- **Budget Details**:
  - Shared Budget — distribute across multiple campaigns
  - Campaign Budget — dedicated to this campaign
- **Maximum Daily Budget** input

Action buttons at bottom: **Cancel & Go Back**, **Save As Draft**,
**Launch Campaign**.

## 9. Add Negative Keywords to Existing Campaign

Open Campaign Detail → Targets tab. The Targets tab manages positive
keywords. For negatives, look for a "Negative Targets" section or
sub-tab on the same page (noon UI varies; explore the tab headers).

When creating a new campaign, use step 4 "Negative Targeting" above.

## 10. Vantage Analytics

**URL**: `https://vantage.noon.partners/en/?project=PRJ{project_id}`

First visit asks to select country (UAE / KSA / EGY) and account.
Provides deeper analytics across campaigns.

## 11. Reference catalog — "what to do" thinking

The mechanics in §1–§10 above are click paths. The actual
*thinking* — when to create a campaign, when to tune an existing
one, how to research keywords — lives in three reference files:

| Reference | Load when |
|---|---|
| [`references/ads-creation.md`](references/ads-creation.md) | Creating a new campaign. Covers targeting choice, bidding strategy, per-keyword bid heuristic, match-type strategy, negative scoping, TOS boost rules, budget choice, the Save-as-Draft → Launch UI quirk, naming convention, post-launch verification cadence. |
| [`references/ads-tuning.md`](references/ads-tuning.md) | **Any task that reads existing campaigns and proposes changes**, including phrasings like "review all ads", "audit the campaigns", "give me an improvement plan", "weekly ad review", "tune ads", "fix ACOS / ROAS". Defines the steps, the data to capture, and the canonical recommendation output format (header table → per-campaign sections → Problem-N subsections). |
| [`references/ads-keyword-research.md`](references/ads-keyword-research.md) | Building the initial keyword list for a Manual campaign. Covers buyer-vs-seller language, storefront autocomplete (English + Arabic), peer-listing reading, cross-checking against existing campaigns to avoid self-competition, parallel negative-list build, match-type assignment. |

Safety rails:

- **Compare same-country with same-country.** KSA and UAE buyers
  behave differently; UAE peer data isn't a fair baseline for a
  KSA campaign or vice versa.
- **Surface, don't auto-execute.** Recommendations are presented
  to the user with current value, proposed value, and reason. The
  user confirms before any state-changing click.
- **Per-run captures → `/tmp/<run-slug>/`.** Live data captures
  go to a temp dir, never under `~/.vibe-seller/knowledge/`.

## Tips

- **Ad Manager is per country** (`/en-sa/` vs `/en-ae/`).
- **Campaign Detail tabs**: Products / Placements / Targets / Customer Queries.
- **ROAS** = Revenue / Spends, target > 1.0 minimum (but real
  scale-target depends on margin — see `ads-tuning.md`).
- **Export Data** buttons exist on Products, Targets, Customer Queries tabs.
- **Negative keyword limits**: 30 Day negatives + 30 Phrase negatives per campaign.
- **Top-of-search bid boost**: up to 900%.
- **Session timeout recovery**: during long audits (7+ campaigns,
two countries), `browser-use state` may return timeout. Recovery:
`browser-use open <any_admanager_url>` reconnects without needing
`browser-use close`. Login state is preserved.
- **Export Data is unreliable**: clicking Export Data on Targets or
Customer Queries tab may not produce a CSV in
`~/.vibe-seller/downloads/<slug>/`. Use DOM eval extraction
instead (§ 4 pattern).

### Don't trust an "empty" Ad Manager that contradicts the store profile

noon's Ad Manager has been observed returning a **transient empty
state** that the UI faithfully renders as
*"No data available / Showing 0 items per page"*, even when the
store has 5+ active campaigns in that country (verified by
re-navigating the same URL ~30 minutes later — same
"Last Updated" timestamp, very different result). This isn't a
client-render race — the page literally says zero campaigns —
but the page is wrong.

**The store profile is the durable ground truth.**
`stores/<slug>/metadata.json` carries
`platform_countries.noon` and `notes.md` documents prior-run
campaigns. When the live Ad Manager for a country listed there
shows zero campaigns, that's a contradiction — treat it as a
transient UI/backend issue, not a fact about the store.

When that contradiction fires, in order:

1. **Re-navigate the same URL** (full `browser-use open`, not
   just refresh — open a fresh navigation). If campaigns show
   up: trust them, audit, move on.
2. **Check the on-page filters.** noon's overview has Status and
   Ad Type dropdowns — clear them and re-read.
3. **Open the country's `/campaigns` page directly** instead of
   `/home`; the home view is more cache-prone.
4. Only after all three return zero with a fresh "Last Updated"
   timestamp may you report the country as actually empty —
   and even then, surface the contradiction with the store
   profile so the user can resolve it.

Dropping a country that the store profile lists as active is a
much worse failure than spending 60 extra seconds verifying.

## See also

- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — promote a SKU you've just listed
