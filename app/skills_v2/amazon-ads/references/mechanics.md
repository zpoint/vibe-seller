# Amazon Ads & Coupons — Mechanics (index)

Click paths, URLs, selectors and console quirks for
`advertising.amazon.<tld>` and the Seller Central promotions pages, all
confirmed with browser-use. Chinese labels are what a zh-CN account
shows; English labels are noted where the page is locale-mixed. This is
the "how to click" layer — what to decide lives in the tuning references,
and what to write down lives in [`data-contract.md`](data-contract.md).

The mechanics are split by job so you load only what you need:

| File | Load when |
|---|---|
| this file | Before your first ad-console call: preconditions, URLs, which marketplace you are looking at, field ranges, daemon recovery |
| [`mechanics-reading.md`](mechanics-reading.md) | Reading campaigns: enumerating the full list, bulk download, campaign/ad-group pages, Targeting and Search-terms extraction, Bid adjustments, Brand Analytics |
| [`mechanics-edit.md`](mechanics-edit.md) | Changing a live campaign: bids, pause/enable, negatives, harvesting search terms, placement %, bidding strategy, budget, SB/SBV edits |
| [`mechanics-create.md`](mechanics-create.md) | Creating a campaign or ad group in the UI (the fallback to [`bulk-operations.md`](bulk-operations.md)), SKU lookup, the bulk sheet schema |
| [`mechanics-coupons.md`](mechanics-coupons.md) | Coupons and the Manage promotions page |

## Contents

