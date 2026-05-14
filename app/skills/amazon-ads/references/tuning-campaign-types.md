# Tuning per campaign type — observed differences

> **Reference of the `amazon-ads` skill.** See `../SKILL.md` for the
> catalog. Pair with `tuning-workflow.md` (Phase 3 branches on type)
> and `tuning-toolbox.md` (the 8 levers). Every claim here is from
> hands-on browser-use verification on a live merchant account
> running multiple types — `(verified)` markers note the specific
> page each fact was observed on; `(inferred)` markers flag claims
> derived from the campaign-list view but not yet drilled into.

The 5-phase workflow and the 8-lever toolbox are written generically.
But the **data shape**, **which tabs exist**, and **which levers
actually apply** differ by campaign type. This file is the per-type
lookup.

## How to identify the type

From a campaign-detail page, the **Type** pill near the page header
is authoritative. URL path also encodes it:
- `/cm/sp/campaigns/<id>` → Sponsored Products
- `/cm/sb/campaigns/<id>` → Sponsored Brands (incl. Brands Video)
- `/cm/sd/campaigns/<id>` → Sponsored Display

Inside SP, distinguish sub-types from the **Type** pill's exact text:
- `Sponsored Products - Automatic` → SP-Auto
- `Sponsored Products - Manual` → SP-Manual; then check the ad-group's
  Targeting tab — keyword rows mean Manual-Keyword, category rows
  mean Manual-Product (Category), `B0…` ASIN rows mean Manual-Product
  (ASIN).

Inside SB / SBV / SD, the Type pill is `Sponsored Brands` /
`Sponsored Display`. Video vs static creative is a per-ad-group
choice, not a separate campaign type.

## Campaign-list filter tabs (campaign manager)

Verified on `advertising.amazon.<tld>/campaign-manager`:
- **All** — every type
- **Sponsored Products** — SP only
- **Sponsored Brands** — SB only (including SBV; "Brands" tab covers
  both static and video creative)
- **Display, Video, & Audio** — auto-applies a filter; on accounts
  without SD this tab still shows SB campaigns whose creative is a
  video. Confirm campaign type from the **Type** column, not from
  the tab name.

## Sidebar tabs by type

What's present in the campaign-detail left sidebar (verified):

| Tab | SP-Auto | SP-Manual | SB | SBV | SD |
|---|---|---|---|---|---|
| Ad groups | ✓ | ✓ | ✓ | ✓ | (not verified) |
| Bid adjustments | ✓ | ✓ | ✓ (labelled "New") | (inferred ✓) | (not verified) |
| Negative targeting | ✓ | ✓ | **✗ at campaign level — only at ad-group level for SB** | (inferred same as SB) | (not verified) |
| Budget rules | ✓ | ✓ | ✓ | ✓ | (not verified) |
| Campaign settings | ✓ | ✓ | ✓ | ✓ | (not verified) |
| History | ✓ | ✓ | ✓ | ✓ | (not verified) |

What's present in the ad-group-detail left sidebar (verified):

| Tab | SP-Auto | SP-Manual | SB | SBV | SD |
|---|---|---|---|---|---|
| Ads | ✓ | ✓ | ✓ (with `Manage ad` link — SB ads have a creative) | (inferred ✓) | (not verified) |
| Targeting | ✓ | ✓ | ✓ | ✓ | (not verified) |
| Negative targeting | ✓ | ✓ | ✓ | ✓ | (not verified) |
| Search terms | ✓ | ✓ | ✓ | ✓ | (not verified) |
| Ad group settings | ✓ | ✓ | ✓ | ✓ | (not verified) |
| History | ✓ | ✓ | ✓ | ✓ | (not verified) |

## Type-specific data shapes

### SP — Auto Targeting (verified)

**Campaign-detail header pills**: Status / Type=`Sponsored Products - Automatic` / Country / Schedule / Budget.

**Campaign-detail top tiles** (4 fixed): Total cost / Sales / ROAS / Purchases.

**Ad-group level Targeting tab columns** (verified):
- Active toggle
- **Automatic targeting groups** ← this column header is the type giveaway
- Target match type (always shown blank `—` for Auto)
- Status (Delivering / Paused)
- Suggested bid (with `Apply` button per row)
- **Bid** (editable cell; column subtitle shows `0 rules active`)
- Clicks / Total cost / Purchases / Sales / ACOS

**Exactly 4 rows always**, in this UI order:
1. Close match
2. Substitutes
3. Loose match
4. Complements

