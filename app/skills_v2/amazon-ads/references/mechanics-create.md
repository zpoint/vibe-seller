# Amazon Ads — creating campaigns and ad groups in the UI

The UI create flow. The default path for creating a campaign is the bulk
round trip in [`bulk-operations.md`](bulk-operations.md); use these
clicks for a single campaign, a field the bulk sheet cannot express, or
after two failed imports. Launching spends money, so launch only what
the user approved (save as draft, show it, launch on their go-ahead).

## Contents

1. [Open the form](#1-open-the-form)
2. [Choose the country](#2-choose-the-country)
3. [Form-field map (Sponsored Products)](#3-form-field-map-sponsored-products)
4. [Product search](#4-product-search)
5. [Date picker](#5-date-picker)
6. [Keywords (manual keyword targeting)](#6-keywords-manual-keyword-targeting)
7. [Categories (manual product targeting)](#7-categories-manual-product-targeting)
8. [Auto-targeting groups and negatives](#8-auto-targeting-groups-and-negatives)
9. [Save, launch, and fix validation errors](#9-save-launch-and-fix-validation-errors)
10. [New ad group in an existing campaign](#10-new-ad-group-in-an-existing-campaign)
11. [Verify the Product Ad after launch](#11-verify-the-product-ad-after-launch)
12. [SKUs and the bulk sheet schema](#12-skus-and-the-bulk-sheet-schema)

The SP create page is one long form, not a wizard: Launch and Save as
draft both submit the whole form.

## 1. Open the form

Open the Campaign Builder at `https://advertising.amazon.<tld>/cb?entityId=ENTITY…`
(also the plain link at the bottom of the all-campaigns page). It shows
a country selector top-left, the H1 "Choose your campaign type", a
`View drafts` button, and three cards — Sponsored Products / Sponsored
Brands / Display, Video, and Audio — each with its own `Create campaign`
button (Sponsored Products → `/cb/sp?entityId=…`).

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.<tld>/cb?entityId=ENTITY…")
wait_for_load()
# then click_at_xy the Sponsored Products card's "Create campaign"
PY
```

`/cb/sp/campaigns?entityId=…` and other `/cb/sp/*` deep paths render
"Page not found". The left-rail `button[aria-label="Create campaign"]`
opens a flyout (Campaigns / Ad groups / Targets) whose rows did not
navigate reliably; prefer the Campaign Builder page.

## 2. Choose the country

The country control sits top-left of the Campaign Builder. It comes in
two shapes; read the DOM rather than guessing from the URL:

```text
# A) entity registered for several marketplaces
<button aria-label="<current-country-name>">        # clickable
  <button value="<CC1>" role="option" selected="false">Country One</button>
  <button value="<CC2>" role="option" selected="true">Country Two</button>

# B) entity registered for one marketplace
<button aria-label="<country-name>" disabled="true">  # not clickable
```

1. Read the trigger's attributes.
2. `disabled="true"` (shape B): this entity can only advertise in the
   country shown. If that is not the task's country, stop and tell the
   user — the fix is not on this page.
3. Clickable (shape A): if its `aria-label` is not the target country,
   click it, click the option whose `value` is the target ISO code, and
   re-read the `aria-label` to confirm.

In shape A the dropdown, not the URL's TLD, decides the country; the TLD
is only where this entity's console lives.

## 3. Form-field map (Sponsored Products)

Select by `id` — ids are stable across locales; placeholders and the
currency in `aria-label="enter amount in <CURRENCY>"` are not.

| Field | Selector | Notes |
|---|---|---|
| Ad group name | `#sspa_sp_adGroupSettings_adGroupName` | Pre-filled `Ad Group - <timestamp>`; the JS setter persists. |
| Campaign name | `#fieldSet\.campaignSettingsFieldSet-AtlasCoreComponents\:CampaignName\:Input` | Pre-filled `Campaign - <timestamp>`. The setter does not survive a direct launch — see § 9. |
| Product search | `#ucb-sp-ups\:ups-asin-search-input` | Typing fires the typeahead (no Enter). See § 4. |
| Default bid | `#sp-defaultBid` | Pre-filled with Amazon's suggested bid. |
| Daily budget | `#fieldSet\.campaignSettingsFieldSet-AtlasCoreComponents\:CurrencyInput\:Input` | Plain number, marketplace currency. |
| Targeting type | `input[type=radio][name=targetingType][value=AUTO\|MANUAL]` | |
| Manual sub-type | `input[type=radio][name=manualTargetingType][value=KEYWORD\|PRODUCT]` | |
| Auto sub-type | `input[type=radio][name=automaticTargetingType][value=DEFAULT\|TARGETING_GROUP]` | `TARGETING_GROUP` shows 4 per-group bids. |
| Bidding strategy | `input[type=radio][name=biddingStrategy][value=optimizeForSales\|legacy\|manual]` | `legacy` = Dynamic - down only. |
| Start / end date | inner input of `kat-date-picker[name=start-date]` / `[name=end-date]` | See § 5. |
| Save as draft | `#sspa_sp_save_as_draft` | Persists without launching. |
| Launch | `#sspa_sp_createCampaign` | Final commit — only after the user approves the draft. |

## 4. Product search

**Flip to `All products` first.** Above the results are two
`role=switch` toggles, `Suggested` (on by default) and `All products`
(labels localized). With `Suggested` on, the grid ignores the search
input and shows other products, which looks like a failed search.

**Scroll the results grid, not the page.** The panel
`<div aria-label="grid">` is virtualized: the header can say
`1-35 of 35 results` while about 15 rows are in the DOM, so a page
scrape concludes the product "isn't there".

```js
const grid = document.querySelector('div[aria-label="grid"]');
grid.scrollTop = grid.scrollHeight;   // then re-read its rows
```

Repeat until `scrollTop + clientHeight >= scrollHeight`, collecting
SKUs/ASINs as you go; `window.scrollTo` does not move this viewport.

**Parse rows by anchored fields.** A row's `innerText` is
`<title>\n(<rating>)\n\n<currency> <price>\n\nIn stock\n\nASIN:<asin>\n\nSKU:<sku>\n\n<eligibility>\n<brand>`,
and a greedy `/SKU:([^\s]+)/` can swallow the next fields
(`<sku>IneligibleBRAND`). Use `/SKU:\s*([A-Za-z0-9._-]+)\b/` and read
`Eligible`/`Ineligible` and `In stock`/`Out of stock` as separate fields.

Each result row has an `添加` (Add) button; walk up to ~8 parents from
the desired ASIN to find its row. The right pane (`N 个商品`, N products
selected) lists each added item with brand, price, ASIN and SKU.

**The added product carries over between campaigns** in the same
session. When creating several campaigns in a row (one per SKU), the
right-pane card can still hold the previous campaign's product while
you change only the name and keywords. Before each launch, confirm the
card shows this campaign's SKU (remove a carried-over one), and check
again after launch (§ 11).

## 5. Date picker

`kat-date-picker` stores `MM/DD/YYYY` but displays `DD/MM/YYYY`, and a
value set on its inner input is cleared on the next re-render. Open the
picker by clicking it, then click the day button whose `innerText.trim()`
equals the day and whose rect lies inside the open overlay (roughly
y 200–400, x 100–400 for start; x 800–1000 for end). The input then
reflects the date. If the click does not take, Tab out of the field.

## 6. Keywords (manual keyword targeting)

**Open the keyword textarea.** There are two `输入列表` (Input list)
tabs — product search above, keywords below. Collect both, take the
lower one (larger y). The textarea
`textarea#kwp\:kwp-enter-list-text-input-area` has a zero rect until
that tab is clicked, so `scrollIntoView({block:'center'})` the tab and
`click_at_xy` it (a `js()` click on the wrong span silently fails), then
confirm the textarea's rect is non-zero.

**Enter the keywords with real keystrokes:** `click_at_xy` the textarea,
`type_text` each keyword with `press_key("Enter")` between them, then
click `添加关键词` (Add keywords). A `js()` `.value` set is reverted by
React; if you must script it, use the `execCommand('insertText')`
workaround in [`mechanics-edit.md` § 5](mechanics-edit.md#5-the-react-textarea-workaround).

**Match types.** The checkboxes `广泛` / `词组` / `精准` (Broad / Phrase
/ Exact) move after each batch (y≈1990 at first, y≈490 after a re-flow),
so match them by label text and click the surrounding `<label>` —
`kat-checkbox` swallows clicks on the inner input. One add with all
three ticked usually creates broad + phrase + exact rows for every
keyword; if you see duplicates or skips, add one match type per batch
(untick the others, add, dismiss any modal, repeat).

**"No suggested bid" modal.** When a keyword has no suggested bid,
Amazon opens `<keyword>-精准 没有建议竞价 / 选择备选竞价` with a fallback
bid input and a button matching `^添加\s*\d*\s*个?关键词$`. It blocks the
following adds until dismissed — undismissed, the next batches are
silently dropped. Check for it after every batch and click that button.

**Per-keyword bids** (aria-label `针对关键词<keyword>的竞价，<BROAD|PHRASE|EXACT>匹配类型`)
are overwritten by Amazon's suggested bid about a second after you set
them. Expect keywords to go in at suggested bids; set the approved bids
after launch ([`mechanics-edit.md` § 2](mechanics-edit.md#2-sp-keyword--target-bid))
rather than blocking the launch on them.

## 7. Categories (manual product targeting)

Switch the sub-type to `PRODUCT`. Tabs `Categories | Individual
Products`; under Categories, `Suggested | Search` — click `Search` for
`input[placeholder="Search by category name"]`.

Set the **bid-mode dropdown** above the search (default `Suggested bid`;
`Custom bid` reveals a currency input) before adding: a category takes
the bid mode active when you click Add. Search the category, then click
`Add` on the row whose breadcrumb ends in your exact category (usually
the first; check). `Refine` opens sub-categories without adding the
parent.

## 8. Auto-targeting groups and negatives

Targeting `AUTO`, sub-type `TARGETING_GROUP`: four bid inputs in
document order — 紧密匹配 / 宽泛匹配 / 同类商品 / 关联商品 (close-match /
loose-match / substitutes / complements), typically around y 1776 / 1852
/ 1924 / 2000. Set them in y-order; these setters stick because the
values go out in the form payload.

The `否定关键词投放` / `否定商品投放` sections can stay collapsed when
there are no negatives.

## 9. Save, launch, and fix validation errors

Bottom actions: `返回广告活动` (cancel) / `另存为草稿` (save as draft) /
`启动N个广告活动` (launch N campaigns).

**Draft first.** Click `另存为草稿`, reopen the draft from `View drafts`
on the Campaign Builder or `https://advertising.amazon.<tld>/cm/drafts?entityId=…`
(each row has `编辑` Edit), show it to the user, and launch from inside
the draft once they approve. The reopened form re-hydrates every value.

**Campaign name.** A campaign-name value set by JS and launched directly
is lost (React still holds the generated `广告活动 - <timestamp>`), so
the campaign launches under the placeholder. Set the name in the
reopened draft, which binds it properly. To fix one that already
launched: `…/cm/sp/campaigns/<id>/settings`, set the name input +
`input`/`change`/`blur`, click `Save`. The ad-group name does not have
this problem.

**Name conflicts.** `您的其中一个广告活动已使用了此名称` means the name is
taken — often by an empty shell from a failed bulk upload. Archived
campaigns keep their names, so add ` v2` after the ` - agent` suffix on
both the campaign and the ad-group name.

**Validation errors — the `修复` (Fix) flow.** Instead of launching,
a banner `在您修复 N 个或更多问题之前，我们无法创建您的广告活动` with a
`修复` button may appear; nothing is created until it clears. Click
`修复` (it scrolls to the offending row) rather than clicking Launch
again. The usual cause is the auto-inserted keyword-group row
`Keywords related to your product category` (match label
`GROUP匹配类型` / `关键词组`) with bid `0.00`, which fails
`竞价必须至少为 <currency> 0.10`. A JS value set reverts to 0.00, and the
~31 px input sits under an overlay (a coordinate click lands on
`BODY`), so:

1. Find the input by aria-label matching `GROUP匹配类型` with value
   `0.00`; `scrollIntoView({block:'center'})`.
2. `click_at_xy` the centre of its parent cell (`inp.parentElement`,
   ~51 px wide) — this enters edit mode; the input widens and takes
   focus.
3. Real keystrokes: `Meta+a`, `type_text("2.00")` (a small positive
   bid), `Enter`. Re-read the value and check the banner
   `此列表存在一个或多个错误` is gone.
4. Launch again. Success lands on `…/cb/sp/summary?campaign=<NEW_ID>`
   and the draft leaves the drafts list; the new campaign id differs
   from the draft's.

Cell-click to enter edit mode, then real keystrokes, is the reliable way
into any bid cell rendered as a collapsed display.

## 10. New ad group in an existing campaign

On `/cm/sp/campaigns/<id>/ad-groups`, click **Create ad group**; the
form opens at `/cb/sp/campaigns/<id>/adGroups`.

1. **Name** — `#sspa_sp_adGroupSettings_adGroupName`.
2. **Products** — search by ASIN/SKU and `click_at_xy` the row's Add
   (a `js()` click may not register).
3. **Default bid** — `#sp-defaultBid`, pre-filled with the suggested
   bid, which can be well above what you want.
4. **Targeting type** — the form defaults to product targeting even in a
   manual keyword campaign. `click_at_xy` the
   `manualTargetingType-KEYWORD` radio, or the group gets a
   product-targeting tab and "Add keywords" never appears.
5. **Keywords** — as § 6.

Per-keyword bid cells (`#kwp:kwp-bid-cell-field-0`, inside open shadow
roots) ignore `fill_input` — validation then says "Required". Clicking
the row's suggested-bid **Apply** link sets it natively; use it when the
suggested bid is acceptable and adjust afterwards.

While this form is open, avoid `js(...)` on the page: the SPA can treat
it as navigation and drop to `about:blank`, losing the form. Read with
`page_info()` and act with `fill_input` / `click_at_xy`.

After creating, the SPA can keep redirecting the campaign's ad-groups
page back to the form; go to `campaign-manager/all-campaigns` first, then
back to the campaign.

## 11. Verify the Product Ad after launch

The campaign name and keywords do not prove what is advertised; the
Product Ad does, and the create form can carry it over (§ 4). Open each
new campaign's ad-group Ads tab
(`…/cm/sp/campaigns/<campId>/ad-groups/<agId>/ads?entityId=…`) and
check the `SKU/ASIN` column is the SKU this campaign is for. A campaign
named for one SKU showing another spends budget on the wrong product
for the right keywords and misattributes every order in later analysis.
Fix it by adding the correct product (enabled) and pausing or removing
the wrong one in each affected ad group.

## 12. SKUs and the bulk sheet schema

**An ASIN in a Product Ad `SKU` cell empties the ad group** — see
[`bulk-operations.md` § The ASIN-as-SKU trap](bulk-operations.md#the-asin-as-sku-trap-guarded-in-code).

**Finding the store's SKUs.** The bulk export's
`Sponsored Products Campaigns` sheet, filtered to
`Entity == 'Product Ad'`, lists every live campaign's `SKU` — the
store's real naming scheme. For one child SKU, open
`…/skucentral?mSku=<exactChildSku>&condition=New`: it resolves the child
even with zero parent inventory and shows ASIN, FNSKU and per-SKU tabs.
Searching the parent first does not help (skucentral has no
expand-variations control, and Manage Inventory lists parents only).
New listings sometimes use the store's scheme (`<PREFIX>-NNN`) and
sometimes Amazon-generated tokens (`XX-XXXX-XXXX`); if you cannot tell,
ask the user for the exact child SKU with `AskUserQuestion` rather than
guessing. The create form's product search also shows the seller SKU on
the right-pane card.

**SP bulk sheet — 52 columns.** Treat the sheet's own header row as
truth (labels change; the order is what `ads_bulk.py` relies on):

```
Product, Entity, Operation, Campaign ID, Ad Group ID, Portfolio ID, Ad ID,
Keyword ID, Product Targeting ID, Campaign Name, Ad Group Name,
Campaign Name (informational), Ad Group Name (informational),
Portfolio Name (informational), Start Date, End Date, Targeting Type,
State, Campaign State (informational), Ad Group State (informational),
Daily Budget, SKU, ASIN (informational), Eligibility Status (informational),
Ineligibility Reason (informational), Ad Group Default Bid,
Ad Group Default Bid (informational), Bid, Keyword Text, Native Language Keyword,
Native Language Locale, Match Type, Bidding Strategy, Placement, Percentage,
Product Targeting Expression, Resolved Product Targeting Expression (informational),
Audience ID, Shopper Cohort % (informational), Shopper Cohort Type (informational),
Segment Name (informational), Impressions, Clicks, Click-through Rate,
Spend, Sales, Orders, Units, Conversion Rate, ACOS, CPC, ROAS
```

Key columns by entity:

- **Campaign** — Campaign Name, Start Date (`YYYYMMDD`), Targeting Type,
  State, Daily Budget, Bidding Strategy.
- **Bidding Adjustment** — Campaign Name, Bidding Strategy, Placement
  (`Placement Top` / `Placement Product Page` / `Placement Rest Of Search`),
  Percentage.
- **Ad Group** — Campaign Name, Ad Group Name, State, Ad Group Default Bid.
- **Product Ad** — Campaign Name, Ad Group Name, State, SKU (the seller
  SKU).
- **Keyword** — Campaign Name, Ad Group Name, State, Bid, Keyword Text,
  Match Type.
- **Product Targeting** — Campaign Name, Ad Group Name, State, Bid,
  Product Targeting Expression: `category="<exact category name>"`
  (manual) or `close-match` / `loose-match` / `substitutes` /
  `complements` (auto).

Operation is `Create`, `Update` or `Archive`. Upload tokens must be the
English API values and Create rows need placeholder ids — see
[`bulk-operations.md`](bulk-operations.md).