1. [Preconditions](#1-preconditions)
2. [URLs](#2-urls)
3. [Which marketplace are you looking at?](#3-which-marketplace-are-you-looking-at)
4. [Field input ranges](#4-field-input-ranges)
5. [Per-store conventions](#5-per-store-conventions)
6. [Recovering a wedged daemon](#6-recovering-a-wedged-daemon)
7. [Not covered here](#7-not-covered-here)

## 1. Preconditions

1. Load the `browser-harness` skill first. browser-use 0.13 takes Python
   helper code on a `<<'PY' … PY` heredoc; the pre-imported helpers are
   `new_tab`, `page_info`, `capture_screenshot`, `click_at_xy`,
   `wait_for_load`, `ensure_real_tab`, `js`, `fill_input`, `type_text`,
   `press_key`, `cdp`.
2. Call `browser-use` only through the store wrapper — a bare binary
   would attach to the user's own Chrome. The wrapper starts the
   browser on the first `new_tab(...)` if the CDP proxy is not up yet,
   so nothing needs pre-warming.
3. Read data from the DOM (or an export), not from screenshots. A
   screenshot is for checking layout, never the source of a number.

## 2. URLs

This table covers advertising and coupon URLs only. Seller Central
paths (inventory, listings, orders, reports) and marketplace TLDs live
in `knowledge/project/common/amazon-sites.md` and the `amazon-shared`
skill; don't guess them. On a Ziniao-gated store, enter the console
through Seller Central's own menu as `amazon-shared` describes; the
deep links below work once you are inside the console session.

Campaign pages answer on two prefixes: the campaign list's anchors and
the address bar use `/campaign-manager/sp/campaigns/…`, while in-page
tabs and ad-group links emit `/cm/sp/campaigns/…`. Both resolve (one
redirects to the other), so a prefix mismatch is not a broken link.

| Purpose | Working URL | Notes |
|---|---|---|
| Campaign manager | `https://advertising.amazon.<tld>/campaign-manager` | Amazon resolves a default `entityId` from the signed-in account. `/aiv/overview` is a stale redirect target that 404s. To deep-link further, scrape `entityId=ENTITY[A-Z0-9]+` from the landing page's HTML and append it. |
| Single campaign | `…/cm/sp/campaigns/<campaignId>?entityId=…` | The leg after `cm/` is the ad type: `sp`, `sb`, `sd`. |
| Ad group | `…/cm/sp/campaigns/<campId>/ad-groups/<agId>/<tab>?entityId=…` | tab ∈ `ads` / `targeting` / `negative-targeting` / `search-terms` / `history` |
| Campaign tabs | `…/cm/sp/campaigns/<id>/<tab>` | tab ∈ `ad-groups` / `bid-adjustments` / `negative-targeting` / `budget-rules` / `settings` / `history` |
| Bulk operations | `https://advertising.amazon.<tld>/bulk-operations?entityId=…` | Host root, no prefix. `/campaign-manager/bulk-operations`, `/cm/bulk-operations`, `/cm/bulk`, `/bulk/campaigns`, `/sp/bulk-operations` all 404. Some marketplaces have no Bulk Operations page at all — see [`mechanics-reading.md` § 2](mechanics-reading.md#2-bulk-download-the-account-wide-export). |
| Drafts | `…/cm/drafts?entityId=…` | Not `/cm/sp/drafts/…`. Also reachable from `View drafts` on the Campaign Builder. |
| Campaign Builder (create) | `https://advertising.amazon.<tld>/cb?entityId=…` | Host root `/cb`. "Choose your campaign type" with one Create button per card (Sponsored Products → `/cb/sp?entityId=…`). `/cb/sp/campaigns` and other `/cb/sp/*` deep paths 404. |
| Reports | `…/reports?entityId=…` | Can build a Sponsored Products Advertised Product report. |
| Promotions / coupons | `https://sellercentral.amazon.<tld>/manage/promotions` | `/coupons/dashboard` redirects here. See [`mechanics-coupons.md`](mechanics-coupons.md). |
| Coupon create | `https://sellercentral.amazon.<tld>/workflow/coupons/create` | 4-step wizard. Bulk coupons: `/coupons/bulk-upload`. |

**Sign-in.** The ad console redirects through Amazon sign-in on the
first hit of a session even when Seller Central is signed in. Follow
`amazon-shared` § 2: it is a challenge loop (password / OTP /
hosted passkey in whatever order Amazon picks), and Ziniao fills each
step it has stored. Its OTP panel and passkey overlay are native
windows, not page DOM, so coordinate-click them. If a step needs a
credential Ziniao does not hold, ask the user with `AskUserQuestion`;
never type or store a credential yourself.

**Entity ids.** A store's ad console can sit on a different Amazon
account from its Seller Central (different email and entity id — see
`amazon-shared` § 3). Read the entity id from the campaign-manager
landing page (`entityId=ENTITY[A-Z0-9]+` in the HTML) and reuse it on
every URL. Never copy a campaign URL across marketplaces: each
marketplace can have its own `entityId`.

**Campaign ids come in two forms.** The bulk export carries a numeric id
(`100000000001`); Campaign Manager links can show an `A`-prefixed id
(`A0EXAMPLE1EXAMPLE1EX`). They name the same campaign, and a SP detail
URL built from the numeric id resolves. If a detail URL 404s (SB/SBV
URLs need the alphanumeric form), search the campaign name in Campaign
Manager and read the id from the result link's `href`.

## 3. Which marketplace are you looking at?

The console comes in two shapes depending on how the account's
marketplaces are registered. Detect which one you are on before
trusting any list or export.

**Layout A — per-country account (one marketplace at a time).** The
top-right control reads `<account name>` over `Sponsored ads, <Country>`
and is a switcher. Clicking the outer button does nothing; click the
inner `Sponsored ads, <Country>` line (the `<p>` inside the button). A
panel titled "Sponsored Ads Accounts" lists one `<li>` per country, each
with the account display name and the country; the current one is a
plain `<div>`, the others are `<a>`. The panel also shows the ads
account id (`amzn1.ads-account.g.…`). Each entry is a separate
account/entity and its brand name can differ per country, so key on the
country, not the name. The campaign list and any bulk export cover only
the selected country.

To switch programmatically: find the leaf whose text matches
`^Sponsored ads,\s*<Country>$`, click it, click the target country's
entry, and wait for the list to reload before reading. A multi-country
audit on Layout A is one export per country: switch → wait → export →
repeat, labelling each file by country.

**Layout B — unified multi-market entity (one list, many countries).**
The chip reads `Sponsored ads, multiple countries/regions` (zh:
`搜索广告，多个国家/地区`). One campaign list spans several
marketplaces with a Country/Region column (`国家/地区`), and the bulk
export carries a marketplace/country column. The Campaign Builder's
country dropdown lists every country the entity covers. Confirm the
column is present and populated before relying on it.

**Telling them apart:** if the control names one country and no rows
carry differing Country values, it is Layout A; a Country column or
rows from several countries means Layout B.

Either way, a country missing from what is in view is not evidence that
it has no campaigns — on Layout A it is simply not selected, on Layout B
a filter excluded it. Put every country you were asked about in view
before concluding anything about it. A stored note saying a market is
empty is a snapshot from an earlier run; re-check it live.

Seller Central's own marketplace switch (account-switcher page,
`bh_switch_marketplace.py`) is documented in `amazon-shared` § 4a-bis.

## 4. Field input ranges

Know the valid range before proposing a value — an out-of-range
proposal is rejected at submit time and wastes the user's approval.

| Field | Where | Range | Direction | Unit | Note |
|---|---|---|---|---|---|
| Placement bid adjustment | Campaign → Bid adjustments → cell | 0 to 900 | Increase only | % | Amazon's popup: "Choose a percentage between 0 and 900%" |
| Keyword bid | Ad group → Targeting → bid cell | marketplace minimum (around 0.10–0.50 in local currency) to 1000 | Either | currency | Varies by marketplace |
| Daily budget | Campaign settings → Budget | marketplace minimum (around 5 in local currency) to 21000 | Either | currency | A bulk Campaign row below the minimum fails the whole upload |
| Default bid (ad group) | Ad group settings | marketplace minimum to 1000 | Either | currency | |
| Keyword match type | keyword editor | Broad / Phrase / Exact | enum | | |
| Negative match type | Search terms `Add as ⌄`, negative-keyword form | Exact / Phrase | enum | | Negative broad does not exist on SP |
| Bidding strategy | Campaign settings | Fixed / Dynamic down only / Dynamic up and down / Rule-based | enum | | |
| Rule-based ROAS target | Bidding strategy → Edit rule | positive, typically 0.5–10 | Either | ratio | |
| Coupon amount / budget | Coupon wizard | positive; amount depends on price | | currency | |
| Coupon duration | Coupon wizard | up to 30 days | | days | 31 fails |

A proposed numeric change states the current value and unit, the new
value and unit, the direction, and that the value is inside this range.
The bid floor and step size rules are in
[`tuning-recommendation-format.md` § Bid floor and step cap](tuning-recommendation-format.md#bid-floor-and-step-cap).

## 5. Per-store conventions

- **Naming.** Existing campaigns usually follow
  `<sku> <targeting type> <market>` (market = country code). A campaign
  the agent creates gets the suffix ` - agent` so the user can filter
  for it; add ` v2` / ` v3` after that only when Amazon rejects a name
  conflict.
- **Bidding strategy for a new launch:** `Dynamic - down only`
  (`legacy` in the form payload).
- **Placement adjustments:** 0% unless copied from a sister campaign.
- **Currency:** follows the marketplace. The form's
  `aria-label="enter amount in <CURRENCY>"` can name another currency;
  trust the marketplace.
- **Listing copy:** an ad run does not edit the listing. Amazon serves
  the listing's own title and bullets, so there is no ad copy to set,
  and copying a sister store's listing text is not part of ad work.

## 6. Recovering a wedged daemon

The wrapper already treats a 120 s timeout as a wedged daemon and
force-reloads it; on the second consecutive timeout it prints
`UNRECOVERABLE` and exits 75 — at that point stop retrying and report
the gap (`browser-harness` § Budget each invocation). If instead the
calls fail outright with `Client is stopping` (the daemon socket died,
not a slow call), re-issue a heredoc through the wrapper — the next
call recreates the daemon and, if needed, the CDP proxy:

```bash
browser-use <<'PY'
new_tab("<url>")
wait_for_load()
PY
```

If calls still fail, kill only the wedged per-store daemon
(`pkill -f "skill_cli.daemon.*<store>-<8hex>"`) and call again. The
Ziniao browser itself stays up. For a non-seller page (a public
Amazon page, docs) use the store's aux session as the system prompt's
dual-browser section describes; seller-center work stays in the main
session.

## 7. Not covered here

- What to tune and why → [`tuning-workflow.md`](tuning-workflow.md) and
  the other `tuning-*` references.
- Listing creation/edit, FBA shipments, account onboarding.
- Sponsored Display: not verified end-to-end.