(This **differs from how Amazon docs and the bulk-upload schema list
them** — close-match / loose-match / substitutes / complements. The
UI rendering order has Substitutes before Loose match. Don't hardcode
order; match by the row's group label.)

Each group has its **own bid**, independently tunable, independently
pause-able via the Active toggle (verified — saw "Loose match"
Paused while siblings Delivering).

**Levers that apply**:
- Lever 1 (negatives): ✓ at ad-group Negative targeting tab.
- Lever 2 (pause): ✓ via per-row Active toggle. Pausing a single
  auto-group is a real lever — saw it used on Loose match.
- Lever 3, 5 (per-row bid trim/raise): ✓ — each of the 4 rows is a
  tunable. Treat them as 4 mini-targets within the campaign.
- Lever 4 (negative ASIN): ✓ at campaign Negative targeting.
- Lever 6 (placement modifier): ✓ — Bid adjustments tab present.
- Lever 7 (bidding strategy): ✓ at Campaign settings.
- **Lever 8 (search-term harvest): the most important lever for
  Auto.** Auto campaigns are query-discovery engines; their value
  compounds when winners are harvested into Manual-Exact campaigns.

**Distinctive pattern: Auto-as-discovery, Manual-as-exploitation**.
Run Auto on a moderate bid for ongoing discovery. Periodically
harvest converters out into Manual-Exact for tighter control. Auto's
job is "find queries", Manual's job is "exploit known winners".

### SP — Manual Keyword (verified — see other tuning references)

This is what the rest of the skill describes by default. See
`tuning-workflow.md` Phase 3 numbered steps and the worked example
in `tuning-recommendation-format.md`.

**Ad-group level Targeting tab columns**: Active / Keyword / Match
type (Broad / Phrase / Exact) / Status / Suggested bid / Bid / Clicks
/ Total cost / Purchases / Sales / ROAS / ACOS / CPC.

All 9 standard levers apply. This is the most-tunable type and the
default of the skill.

### SP — Manual Product (Category / ASIN) (inferred from creation-flow + listing)

Same campaign URL path (`/cm/sp/campaigns/<id>`) as SP-Manual-Keyword.
Distinguishable inside the ad-group's Targeting tab — rows are
categories or `B0…` ASINs instead of keywords.

**Levers that apply** (same surface as SP-Manual-Keyword, so all 9
work in principle):
- Lever 1 (negatives): negative-keyword applies for Category
  targeting (where queries get matched into the category); for ASIN
  targeting, negative-ASIN is the more relevant lever.
- Lever 8 (search-term harvest): less applicable for ASIN targeting
  (the "term" is usually the targeted ASIN itself); applies for
  Category targeting (queries within the category are harvestable).

**Field-verification status**: campaign creation flow verified
(`mechanics.md` § 3e). Tuning surfaces inferred to match SP-Manual-
Keyword based on shared URL path and tab structure; per-row data
columns not yet captured on a live Manual-Product campaign.

### SB — Keyword (verified)

**Campaign-detail header pills** (different from SP): Status / Type
=`Sponsored Brands` / Country / Schedule / Budget / **Goal=`Drive
page visits`** / **Cost type=`CPC`**.

The **Goal** and **Cost type** pills are SB-specific. Goal is set at
campaign creation (`Drive page visits` vs `Drive sales`); Cost type
is `CPC` or `vCPM`. Both influence how Amazon optimizes — tunable
levers that don't exist on SP.

**Campaign-detail top tiles** (configurable, default visible):
Total cost / Sales / ACOS / Impressions, with a `+ Add Metric`
button to add NTB metrics, viewable impressions, etc. (SP's tiles
are fixed.)

**Ad-group "Ads" tab columns**: Active / Ad name / Status / **Landing
page** (`View landing page` link) / Clicks / Cost / NTB-related
columns / Sales / ROAS-related columns. SB ads have a creative
(headline, image / video, landing page) — `Manage ad` link below
the row name opens the creative editor.

**Ad-group "Targeting" tab columns** (verified):
- Active toggle
- Keyword
- **Match type** (Phrase / Exact / Broad — all three observed on
  SB-Kw and SBV. Earlier skill notes claimed "no Broad on SB"
  based on one campaign; that was per-campaign, not account-wide.)
- Status
- Suggested bid (often shows `No current data`)
- **Keyword bid** (editable currency cell; label is `Keyword bid`,
  not just `Bid` as on SP)
- **Viewable impressions** ← SB-specific column (vCPM-style metric)
- Clicks / Click-through rate / Spend / Cost-per-click (CPC) /
  Orders / Sales / ACOS / Returns

**Levers that apply**:
- Lever 1 (negative keyword): ✓ at ad-group Negative targeting tab.
  Campaign-level Negative targeting tab is **absent** on SB.
- Lever 2, 3, 5 (pause / bid down / bid up): ✓ standard.
- Lever 4 (negative ASIN): not at the keyword-targeted ad-group
  (irrelevant target type); applies to SB-Product ad groups.
- Lever 6 (placement modifier): SB has a Bid adjustments tab (with
  a "New" badge as of Apr 2026 — column / cell semantics not yet
  drilled into). Default skill stance: load the tab and read what's
  there before recommending; don't carry over assumptions from SP.
- Lever 7 (bidding strategy): SB doesn't expose Fixed / Dynamic-down
  / Dynamic-up-and-down / Rule-based the way SP does. The Goal +
  Cost type pills are the equivalent campaign-level optimization
  selector. Changing Goal or Cost type may require the campaign
  to be paused (not yet verified).
- Lever 8 (search-term harvest): ✓ — Search terms tab exists at
  ad-group level. Harvesting from SB queries into SP-Manual-Exact
  is a known good pattern (don't harvest SB→SB-Exact; that
  over-bids on brand-related queries).

**Distinctive lever — creative refresh** (SB-only): SB has a
creative (headline copy, hero image, video). Underperformance often
traces to *creative*, not bid. Diagnostic: low CTR despite reasonable
match-type / bid → creative is stale. Surface to the user; the
skill doesn't auto-edit creatives.

**Distinctive metric — NTB (New-To-Brand)**: SB tracks NTB orders /
NTB sales / NTB %. SB campaigns are valued partly for new-customer
acquisition (NTB) on top of ROAS. A "growth" SB campaign should be
allowed a higher ACOS if NTB% is healthy.

### SB — Product (inferred)

Same campaign URL (`/cm/sb/...`) as SB-Keyword but the ad-group
Targeting tab rows are ASINs / categories instead of keywords.
Same NTB metrics, same Goal / Cost type levers, same creative
considerations.

**Field-verification status**: not yet drilled into on a live
SB-Product campaign. Inferred from SB-Keyword + general SB structure.

### SBV — Sponsored Brands Video (inferred — same surface as SB)

Catalogued as `Sponsored Brands` in the Type column; the "video" is
just a creative format inside the SB ad-group. URL path is
`/cm/sb/...` same as SB-Keyword. Distinguished by the ad's creative
(video-format) and naming convention (campaigns often suffixed with
" video" in the user's own naming scheme).

**Distinctive metrics**: 5-second view rate, through-play rate,
click rate from video tile (these become available as "Add Metric"
options on the campaign-detail tiles). Not yet captured live.

**Distinctive lever — video creative tuning**: SBV CPC tends to be
higher than SP CPC. The lever that matters most is the video itself
(opening hook, length, captions). Diagnostic from low view-rate +
low click rate suggests the video isn't engaging.

**Field-verification status**: campaigns observed in the listing
(named `…video` by the user); per-creative-metric tab not drilled
into.

### SD — Sponsored Display (not verified on this account)

The merchant account browsed during this verification pass did not
have visible SD campaigns under the `Display, Video, & Audio` tab —
that tab returned only SBV campaigns. SD coverage in this skill is
**not field-verified**; mark Confidence Low for any SD-specific
recommendation and bias to conservative reversible actions.

## Summary lever-applicability matrix

✓ verified, ~ verified with caveats, ✗ verified absent, ? not yet
verified, **(default skill assumes SP-Manual-Keyword):**

| Lever | SP-Auto | SP-Manual-Kw | SP-Manual-Product | SB | SBV | SD |
|---|---|---|---|---|---|---|
| 1. Negative kw / phrase | ✓ | ✓ | ~ (kw for Cat, n/a for ASIN) | ✓ at ad-group only | ? | ? |
| 2. Pause keyword / target | ✓ (per auto-group) | ✓ | ✓ | ✓ | ? | ? |
| 3. Lower per-target bid | ✓ | ✓ | ✓ | ✓ | ? | ? |
| 4. Negative ASIN | ✓ | ✓ | ✓ | n/a for Kw, ✓ for Product | ? | ? |
| 5. Raise per-target bid | ✓ | ✓ | ✓ | ✓ | ? | ? |
| 6. Placement modifier | ✓ | ✓ | ✓ | "New" tab present, semantics not verified | ? | ? |
| 7. Bidding strategy | ✓ (Fixed/Dyn-down/Dyn-up&down/Rule-based) | same | same | ✗ — replaced by Goal + Cost type | ? | ? |
| 8. Search-term harvest → exact | ✓ (most important on Auto) | ✓ | ~ (Cat yes, ASIN no) | ✓ — harvest to SP-Exact | ? | ? |
| 9. Daily budget | ✓ | ✓ | ✓ | ✓ | ✓ | ? |
| **Type-specific (not in 8)** | — | — | — | Goal change, Cost-type change, Creative refresh | Video creative recut | Audience refinement, vCPM/CPC switch |

## Verification status — at a glance

| Type | Campaign list | Campaign detail | Ad-group detail | Targeting tab |
|---|---|---|---|---|
| SP-Auto | ✓ | ✓ | ✓ | ✓ (4 auto-groups) |
| SP-Manual-Keyword | ✓ | ✓ | ✓ | ✓ |
| SP-Manual-Product (Cat / ASIN) | ✓ | inferred | inferred | not drilled |
| SB-Keyword | ✓ | ✓ | ✓ | ✓ |
| SB-Product | ✓ | inferred from SB-Kw | inferred | not drilled |
| SBV | ✓ | inferred (same as SB) | inferred | not drilled |
| SD-Audiences | not seen on this account | — | — | — |
| SD-Product | not seen on this account | — | — | — |

When recommending actions on a row not marked ✓, **note the
verification gap** in the recommendation's Confidence field.
