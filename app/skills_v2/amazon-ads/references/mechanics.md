# Amazon Ads & Coupons — Verified Mechanics

> **Reference of the `amazon-ads` skill.** See `../SKILL.md` for the
> catalog and high-level reference picker. This file is the single
> source of truth for click paths, URLs, modal patterns, and field
> input ranges across the Amazon Ads + Coupons UIs.

Every click path below was confirmed by hands-on browser-use testing on
`advertising.amazon.<tld>` and `sellercentral.amazon.<tld>`. The Chinese
labels are what the UI actually shows when the merchant account uses
zh-CN; English labels are noted where the page is locale-mixed.

## 0. Preconditions every run

Before any browser-use call:

1. Load `browser-use` skill first (0.13 heredoc interface — you pipe
   Python helper code to `browser-use` via a `<<'PY' … PY` heredoc; the
   pre-imported helpers are `new_tab`, `page_info`, `capture_screenshot`,
   `click_at_xy`, `wait_for_load`, `ensure_real_tab`, `js`, `fill_input`,
   `type_text`, `press_key`, `cdp`).
2. The wrapper auto-starts the browser on the first `new_tab(...)` if the
   CDP proxy isn't already up, so you don't need to pre-warm anything.

## 1. URLs that work (and ones that don't)

This table covers **advertising and coupons URLs only**. For
seller-central paths (inventory, listings, orders, performance,
etc.) read `knowledge/project/common/amazon-sites.md` — that file
has the canonical per-country base URLs and the path table. Don't
guess seller-central paths.

| Purpose | Working URL | Notes |
|---|---|---|
| Campaign manager | `https://advertising.amazon.<tld>/campaign-manager` | Bare URL works — Amazon resolves a default `entityId` from the signed-in account. The path `/aiv/overview` is a **stale redirect target** that 404s — do not document it. To deep-link to a specific campaign / ad-group, scrape `entityId=ENTITY[A-Z0-9]+` from the landing page's body innerHTML first, then build the deeper URL with that suffix. |
| Single campaign | `…/cm/sp/campaigns/<campaignId>?entityId=...` | Deep-link only — needs the `entityId` scraped from the campaign-manager landing. The first path leg after `cm/sp/` switches per ad type (`sp`, `sb`, `sd`). |
| Ad group | `…/cm/sp/campaigns/<campId>/ad-groups/<agId>/<tab>?entityId=...` where tab ∈ `ads`/`targeting`/`negative-targeting`/`search-terms`/`history` |  |
| Campaign tabs | `…/cm/sp/campaigns/<id>/<tab>` where tab ∈ `ad-groups`/`bid-adjustments`/`negative-targeting`/`budget-rules`/`settings`/`history` | |
| Bulk operations | `…/bulk-operations?entityId=...` | NOT `/sp/bulk-operations` (404). |
| Drafts | `…/cm/drafts?entityId=...` | NOT `/cm/sp/drafts/...` (only the inner path). |
| Coupons dashboard | `https://sellercentral.amazon.<tld>/coupons/dashboard` | NOT `/promotions/coupons` (redirects elsewhere) and NOT `/cppd/coupons` (404). |
| Coupon create | `https://sellercentral.amazon.<tld>/coupons/create-coupon` | |

The ad console **redirects through Amazon sign-in on first hit per
session** even though the seller-central session is good. Ziniao
auto-fills the password (and OTP/2FA via 紫鸟验证码服务). Click
`signInSubmit` (or the `mfaSubmit` after OTP) and wait ~5s for the
redirect to settle.

Each store's ad console may be on a *different* underlying Amazon
account from its seller-central account (different email and entity
id). Re-check the entity id on the campaign-manager landing page —
search the page HTML for `entityId=ENTITY[A-Z0-9]+`. Save it; you'll
need it on every URL.

## 2. Reading existing campaigns

### 2a. The campaign-list "Find a campaign" search

The campaign list has a search input with placeholder
`Find a campaign` (or `查找广告活动`). Setting its value via the standard
React-friendly setter and dispatching `input` + `keydown:Enter` triggers
the table filter. Returns deep-links to each campaign in the result rows
(`<a href="…/cm/sp/campaigns/<id>?entityId=…">`).

The other top-of-page input — placeholder `Search with metrics or
settings` / `使用指标或设置进行搜索` — is **not** a campaign-name search.
It's a saved-filter builder. Don't use it.

The campaign list is **paginated** (50/page) but `pageNumber` URL
params are silently ignored. To browse pages, click the bottom-right
`Next Page` button. Easier: just use the "Find a campaign" search to
jump straight to the campaigns you want.

> ⚠️ **The search value persists across sessions** — it's stored in the
> browser profile, not the URL. A `keyboard` you (or a previous task, or
> a human) typed last week is *still applied* when you open the campaign
> list today, silently hiding every campaign that doesn't match. This
> has produced real audits that reported "8 campaigns, 2 active" for a
> store that actually has 145 — the agent read the filtered list as if
> it were the whole account.
>
> **Phase 1 (Discover) must enumerate the WHOLE account, so before you
> read the list you must clear the search and confirm it's empty:**
>
> ```js
> // pass this to js("""…""") — clears any persisted "Find a campaign" term
> (function(){
>   var inp=[...document.querySelectorAll('input')]
>     .find(function(i){return /Find a campaign|查找广告活动/
>       .test(i.getAttribute('placeholder')||'');});
>   if(!inp) return 'NO_SEARCH_INPUT';
>   var set=Object.getOwnPropertyDescriptor(
>     window.HTMLInputElement.prototype,'value').set;
>   set.call(inp,'');
>   inp.dispatchEvent(new Event('input',{bubbles:true}));
>   inp.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Enter'}));
>   return 'CLEARED';
> })()
> ```
>
> Then **verify the true total** before trusting any row count — the
> grid's `aria-rowcount` (minus the header row) is authoritative and
> reflects the full filtered set, not just the rendered rows:
>
> ```js
> (function(){
>   var g=document.querySelector('[role=grid],[role=treegrid]');
>   var inp=[...document.querySelectorAll('input')]
>     .find(function(i){return /Find a campaign|查找广告活动/
>       .test(i.getAttribute('placeholder')||'');});
>   return JSON.stringify({searchVal:inp?inp.value:null,
>     totalRows:(+g.getAttribute('aria-rowcount'))-1});
> })()
> ```
>
> If `searchVal` is non-empty, you did not clear it — stop and clear
> before reading. The grid **virtualizes** (≈13 DOM rows regardless of
> total), so `querySelectorAll('.ag-center-cols-container [role=row]')`
> NEVER gives you the full list once the account has >~15 campaigns. For
> the full enumeration use **bulk download** (§2d) — it is the only
> reliable way to capture every campaign's name/id/status/type/spend in
> one shot. Treat any DOM-scrape of the campaign list as a spot-check,
> never as the manifest.

The default status filter is "Active : Enabled" (a removable chip
near the table). The "Remove all" link near the top *doesn't* clear
this chip — you have to click the chip's own × button (or its
internal close action) to see paused/archived campaigns. The same
persistence caveat applies: a status/type filter left from a prior
session stays applied — confirm the chips before enumerating.

### 2b. Reading a single campaign's settings tab

`…/cm/sp/campaigns/<id>/settings?entityId=…` page text contains, in
order:

```
Campaign:<name>
Status:<状态>
Type:Sponsored Products - Manual|Auto
Country:<country>
Schedule:<start date> - <No End Date|end date>
Budget:<currency><amount> - Daily
…
Campaign ID
<id>
Portfolio
<No portfolio|name>
…
Bid optimization
<Dynamic (down only)|Dynamic (up and down)|Fixed bids>
…
Top of search (first page)
adjust value %
Product detail pages
adjust value %
Rest of search
adjust value %
```

`Schedule` shows only one date when the campaign has no end date.
Placement adjustments default to 0% — when blank in display they
ARE 0%, not unset.

### 2c. Reading targets

Ad-group `…/targeting` page **virtualizes** rows — the DOM may only
contain ~16 of 20 keyword rows at any time, and scrolling within the
table doesn't always materialize the rest. The reliable way to get
all rows is the per-page **Export** button (top-right of the targets
table) which writes a CSV like `Sponsored_Products_Target_<date>.csv`
to `~/.vibe-seller/downloads/<store>/`. CSV columns include `State`,
`Keyword`, `Target match type`, `Status`,
`Suggested bid (low|median|high)(<marketplace currency>)`,
`Bid (<marketplace currency>)`, plus performance metrics. The column
header carries the marketplace currency code — do NOT hardcode one.
Per-table CSV export works for
keyword and category and auto-target groups.

The "Total: N" cell in the table footer is the authoritative count.

### 2d. Bulk download (cross-campaign capture)

**FIRST, reuse the newest existing export — do NOT generate a fresh job
by default.** For a read-only audit you need a recent snapshot, not a
brand-new export. The bulk-operations page keeps recent completed jobs
with live download links, and the shadow-root walk below finds those
`<a download>` links for *already-generated* files too. So the default
first action on this page is: run the download-link walk (the code block
below). It clicks the newest link (`a[0]`) and **returns its filename** —
then **verify that filename's date range covers your audit window**; if
it does, you are done. The filename encodes the range:
`bulk-<entity>-<START>-<END>-<epoch>.xlsx` (e.g. `…-20260602-20260702-…`
= Jun 2–Jul 2). If `a[0]` is `NO_LINK`, or its range does **not** cover
your window, generate a fresh export (the 5–15 min job below) — that is
the **fallback**. Reusing a recent file avoids the flaky `下载广告活动`
modal entirely.

**Only if there is no recent export**, generate one: click
`Download campaigns` (`下载广告活动`) to open the modal. ⚠️ **This trigger
button is inside the `<bulk-storm-dashboard>` custom element's shadow
DOM** — a top-level `document.querySelector('button')` / text match will
**not find it**, and a coordinate/`.click()` on a look-alike top-level
element **silently no-ops** (the common failure: repeated click attempts,
"let me try a more reliable click", switching strategies). It is the
dashboard's **2nd header button** (fixed order: 1st = Upload,
2nd = Download); find it by **walking shadow roots** (same `walk()` as
below) and click the shadow element directly (`el.click()`), or
`click_at_xy` on its `getBoundingClientRect()` centre. See
`bulk-operations.md` → "drive it by language-INDEPENDENT selectors" for
the full structural anchor table. The modal then has these checkboxes
(default all checked for "Enabled & Paused"):

- Terminated campaigns
- Paused campaigns
- Campaign items with zero impressions
- Placement data
- Sponsored Products / Brands / Brands multi-ad / Display data
- Sponsored products search term data
- Sponsored brands search term data
- Budget Rules Data

Click `Download` (`下载` / `下載` / localized) in the modal to submit.
The job takes **5–15 min** for ~150 campaigns and the status table polls
infrequently — re-`open` the bulk-operations URL to refresh. When the
row's Status flips to `Success`, trigger the download by its **anchor,
matched by href** — this is the one method that works on **both macOS
and native-Windows**. Do NOT click the visible link text: the
"下载 / 下載 / Download" cell content is a `<p>` label, not the clickable
`<a>`. A coordinate/element click on that label happened to work on
macOS but **silently no-ops on Ziniao / native-Windows** (nothing
downloads, the dir stays empty — the failure this section fixes). The
real link is an `<a download href="…/bulk-operations/download/…xlsx">`
in that cell; match it **by href, not by button text** (the label is
localized — 下载 / 下載 / Download — and the ag-Grid column *header* also
reads "Download"):

```bash
# Preferred: click the real <a download> via JS (the grid is an ag-Grid
# inside a web-component shadow root, so walk shadow roots to find it).
browser-use <<'PY'
js(r"""
(function(){
  function walk(root,acc){root.querySelectorAll('*').forEach(function(e){
    if(e.shadowRoot)walk(e.shadowRoot,acc);
    if(e.tagName==='A'&&/bulk-operations\/download\//.test(e.href||''))acc.push(e);
  });return acc;}
  var a=walk(document,[]);
  if(!a.length)return 'NO_LINK';   // newest job is a[0]
  a[0].click();                    // real <a download> click → fires the download
  return 'CLICKED '+a[0].href.slice(-48);
})()
""")
PY
# Alternative: capture a[0].href (js("return …")) and new_tab(href). Chrome
# aborts the 'navigation' to start a download, so it returns a benign
# `net::ERR_ABORTED` — the file still lands.
```

The file lands in `~/.vibe-seller/downloads/<slug>/` (the CDP mux proxy
pins Chrome's download path there via `setDownloadBehavior` — verified on
native-Windows too, not just macOS). Its name is Amazon's real export
name `bulk-<entity>-<YYYYMMDD>-<YYYYMMDD>-<epochms>.xlsx`
(e.g. `bulk-a1b2c3d4e5-20250101-20250131-1700000000000.xlsx`), **NOT** a
fixed `BulkSheetExport.xlsx`. Don't hard-code the name — grab the newest
xlsx and wait for any in-progress `*.crdownload` to disappear first:

```bash
ls -t ~/.vibe-seller/downloads/<slug>/*.xlsx 2>/dev/null | head -1
```

The XLSX has 9 sheets; the meaningful ones for SP are
`Sponsored Products Campaigns` (one row per entity: Campaign /
Bidding Adjustment / Ad Group / Product Ad / Keyword / Negative Keyword
/ Product Targeting / Negative Product Targeting) and
`SP Search Term Report`.

**Bulk export gotchas verified in the wild:**

- The download date-range filter affects whether **structure** is
  emitted, not just metrics — campaigns that are paused with no
  recent activity get *dropped from the export entirely*, even when
  "Paused campaigns" + "Campaign items with zero impressions" are
  both checked. To capture an old paused campaign, fall back to the
  per-table CSV export from the campaign's UI tabs.
- Auto-targeting `complements` group can be **silently dropped** when
  the rest of close/loose/substitutes have non-default bids and
  complements is at the ad-group default. The ad group will appear
  to have only 3 product-targeting rows in the export when the UI
  shows 4. Always cross-verify auto campaigns via UI.

## 3. Creating a campaign via UI (the verified path)

The new-campaign UI is a **single long form**, not a multi-step
wizard, despite having section headers. Submit goes from the entire
form at once. Save-as-draft also writes the entire form.

### 3a. Open the form

```bash
# campaign-manager landing → the visible "Create campaign" link/button
# (the icon-only one is at x≈12, the labeled link is at x≈316; click the
# labeled one — the icon variant doesn't always navigate)
browser-use <<'PY'
js("(()=>{const all=document.querySelectorAll('a, button');for(const a of all){const t=(a.innerText||'').trim();const r=a.getBoundingClientRect();if((t==='创建广告活动'||t==='Create campaign')&&Math.abs(r.x-316)<10){a.click();return;}}})()")
PY
# A modal opens with options: 商品推广 / 品牌推广 / 展示
# (Sponsored Products / Sponsored Brands / Sponsored Display)
# click the matching one.
```

**Country selector — verify before proceeding (gotcha).** The
modal renders a country control above the campaign-type tiles.
There are two shapes depending on how the seller account is
registered with Amazon Ads — the agent must read the DOM, not guess
from the URL.

DOM shapes verified live:

```text
# A) multi-market account (one Amazon Ads entity registered for
#    several marketplaces, e.g. multiple country marketplaces under
#    the same login)
<button aria-label="<current-country-name>">  # clickable trigger
  …click to open…
  <button value="<CC1>" role="option" selected="false">Country One</button>
  <button value="<CC2>" role="option" selected="true">Country Two</button>
  <button value="<CC3>" role="option" selected="false">Country Three</button>

# B) single-market account (Amazon Ads entity registered for one
#    marketplace only — typical when the seller signed up Amazon
#    Ads for one country and never extended)
<button aria-label="<country-name>" disabled="true">  # NOT clickable
  Country One
```

Procedure:

1. Read the country trigger button's attributes from the modal.
2. If `disabled="true"` (shape B): this account can only advertise
   on the country shown. The campaign you're about to create will
   land on that country regardless of which seller-central
   marketplace the listing lives on. If that doesn't match the
   task's intent, **stop and tell the user** — the fix is not on
   this page (the account needs Amazon Ads registration for the
   target marketplace, or use a different store whose Amazon Ads
   entity already covers it). Do NOT proceed and "hope it sorts
   out at submit time".
3. If the trigger is clickable (shape A): read its `aria-label`. If
   it doesn't already match the task's country, click the trigger,
   then click the option whose `value` equals the target ISO code
   (a 2-letter country code). Re-read the trigger's `aria-label` after the
   click to confirm the switch took.

The URL TLD (e.g. `advertising.amazon.<tld>`)
**does not** force the country in shape A — the modal dropdown
overrides it. The TLD matters only as the entry point of an
already-authenticated session: in shape B, the TLD typically
matches the only registered marketplace; in shape A it's just
where this entity's console happens to live, and the dropdown is
authoritative.

### 3b. Form-field map (Sponsored Products)

Prefer `id`-based selectors below — they're stable across UI locale.
The `placeholder` columns show the per-locale strings for context;
do not rely on placeholder for selection. Currency-bearing inputs
are dynamically labelled `enter amount in <CURRENCY>` with the
marketplace's currency — match by `id`, not currency.

| Field | id selector | Locale placeholder examples | Notes |
|---|---|---|---|
| Ad group name | `#sspa_sp_adGroupSettings_adGroupName` | EN `Example: Seasonal Products` / ZH `示例：季节性商品` | Pre-filled with `Ad Group - <DD/MM/YYYY HH:MM:SS.mmm>` timestamp on form open; overwrite if a naming convention is required. |
| Campaign name | `#fieldSet\.campaignSettingsFieldSet-AtlasCoreComponents\:CampaignName\:Input` | EN `Example: Holiday Favorites` / ZH `示例：假日最爱` | Pre-filled with `Campaign - <DD/MM/YYYY HH:MM:SS.mmm>`. **Setter on launch is unreliable** — see § 3h save-as-draft-then-rename workaround. The ad-group-name setter works the same way and persists. |
| Product search | `#ucb-sp-ups\:ups-asin-search-input` | EN `Search by product name, ASIN, or SKU` / ZH `按商品名称、ASIN 或 SKU 搜索` | Set value via `input` event — typeahead fires automatically, no Enter required. **Must flip the All-products toggle first** (see Suggested-vs-All-products trap above) and **scroll the virtualised grid** to see all results. |
| Default bid | `#sp-defaultBid` | dynamic `aria-label="enter amount in <CCY>"` | Pre-filled with Amazon's suggested bid (e.g. `0.64`); overwrite as needed. |
| Daily budget | `#fieldSet\.campaignSettingsFieldSet-AtlasCoreComponents\:CurrencyInput\:Input` | dynamic `aria-label="enter amount in <CCY>"` | The **empty** currency input. Set the value as a plain number. Currency follows the campaign's marketplace — do NOT hardcode a currency; read it from the campaign. |
| Targeting type | `input[type=radio][name=targetingType][value=AUTO\|MANUAL]` |  |  |
| Manual sub-type | `input[type=radio][name=manualTargetingType][value=KEYWORD\|PRODUCT]` |  |  |
| Auto sub-type | `input[type=radio][name=automaticTargetingType][value=DEFAULT\|TARGETING_GROUP]` |  | `TARGETING_GROUP` reveals 4 per-group bid inputs. |
| Bidding strategy | `input[type=radio][name=biddingStrategy][value=optimizeForSales\|legacy\|manual]` |  | `legacy` = "Dynamic - down only". |
| Start date | inner of `kat-date-picker[name=start-date]` |  | See § 3c. |
| End date | inner of `kat-date-picker[name=end-date]` |  |  |
| Save as Draft (button) | `#sspa_sp_save_as_draft` |  | Persists form state without launching ads. Use this for the plan-stop review flow — show the draft to the user, then return here to click Launch. |
| Launch campaign (button) | `#sspa_sp_createCampaign` |  | Final commit. Only click after the user confirms the draft. |

After clicking the product search input + dispatching Enter, parse the
left-pane suggestions: each row has an `添加` (Add) button. The right
panel pinned to the screen shows `N 个商品` (N products selected) and
each added item with brand, price (in the marketplace currency), ASIN,
SKU. Use the surrounding
row's text to disambiguate when multiple matches show — walk parents 8
deep looking for the desired ASIN.

**Suggested-vs-All-products toggle (recurring trap).** Above the
results grid the form has two `role=switch` buttons: the `Suggested`
toggle (default, checked; its label is localized to the page locale)
and the `All products` toggle (unchecked; likewise localized). With
the default `Suggested` toggle on, the results grid
shows other products from the seller and **ignores the search input**
— a typed ASIN returns rows for unrelated SKUs and looks like the
search "didn't work". Click the `All products` toggle *before*
judging whether a search returned the right ASIN. Typing the ASIN
alone fires the search (no Enter required); switching the toggle is
what makes results filter to the typed value.

**Virtualised results grid — scroll the GRID, not the page (recurring
trap).** The product results panel is `<div aria-label="grid">` and is
**virtualised**. Only the rows that fit in the visible viewport are
rendered into the DOM at any moment. Header text reads e.g.
`1-35 of 35 results` even though only ~15 are present in the DOM. If
you only inspect what's visible — via `page_info()`, an innerText scrape,
or a SKU regex — you will conclude the target product "isn't there" and
fail the task on a phantom indexing-lag theory. The product is there;
it's just below the fold of the inner grid.

To inspect all results, scroll the grid element itself (not the page):

```js
const grid = document.querySelector('div[aria-label="grid"]');
grid.scrollTop = grid.scrollHeight;          // jump to the bottom
// then re-read grid.innerText / re-query its row children
```

Scroll, snapshot, scroll again until `scrollTop + clientHeight >=
scrollHeight`, accumulating SKUs/ASINs as you go. Page-level scroll
(`js("window.scrollTo(...)")` / `js("window.scrollBy(0, window.innerHeight)")`)
does **not** reveal the off-screen rows because the grid uses an internal
virtualised viewport.

**Parsing rows is column-concatenated, not field-labelled.** A single
visible row's `innerText` reads like
`<title>\n(<rating>)\n\n<currency> <price>\n\nIn stock\n\nASIN:<asin>\n\nSKU:<sku>\n\n<eligibility>\n<brand>`
— so a SKU regex like `/SKU:([^\s]+)/` will sometimes capture the SKU
glued to the next two fields (e.g. `<sku-name>IneligibleBRAND`) when
the row separator between them is a non-newline character. Always
anchor the regex to a known boundary: prefer
`/SKU:\s*([A-Za-z0-9._-]+)\b/` over greedy `[^\s]+`, and parse
`Eligibility` (`Eligible` / `Ineligible`) and `Status` (`In stock` /
`Out of stock`) as separate fields rather than tail-of-SKU substrings.

**Multi-campaign product-reuse trap (verified the hard way, 2026-06-23).** The right-pane added-product card **persists across
campaign creations in the same session**: after you add a product and
launch one campaign, opening the form for the NEXT campaign can keep
that SAME product selected. So when creating several campaigns in a row
(one per SKU), it is dangerously easy to change only the campaign NAME
and KEYWORDS while the Product Ad silently stays the FIRST SKU —
shipping, e.g., SKU-001 as the advertised product in all six
campaigns. Before launch, re-confirm the right-pane added-product card
(the panel pinned with "N 个商品") shows THIS campaign's intended
SKU/ASIN (remove the carried-over product first if present), and verify
again *after* launch (§ 3i).

### 3c. Date picker — the recurring trap

The kat-date-picker accepts `value=MM/DD/YYYY` internally but its
visible inner `<input>` shows `DD/MM/YYYY`. Setting the inner input's
value via `Object.getOwnPropertyDescriptor(...,'value').set` and
dispatching `input`/`change`/`blur` is **not enough** — the kat
component re-renders and clears the field on next form interaction.

Reliable path: open the picker by clicking it, then click the day cell
button whose innerText equals the day-of-month you want. Search:
`button` whose `innerText.trim()` exactly equals the target day, and
whose `getBoundingClientRect()` falls inside the open picker overlay
(roughly y∈[200,400], x∈[100,400] for start; x∈[800,1000] for end).
After clicking the day, the picker closes and the inner input
properly reflects.

If the day click doesn't take, try Tab out of the field after typing.

### 3d. Adding keywords (manual / KEYWORD targeting)

Click the keyword section's `输入列表` (Input list) tab — there are
**two** `输入列表` tabs on the page (one for the product search above,
one for the keyword section below). The keyword one is the **lower**
tab (larger y); the product one is higher. Don't hardcode y — collect
both, sort by y descending, take the first. **The keyword textarea
(`textarea#kwp\:kwp-enter-list-text-input-area`) is hidden
(`getBoundingClientRect` width/height 0) until this tab is clicked**,
so you MUST: `scrollIntoView({block:'center'})` the lower tab, then
click it **by coordinate** (a JS `.click()` on the wrong span silently
fails — this is the trap that strands the agent in a "textarea not
visible" loop). After the click, re-read the textarea rect to confirm
it is now visible before typing.

The match-type checkboxes are at y≈1990 immediately *after* clicking
the input-list tab, but **after one batch is added the page re-flows
and the match-type checkboxes can shift to y≈490**. Do not hardcode a
y range when toggling them; instead match by label text:
`广泛`/`词组`/`精准` (Broad/Phrase/Exact). Click the surrounding
`<label>` element, not the inner input — kat-checkbox swallows direct
input-element clicks.

Bid input for the keyword section: `input[type=text]` with aria-label
matching `输入金额` and `针对关键词…的竞价，GROUP匹配类型` — but
**setting it programmatically is unreliable**: Amazon's auto-suggest
system overwrites whatever you type with its own "suggested bid"
within ~1s. Better: add the keywords first (default bid will populate
each row from the suggested-bid system), then **per-row** edit each
row's individual bid input by aria-label
`针对关键词<keywordText>的竞价，<BROAD|PHRASE|EXACT>匹配类型` — but
even this gets stomped by auto-suggest. Realistic outcome: keywords
go in with Amazon's suggested bids, and the user can tune in the
dashboard later. Do not block the launch on bid mismatches.

To enter the keywords themselves (verified): once the
textarea is visible, **click it with `click_at_xy(x, y)` and `type_text`
each keyword with `press_key("Enter")` between them** — real keystrokes,
NOT a `js()` `.value` setter +
dispatch (Amazon's React textarea reverts a programmatic set, the same
class of bug as the bid cells). Then click the button labeled
`添加关键词` (Add keywords). With all three match-type checkboxes
(`广泛`/`词组`/`精准`) left checked, a single add created broad+phrase+
exact rows for all keywords in one batch (≈3 kw → ~9 keyword rows +
the auto group row). If you instead see duplicates/skips, fall back to
the one-match-type-per-batch method below.

If you must script the textarea programmatically anyway, use the
`execCommand('insertText')` workaround in § 8d — a plain `.value =`
assignment does not register with React.

**Modal-blocker gotcha:** when *any* of the keywords being added
"has no suggested bid" (e.g. some long-tail keywords for an exact
match type), Amazon pops a modal: `<keyword>-精准 没有建议竞价 / 选择备选竞价`
with a fallback bid input (defaulted in the marketplace currency)
and an `添加 个关键词` button.
The modal **blocks subsequent adds** until dismissed. After every
add-keywords batch, check for this modal and click `添加 个关键词`
inside it (the regex `^添加\s*\d*\s*个?关键词$`). Only the keywords
in the *last* batch may have triggered the modal — but if you don't
dismiss it, the *next* two batches you submit are silently dropped.

Each match type needs a separate add — the form's "all 3 checkboxes"
default does **not** create 3 rows per keyword reliably; some keywords
get duplicated, some get skipped, and Amazon de-dups across batches.
Always: uncheck all but one match type, add that match type's
keywords, dismiss any modal, repeat for the next match type.

### 3e. Adding categories (manual / PRODUCT targeting)

Switch sub-type to `PRODUCT`. The targeting section reveals tabs:
`Categories | Individual Products`, and within Categories: `Suggested |
Search`. Click `Search` to expose `input[placeholder="Search by
category name"]`.

Above the search input is a **bid-mode dropdown** — default
`Suggested bid`. To use a fixed bid for all categories, click it and
choose `Custom bid`; a currency input (in the marketplace currency)
appears next to it. **Set this
before searching** — adding a category captures the bid mode active
at the time of click.

Search the category name → click the `Add` button on the matching row.
The search returns multiple matches (e.g. searching "Computer Mice"
returns 17 categories including Gaming Mice); the
category row whose breadcrumb ends in your exact target string is
typically the first row in the result list, but verify before clicking.

The page may also show a `Refine` link next to `Add` — `Refine` opens
sub-categories without adding the parent. Click `Add` for the leaf
category you want.

### 3f. Auto-targeting groups

Switch targeting to `AUTO`, then auto-sub-type to `TARGETING_GROUP`.
Four bid inputs appear at predictable y-coordinates (typically y≈1776,
1852, 1924, 2000 in document order: 紧密匹配 / 宽泛匹配 / 同类商品 /
关联商品 = close-match / loose-match / substitutes / complements).
Target each in y-order and set bids. These setters DO stick (unlike
keyword bids) because the auto-target form submits the values in the
form payload directly.

### 3g. Negatives

Skip the `否定关键词投放` and `否定商品投放` collapsible sections
when there are no negatives — leaving them collapsed is fine.

### 3h. Saving and launching

Bottom of the form has three actions: `返回广告活动` (cancel) /
`另存为草稿` (save as draft) / `启动N个广告活动` (launch N campaigns).

For the new-product-launch plan-stop pattern, **always click 另存为
草稿 first**, then re-open the draft from the `草稿` (Drafts)
sidebar link, screenshot it for the user, get explicit "proceed",
then click `启动` from inside the draft.

The drafts list is at `https://advertising.amazon.<tld>/cm/drafts?entityId=…`;
each draft row has an `编辑` (Edit) button. Reopening loads the same
single-form view with all values populated. Make any final name/bid
tweaks, then `启动`.

**Name conflicts:** if you've already had an empty-shell campaign
created with the same name (typical from a failed bulk upload),
launch will fail with `您的其中一个广告活动已使用了此名称`. Names
are NOT released when a campaign is archived — you have to add a
suffix (e.g. ` v2`) on both the ad group name and the campaign name
to break the conflict. The `- agent` agent-marker suffix from
`new-product-launch` is already part of the name; add `v2` after it.

**Campaign-name persistence gotcha (verified):** if you fill the
campaign-name input via JS setter and click `启动` directly without
the draft round-trip, Amazon's React state is empty for that field
at submit time and the campaign is launched with a default
auto-generated name like `广告活动 - 25/04/2026 23:07:32.456`. Two
fixes:

1. **Save-as-draft-then-edit-then-launch** — save the form as draft,
   re-open the draft (the form re-hydrates the campaign-name field
   correctly), set the name there, launch from the draft view. This
   is the safe path and matches the plan-stop pattern anyway.
2. **Rename in-place after launch** — go to
   `…/cm/sp/campaigns/<id>/settings`, find the campaign-name input
   (it's now bound to a real React value), set + dispatch
   input/change/blur, click `Save`. Useful for fixing campaigns that
   already launched with a placeholder name.

The Ad-Group-name input on the same form does NOT have this issue —
it persists correctly from the JS setter. Only the campaign-name
input is affected. The cause appears to be that the campaign-name
field's React state is initialized to a generated placeholder string
(`广告活动 - <timestamp>`) and only re-binds when the draft cycle
re-hydrates it. Seek confirmation in upstream changelogs before
removing this note.

**Launch blocked by validation errors — the `修复` (Fix) flow (verified
2026-06-03).** Clicking `启动` may not launch; instead a top banner
appears: `在您修复 N 个或更多问题之前，我们无法创建您的广告活动` with a
`修复` (Fix) button. Nothing is created and no spend happens until the
error is cleared. **Do NOT loop-click 启动** — click the `修复` button
(`button`/`a` whose text is exactly `修复`); it scrolls the offending
row into view. The single most common blocker:

**The "Keywords related to your product category" 0.00-bid group row.**
When you add manual keywords, Amazon auto-inserts one or two suggested
**keyword-group** rows (label `Keywords related to your product
category`, match type label `GROUP匹配类型`/`关键词组`) with bid `0.00`.
A 0.00 bid fails validation (`竞价必须至少为 <the marketplace
currency> 0.10`) and blocks the whole launch.
Fix it — do NOT try to JS-set the bid (`.value` + dispatch reverts to
0.00 instantly) and do NOT click the tiny `input` directly (it's a
~31px collapsed display cell with an overlay, so coordinate clicks land
on `BODY`). The verified fix:

1. Find the group bid input by aria-label regex `GROUP匹配类型` with
   value `0.00`; `scrollIntoView({block:'center'})`.
2. Click the input's **parent cell** (`inp.parentElement`, ~51px wide,
   center it via `getBoundingClientRect`). This activates edit mode —
   the input expands (~31px→~54px) and gains focus.
3. Send **real keystrokes**: `Meta+a`, `type "2.00"` (a small
   positive bid in the marketplace currency), `Enter`. Re-read the
   value to confirm it stuck and the banner
   `此列表存在一个或多个错误` cleared.
4. Click `启动N个广告活动` again. Success → URL becomes
   `…/cb/sp/summary?campaign=<NEW_ID>` and the draft leaves the drafts
   list. (Launching a draft mints a NEW campaign id, distinct from the
   draft id.)

This pattern — *cell-click to enter edit mode, then real keystrokes* —
is the reliable way to edit ANY bid cell that shows as a collapsed
display; the `.value` setter is never reliable for these (Amazon's
controlled inputs revert it). See also § 8d for the keyword textarea.

### 3i. Verify the Product Ad SKU after launch (mandatory for multi-SKU batches)

The campaign name and keywords are NOT proof of what's being
advertised — the **Product Ad** (the SKU/ASIN actually shown) is a
separate field, and the create form tends to carry it over between
campaigns (see § 3b "Multi-campaign product-reuse trap"). After
launching, open each new campaign's ad-group **Ads** tab
(`…/cm/sp/campaigns/<campId>/ad-groups/<agId>/ads?entityId=…`,
matching the deep-link table in § 2) and confirm the `SKU/ASIN`
column is the SKU that campaign is meant to advertise.

The tell-tale symptom: a campaign named "SKU-016 …" whose Ads tab
shows SKU-001. That carry-over bug spends real budget showing the
**wrong product against the right keywords** (guaranteed product↔query
mismatch → near-zero conversion),
and it silently **poisons later tuning analysis** — orders get
attributed to the wrong SKU, and "conversion is the ceiling / it's the
listing" conclusions get drawn from a product that was never shown. A
product-ad mismatch dwarfs any bid or keyword tweak, so this check
comes first.

This is the same *verify-against-the-live-page* discipline the tuning
flow already requires for bids (§ 4a0) and negatives — extend it to the
Product Ad at creation. Fix a mismatch by **adding the correct product
(Active) and pausing/removing the wrong one** in each affected ad group.

### 3j. Don't ship sister-store ad copy

The campaign creation form has no "ad copy" field directly — Amazon
serves the listing's own title/bullets. So this rule is enforced by
*not* touching the listing copy on the target store as part of the
ad run.

## 4. Bulk upload — when, when not, gotchas

> **Default path moved.** Creating a campaign and re-bidding across
> keywords/campaigns now go through the export → edit → import flow in
> [`bulk-operations.md`](bulk-operations.md) (with `scripts/ads_bulk.py`).
> This section is the **fallback + reference**: the trusted-event recipe
> for single live edits (§4a0), when to avoid bulk (§4b), and the 52-col
> schema (§4c) the script writes against.

### 4a0. EXECUTION: use TRUSTED input helpers, not `js()`

When *applying* audit changes to the live console, ag-Grid bid/state
cells ARE editable via browser-use — this is how the store was
tuned. The trap is using the wrong tool: a JS-injected event
(`js()` → `element.click()` / `dispatchEvent` / native value-setter +
`input`/`change`) has `isTrusted=false`, and React's synthetic event
system ignores it, so the edit reverts on blur. That is NOT a
browser-use limitation — it's a `js()`/eval limitation.

The `click_at_xy` / `type_text` / `press_key` / `fill_input` helpers
dispatch **CDP `Input.dispatchMouseEvent` / `dispatchKeyEvent` /
`insertText`** — *trusted* browser-level events that React accepts
exactly like a real user. Use these, never `js()`, for edits.

**Working ag-Grid bid-edit recipe (VERIFIED 2026-06-12 —
charging station 3.08→4.00 persisted after refresh):**

The bid input is a **Shadow-DOM `kat-number-input`** web component
(Amazon's KatAL design system), NOT a plain `<input>` in the cell. 0.13
has no element indices, so target it by its on-screen position: read the
inner element's `getBoundingClientRect()` centre, then drive it with the
trusted input helpers.

1. `js("""return (function(){ /* walk shadow roots to the kat-number-input
   for that keyword's bid — value e.g. 3.08 — and return its rect
   centre {x,y} */ })()""")` → get the shadow input's coordinates.
2. `click_at_xy(x, y)` → focus the shadow input (trusted mouse event).
3. `press_key("Control+a")` → **select-all the existing value first.**
   Typing alone does NOT reliably clear the field — on `kat-number-input`
   it sometimes appends, so typing `2.27` into a `3.00` field yields
   `3.002.27` (or similar). A select-all before typing makes the
   replace deterministic.
4. `type_text("4.00")` → TypeText over CDP (trusted). With the value
   selected, this overwrites it. React captures it.
5. `press_key("Enter")` → **commit**. This is the step `js()` can't do —
   a js()-dispatched Enter is untrusted so React never fires its submit
   handler and restores the old value on blur. A real `press_key("Enter")`
   commits.
6. Re-read via `page_info()` / `capture_screenshot()` and confirm the
   cell shows the **exact** new value (not a concatenation like
   `3.002.27`) before moving on.

Pitfalls that produced the earlier false "can't be done" conclusion:
- Coordinate-clicking the OUTER cell + `type_text` targets the wrong node
  (the shadow input never gets the value) → reverts on blur; get the
  inner shadow input's rect centre, not the cell's.
- `js()`-injected click/keydown/native-value-setter events are
  `isTrusted=false` → React ignores them.
- Stale coordinates (the DOM shifts when edit mode opens) → re-read the
  rect via `js("return …")` and re-`click_at_xy` if the click misses.
- `type_text` appending instead of replacing (e.g. `3.00` + `2.27` →
  `3.002.27`) → always `press_key("Control+a")` to select the old value
  before `type_text`, and verify the committed cell shows the exact
  target value. On a production store a concatenated bid is a real-money
  error.

Pause/enable a target or campaign: same — `click_at_xy` the state
toggle (trusted), don't `js()`-click it. This trusted-event path is the
right tool for a **single** live edit. For creating a campaign or
re-bidding **many** keywords at once, prefer the bulk round trip
([`bulk-operations.md`](bulk-operations.md)) — it's the default; drop to
these clicks when a bulk import fails twice or the field has no bulk
column.

**Working search-term negation recipe (VERIFIED 2026-06-12
— "usb-c cable" → Negative exact, confirmed in Negative
targeting tab):**

The ad console loads in **English**; only the separate Bulk Operations
page (`/campaign-manager/bulk-operations`) may render a localized error
in a non-English language — that's a different broken path, NOT the
console. Do NOT abandon the UI flow because of it.

1. Open the campaign → click its ad group → you land on the ad group
   view with tabs: `Ads | Targeting | Negative targeting | Search terms
   | Ad group settings | History`.
2. **These tabs are `<button>`s with NO href** (JS-driven), so
   `page_info()` shows them as bare text. Click them by **coordinates**:
   read the button's `getBoundingClientRect()` centre via
   `js("return …")`, then `click_at_xy(X, Y)`. Use this for the
   `Search terms` and `Negative targeting` tabs.
3. On the Search terms tab, each row has an `Add as` button
   (`id=…:spSearchTerms:cell-actions-<N>:actions`). It carries a
   **stable id**, so read its `getBoundingClientRect()` centre via
   `js("return …")` and `click_at_xy(x, y)` — a trusted click that
   opens a dropdown of three `role=option` buttons:
   `Add as keyword` (addAsKeyword),
   `Add as negative exact` (addAsNegativeExact), `Add as negative
   phrase` (addAsNegativePhrase). **ag-Grid virtualizes rows** — a term
   below the viewport (y > ~839) is not in the DOM, so scroll it into
   view first (`js("window.scrollBy(0, window.innerHeight)")` or scroll
   the grid container), re-read via `page_info()` to get its fresh
   `cell-actions-<N>` rect, then `click_at_xy`. Do not click an
   off-screen row; the dropdown won't open.
4. `click_at_xy` the negative-exact or negative-phrase option (read its
   rect first; per the report's match type). It **commits immediately —
   no save step.** The row's button label flips `Add as` → `Added as`.
5. The negated row **stays in place** (it does not vanish on commit), so
   you can walk straight down the visible list negating each waste term
   by its own `Add as` button. (If you re-read via `page_info()` or the
   grid re-sorts, the row rects shift — re-read then.)
6. Verify: open the `Negative targeting` tab → `Negative keywords`
   sub-tab; the term appears with its match type. (It's also fine to
   trust the `Added as` flip for throughput and spot-check the tab.)

This is all trusted `click_at_xy` — **never `js()`-click the `Add as`
button or the option.** A js() click is `isTrusted=false`, so the
dropdown/commit may not fire and you'll log a false success on a live
store.

**Preferred path when you already know the terms — the "Add negative
keyword" form (VERIFIED 2026-06-13; bypasses the dropdown
entirely and BATCHES):** the `Add as` dropdown sometimes renders its
options in a `#hmhFlyoverRoot` portal where a `click_at_xy` on the
`role=option` doesn't fire React (button never flips to `Added as`).
When you have the
exact terms to negate (e.g. from a report-derived allowlist), skip the
search-term grid and add them directly:
1. Campaign → ad group → **Negative targeting** tab (`click_at_xy` —
   it's a JS-driven button) → **Negative keywords** sub-tab.
2. Click **Add negative Keyword** (`click_at_xy`).
3. Pick the **Match type** radio — `NEGATIVE_EXACT` (default, safest:
   blocks only that exact query) or `NEGATIVE_PHRASE`. Match-type is
   per-batch, so group terms by the type you want.
4. Type the term(s) into the textarea
   (`…:kwp:kwp-enter-list-text-input-area`) — **one per line for a whole
   batch at once**: `fill_input("textarea[id$='kwp-enter-list-text-input-area']", "term a\nterm b\nterm c")`
   (if the Add button stays disabled, use the execCommand insertText
   workaround in § 8d).
5. Click **Add keywords** (stages them) → **Save** (commits). Both
   buttons are JS-driven — read each one's `getBoundingClientRect()`
   centre via `js("return …")`, then `click_at_xy(X, Y)`. **Never
   `js()`-click `element.click()`** — untrusted, the Save won't commit
   and you log a false success.
6. Re-read the Negative keywords list to confirm they appear (only then
   log ✅).
This is far faster than per-row dropdowns (no scroll/virtualization, no
portal) and is the right tool for executing an approved negation list.

**Negating an ASIN / product (VERIFIED 2026-06-15) — a DIFFERENT UI from
keyword negation.** The audit's `**否定该 ASIN**` rows (common on AUTO and
self-promotion campaigns — the row's first column is a `B0…` ASIN, not a
phrase) are NOT keywords and CANNOT be added via the Add-negative-keyword
form. They are **negative product targets**:
1. Campaign → ad group → Negative targeting tab → **Negative products**
   sub-tab (`click_at_xy`).
2. Click **Add negative product targets** (`click_at_xy`).
3. Choose **Exclude products** → **Enter list** sub-tab.
4. Paste the ASIN(s) into the list textarea (one per line, via
   `fill_input(...)`) → **Add** → **Save** (both `click_at_xy` after
   reading each rect; never `js()`-click `element.click()`).
5. Re-read the Negative products list to confirm, then log ✅.
**An approved-negation list mixes the two**: route each entry by shape —
`B0`-prefixed 10-char token → negative *product* (this flow); anything
else → negative *keyword* (the form above). Counting ASINs as keywords
(or vice-versa) is how a campaign gets falsely marked "done" while its
real negatives are still missing.

**Self-verify before claiming a campaign done.** `fill_input`/
coordinate-Save can silently fail (shifted coordinates, a portal, an
untrusted js() click that doesn't commit). After Save, ALWAYS re-read the
live Negative keywords AND Negative products lists and confirm the
count/terms match the approved set for that campaign BEFORE logging ✅ or
reporting completion. Do not trust that a Save persisted — verify it.

Search-terms gotcha: a prior campaign's search-box text **persists
across campaigns** — clear the box on entering each campaign's
Search-terms tab or you negate the wrong terms (or none).

On a production store, verify each change persisted (re-read the cell)
before moving on, and keep an EXECUTION_LOG so a capped session resumes
without redoing or skipping.

### 4a. When to consider it

Useful for re-flipping bid values across N existing campaigns at once,
or for pure structure-only operations that don't include Product Ad
rows.

### 4b. When to avoid it

**Never bulk-upload a Product Ad row using an ASIN as the SKU value.**
The validator silently rejects the Product Ad, and Amazon then drops
*every entity rooted under that ad group* (Ad Group, Product Ad, all
Keyword/Product Targeting rows). The Campaign and Bidding Adjustment
rows DO commit, leaving an empty paused shell that can't deliver. The
upload's status box shows `Failed` only after a long delay; campaign
list shows the empty shell well before that. Waiting 30+ minutes for
the upload-status field to flip to `Failed` is the norm.

**Looking up a child SKU.** If the user gives you a child SKU
(e.g. variation suffix like `<PARENT>-<VARIANT>`), open
`…/skucentral?mSku=<exactChildSku>&condition=New` directly. The
page resolves the child SKU even when the parent has zero
inventory and shows ASIN, FNSKU, and per-SKU sales/inventory tabs.
Do NOT search for the parent first and try to "expand variations"
on skucentral — that page only renders the SKU named in the
query, it has no expand-variations control. Manage-inventory's
list view also shows parent rows only, so searching `Product-Y-XYZ`
returns the parent line and looks like the child is missing.

For freshly listed products, the children sometimes use the
user's existing SKU scheme (e.g. a stable prefix + variant
suffix like `<PREFIX>-NNN`) and sometimes use Amazon-auto-
generated tokens (8-12 alphanumeric, dashed, e.g.
`<XX-XXXX-XXXX>`). When the SKU naming is unknown, ask the user
for the exact child SKU before browsing — guessing variation
suffixes wastes a lot of clicks.

If you suspect SKUs but want to verify before uploading, use the UI
campaign create form's product search — search by ASIN and inspect
the `SKU: …` line on the right-pane added-product card. That confirms
the seller-side SKU.

### 4c. Bulk Excel schema (Sponsored Products Campaigns sheet, 52 columns)

```
Product, Entity, Operation, Campaign ID, Ad Group ID, Portfolio ID, Ad ID,
Keyword ID, Product Targeting ID, Campaign Name, Ad Group Name,
Campaign Name (Informational only), Ad Group Name (Informational only),
Portfolio Name (Informational only), Start Date, End Date, Targeting Type,
State, Campaign State (Informational only), Ad Group State (Informational only),
Daily Budget, SKU, ASIN (Informational only), Eligibility Status (Informational only),
Reason for Ineligibility (Informational only), Ad Group Default Bid,
Ad Group Default Bid (Informational only), Bid, Keyword Text, Native Language Keyword,
Native Language Locale, Match Type, Bidding Strategy, Placement, Percentage,
Product Targeting Expression, Resolved Product Targeting Expression (Informational only),
Audience ID, Shopper Cohort Percentage, Shopper Cohort Type,
Segment Name (Informational only), Impressions, Clicks, Click-through Rate,
Spend, Sales, Orders, Units, Conversion Rate, ACOS, CPC, ROAS
```

Key columns by entity type:

- **Campaign**: Campaign Name, Start Date (`YYYYMMDD`), Targeting Type
  (`Auto`/`Manual`), State (`enabled`/`paused`), Daily Budget,
  Bidding Strategy.
- **Bidding Adjustment**: Campaign Name, Bidding Strategy, Placement
  (`Placement Top`/`Placement Product Page`/`Placement Rest Of Search`),
  Percentage.
- **Ad Group**: Campaign Name, Ad Group Name, State, Ad Group Default Bid.
- **Product Ad**: Campaign Name, Ad Group Name, State, **SKU** (real
  seller SKU — not ASIN).
- **Keyword**: Campaign Name, Ad Group Name, State, Bid, Keyword Text,
  Match Type (`broad`/`phrase`/`exact`).
- **Product Targeting** (manual category): Campaign Name, Ad Group
  Name, State, Bid, Product Targeting Expression
  (`category="<exact category name>"`).
- **Product Targeting** (auto): Campaign Name, Ad Group Name, State,
  Bid, Product Targeting Expression (`close-match`/`loose-match`/
  `substitutes`/`complements`).

Operation = `Create` for new entities; `Update` to modify;
`Archive` to archive.

### 4d. Bulk download to learn the store's SKU naming scheme

Run a `Download campaigns` job, parse the resulting XLSX's
`Sponsored Products Campaigns` sheet, filter `Entity == 'Product Ad'`
to read each existing campaign's `SKU` column. That's the canonical
source of the seller's SKU naming scheme for that store.

## 5. Coupons

### 5a. Eligibility ≠ inventory

A SKU being Active and in stock is **not** sufficient for it to appear
in the coupon-creation product search. Amazon's coupon eligibility
gate is opaque and account-aware. Observed behavior:

- **One marketplace**: New listings (recently added, no review
  history) may NOT be eligible. The coupon search can return
  `No eligible ASINs found` for **every** product — including older
  ones with running coupons — when the account is in a "no new
  coupons" state. This may be account-level (e.g. a recent program
  warning) or temporary.
- **Another marketplace of the same unified account**: the same SKU
  is often eligible immediately after listing.

**On a multi-marketplace (unified) account, if one marketplace's
coupon search returns no eligible ASINs, try the account's other
marketplace(s)** before concluding the SKU can't run a coupon. Run
the coupon in that marketplace's currency.

### 5b. Coupon dashboard URLs

```
https://sellercentral.amazon.<tld>/coupons/dashboard
```

The dashboard lists existing coupons with status pills, dates, budget,
budget-utilization. Two top-right buttons: `Create coupons in bulk`
and `Create a new Coupon` — both inside shadow DOM. Walk
`document → element.shadowRoot` recursively to find them.

### 5c. Three-step form

`/coupons/create-coupon` is a 3-step wizard:

1. **Products** — choose coupon type (Standard), Audience Type (All
   customers), search and check products.
2. **Details** — schedule (start/end), discount type (Money off /
   Percentage off), discount amount, redemption-limit checkbox,
   budget, coupon title (max 150 chars), stacked-promotions radio
   (Yes/No allow stacking).
3. **Review** — final review screen, then submit.

### 5d. Step 1: Products

Search input placeholder: `Search by product name or ASIN`. Search by
SKU works too. The result row has a checkbox at the far left
(usually a `[role=checkbox]` div, not a native `<input>`); click it
to select. The right side counter `Participating products (N)` updates.

### 5e. Step 2: Details — the kat-input wall

This is where DOM-scripting hits a wall. The Amazon-built kat-input
components for `discount-value`, `budget`, and `start-date` accept
your value visually — `i.value` reads back what you set — but the
React state behind them stays "empty/invalid", so **the `Continue`
button stays `disabled=true`**.

Things tried that DON'T enable the button:

- `Object.getOwnPropertyDescriptor(...,'value').set.call(...)` + `input`
  + `change` + `blur` events.
- Setting via the kat-input wrapper's attributes (`value`, `valuetext`).
- `fill_input("<selector>", "10")` (which simulates real typing).
- `press_key("Control+a")` then re-type then `press_key("Tab")`.
- Clicking outside, scrolling, refreshing.

What WOULD work (untested at finish): manual click + click + click +
keyboard-typed digits with explicit per-keystroke events. Real user
typing engages the kat focus → blur → React commit cycle properly.
For the new-product-launch flow, it's faster to **hand the coupon to
the user** for the 30-second manual completion than to keep grinding
on this.

What CAN be set programmatically:

- The `Money off`/`Percentage off` radio (kat-radiobutton with
  `name=discount-type`).
- The stacking radio (`name=combinability`).
- The end-date kat-date-picker (via clicking a day cell after opening,
  not via setting the inner input).

What CANNOT be reliably set:

- The amount kat-numberinputs (`discount-value`, `budget`).
- The start-date kat-date-picker — even when the kat element's
  `value` attribute reads `MM/DD/YYYY`, the inner display stays empty
  until a real user clicks a day cell.
- The coupon-title kat-textinput — sometimes accepts the setter,
  sometimes doesn't, depending on whether the field has been
  user-focused on the page in this session.

### 5f. Pragmatic coupon flow for the agent

Do step 1 (product selection) end-to-end in the agent. Cancel before
step 2 OR save as far as you can and tell the user "click Continue
and type the 4 numbers" — those 4 are: discount=10, budget=1000,
start-date=today (click day in picker), coupon title=<short generic
product name>. The user finishes in <60s.

Do not try to launch a coupon from the agent without explicit
"go ahead" from the user — coupons spend money and have a 24h
review window before going live.

### 5g. Coupon discount, budget, duration defaults

Per the user's standing rule for new-product launches:

- Discount: 10 (in the marketplace's currency).
- Budget: 1000 (same currency).
- Duration: 30 days from today (Amazon caps at 30; trying 31 fails).
- Title: do NOT copy a sister coupon's exact title — Amazon flags
  duplicates across accounts.

## 6. Recovery from a wedged daemon

If `browser-use` calls return `TimeoutError: timed out` or
`Client is stopping`:

1. `pkill -9 -f "skill_cli.daemon.*<store>-<8hex>"` — kill the wedged
   per-store daemon process.
2. Re-issue a heredoc against the wrapper — the next call recreates the
   daemon and the wrapper restarts the CDP proxy if needed:
   ```bash
   ~/.vibe-seller/bin/<store>/browser-use <<'PY'
   new_tab("<url>")
   wait_for_load()
   PY
   ```

The Ziniao Chrome itself stays up; you don't need to restart it.
Only the per-store `skill_cli.daemon` Python process is the wedge
target.

To open a non-seller URL (public Amazon page, docs, etc.) use
`--session <store>-aux` — see the system prompt's DUAL BROWSER
section.

## 7. Per-store conventions to follow

- **Naming:** existing campaigns on a store typically use
  `<sku> <targeting type> <market>` where market is the country code
  (e.g. `US` or `UK`).
  When the agent creates a campaign, append ` - agent` (with surrounding
  spaces) so the user can filter for review. Add ` v2`/`v3` only when
  Amazon rejects for name conflict.
- **Bidding strategy default:** `Dynamic - down only` (`legacy` in
  the form payload) for the new-product-launch flow, matching the
  user's standing preference.
- **Placement adjustments:** all 0% by default. Only set non-zero
  when explicitly captured from a sister campaign.
- **Budget/bid currency:** matches the marketplace. The form's
  `aria-label="enter amount in <CURRENCY>"` can show a different
  currency than the active marketplace — ignore the label and trust
  the marketplace.

## 8. Reading existing campaigns for tuning

For the ad-tuning workflow (see `tuning-workflow.md` reference in
this same skill), these UI surfaces are where the data lives. All
click paths verified.

### 8a. Marketplace switching (Seller Central)

Multiple marketplaces under one seller account share a login. To
switch from one marketplace to another (any pair):

1. From `sellercentral.amazon.<tld>/home`, click the **country pill**
   near the top-left of the header (next to the `amazon seller
   central` logo). Text is the current country name (e.g. "United
   States"). A small dropdown appears with `See all` link.
2. Click `See all` → navigates to
   `sellercentral.amazon.<tld>/account-switcher/default/merchantMarketplace`.
3. The page lists all linked marketplaces. Click the destination
   row (e.g. "United Kingdom") — row highlights with a
   checkmark.
4. Click `Select account` button (bottom-right).
5. Page redirects back to seller-central home with new
   `mons_sel_mkid=<mp-id>` query param. The page header pill now
   shows the new country.
6. Some accounts have **separate ad entities per marketplace**. One
   marketplace uses `merchantId=<X>` + `entityId=<Y>` while another
   uses different ones. The Campaign Manager menu link reads the active marketplace
   and routes accordingly. **Do NOT** copy a campaign URL across
   marketplaces — the entityId won't match.

### 8b. Campaign list view (advertising.amazon.<tld>/campaign-manager)

Reach via `Campaign Manager` link in seller central menu (never type
`advertising.amazon.<tld>/...` directly on Ziniao-backed stores —
gates).

Key UI elements:

- **Date range picker** (page header) — has presets `Today`,
  `Yesterday`, `Last 7 days`, `Last 30 days`, `Last Week`,
  `This Week`, `Last Month`, `This Month`, `Year to Date`,
  `Lifetime`. Apply via the 3-step click sequence in the
  date-picker subsection below.
- **Table-level date filter** (bottom of campaign table, near
  Export) — independent of the page header. Defaults to a rolling
  ~30 days (e.g. "YYYY-MM-DD - YYYY-MM-DD"). For tuning, this is usually the
  filter that matters; the page-header one drives only the top
  metric tiles.
- **Pre-built tuning filter chips** above the table: `Targets with
  impressions and no sales`, `Targets with clicks and no orders`,
  `Targets with conversions`. One-click pre-filtered tuning cuts.
- **Default columns include** `Active`, `Campaign name`, `Status`,
  `Type`, `Campaign start date`, `Campaign end date`, `Campaign
  budget amount`, `Top-of-search impression share`, `Top-of-search
  bid adjustment`, `Clicks`, `CTR`, `Total cost`, `CPC`,
  `Purchases` (= orders), `Sales`, `ACOS`, `ROAS`. **The default
  view is sufficient for most tuning analysis — no Columns toggle
  needed.**
- **Status values that matter**: `Delivering`, `Out of budget`,
  `Paused`, `Ended`, `Archived`. `Out of budget` = Amazon has hit
  the daily budget cap recently (signal to consider raise *only
  after* tuning ACOS, not before).
- **Row-name link href** points to `/cm/sp/campaigns/<id>/ad-groups`.

#### Extracting campaign-list metrics — DO THIS, not that

The campaign-list table renders inside an **ag-Grid that
virtualizes both rows AND columns**. Trying to read every column
via a single `js()` call is unreliable: at any scroll position, ~6 of 17
columns are in the DOM, the rest are placeholder cells. Verified
in the wild — an agent burnt ~50 tool calls trying to scroll +
re-`js()` + disable virtualization + `capture_screenshot()` + bulk-export
before finding a path that worked. *Don't repeat it.*

**The reliable path: per-campaign drill.** Extract every campaign's
detail-page URL from the list, then iterate.

```bash
# Step 1 — read all SP / SB / SD campaign URLs from the list page.
# (`/cm/sp/`, `/cm/sb/`, `/cm/sd/` cover Sponsored Products, Sponsored
# Brands incl. Brand Video, and Sponsored Display respectively — see
# the URL table at top of § 1.) This works regardless of horizontal
# scroll because the campaign-name column is the pinned-left column.
browser-use <<'PY'
js("""
var urls = [];
document.querySelectorAll('a[href*="/cm/sp/campaigns/"], a[href*="/cm/sb/campaigns/"], a[href*="/cm/sd/campaigns/"]')
  .forEach(a => { if (a.href && !urls.includes(a.href)) urls.push(a.href); });
return JSON.stringify(urls);
""")
PY
# Returns the deep-link list. Save to /tmp/<run>/campaign-urls.json.

# Step 2 — for each URL: open + read top-tile metrics via the
# chart's metric-toggle buttons (their innerText carries the value).
browser-use <<'PY'
new_tab("<url>")
wait_for_load()
js("""
return (() => {
  var r = {};
  document.querySelectorAll('button[pressed], button[aria-pressed="true"]').forEach(b => {
    var t = b.innerText.trim();
    if (t) r['metric_' + Object.keys(r).length] = t;
  });
  return JSON.stringify(r);
})();
""")
PY
# Returns e.g. {metric_0:'<ccy x.xx>', metric_1:'<ccy y.yy>',
#               metric_2:'<roas>', metric_3:'<orders>'}
# meaning Total cost / Sales / ROAS / Purchases — in that order
# because the chart's button row is fixed.
```

**Why this works.** The campaign-detail page's 4 top tiles are
metric-toggle buttons for the chart (clicking one switches the
chart series). Each button's `innerText` contains both the label
("Total cost") AND the formatted value (e.g. `<ccy 100.00>`) —
pinning order doesn't matter for tuning analysis as long as the
four fixed positions map to Total cost / Sales / ROAS / Purchases.

**Date range gotcha.** Each campaign-detail page has its OWN date
picker, and the default differs per campaign (one detail page
lands on a 30-day window ending mid-month, the next on a window
ending today). Setting the date range on the campaign-list page
does NOT propagate to the detail pages. Two acceptable strategies:

1. **Accept per-page defaults**, capture each campaign's actual
   date window into the report alongside its metrics, surface the
   misalignment in the *Defaults applied* line. Faster.
2. **Set each detail page's picker** to the session window before
   reading. Slower (one extra click per campaign + verify) but
   produces clean apples-to-apples comparison.

For first-pass reads, (1) is fine; for high-stakes recommendations
that depend on cross-campaign comparisons, escalate to (2).

**Date-picker click sequence.** Both campaign-manager and
per-campaign detail pages require a multi-step click to apply a
preset:

```bash
# 1. Click the date display button to open the picker
browser-use <<'PY'
js("""
return (() => {
  var btns = document.querySelectorAll('button');
  var d = null;
  btns.forEach(function(b) {
    if (b.innerText.match(/<current displayed date string>/)) d = b;
  });
  if (d) { d.click(); return 'opened'; }
})()
""")
PY

# 2. Click a preset leaf by walking up to the parent button
browser-use <<'PY'
js("""
return (() => {
  var els = Array.from(document.querySelectorAll('*'));
  var leaf = els.find(function(e) {
    return e.children.length === 0 &&
      (e.textContent || '').trim() === 'Last 30 days' &&
      e.offsetParent !== null;
  });
  if (leaf) {
    (leaf.closest('button, kat-button, [role=button]') || leaf).click();
    return 'clicked';
  }
})()
""")
PY

# 3. Apply — only needed on some page variants (campaign-manager
#    list view). Detail pages auto-apply after preset click.
sleep 3
browser-use <<'PY'
js("""
return (() => {
  var b = Array.from(document.querySelectorAll('button'))
    .find(function(x) {
      return x.innerText.trim() === 'Apply' && x.offsetParent !== null;
    });
  if (b) { b.click(); return 'apply'; }
  return 'auto-applied';
})()
""")
PY
```

The visible "Last 30 days" text is a leaf inside a clickable
parent — must walk up via `closest('button')`. Two pickers
coexist on the campaign-manager page (table-filter + chart-range);
filter by `offsetParent !== null` to find the visible one.

**When to fall back to bulk export instead.** If the portfolio is
> ~25 active campaigns, the per-campaign-drill loop costs ~25 ×
3-5 s = 1-2 min per country, plus extra time for each ad-group
drill. The bulk download (§ 2d) takes 5-15 min total but emits
ALL data in one XLSX. Tradeoff: drill is faster for ≤ 20
campaigns; bulk is faster for portfolios beyond that. **Don't
attempt ag-Grid scraping in either case.**

#### Second-level drill — ad-group Targeting + Search-terms tabs (REQUIRED)

Top tiles only give campaign-aggregate spend/sales/orders/ROAS.
Per-keyword / per-auto-target-group / per-search-term data lives
one level deeper: the **ad group's Targeting and Search-terms
tabs**. Without this drill, every "check 4 auto-target-groups",
"identify high-ROAS search terms", and "trim bid drift" finding
is impossible to back with data — the report devolves into
generic "needs check" notes. Verified in the wild: an agent run
emitted *"自动广告需检查4个target group"* for 4 SP-Auto campaigns
without ever capturing the 4 rows.

**Click path — same pattern for SP and SB.** Tabs in the ad-group
strip use a JS router (no `<a href>` per tab), so direct URL
deep-link is the reliable navigation:

```
…/cm/{sp|sb}/campaigns/<campId>/ad-groups/<agId>/targeting?entityId=…
…/cm/sb/campaigns/<campId>/ad-groups/<agId>/search-terms?entityId=…
```

Find `<agId>` from the campaign's ad-groups page (already loaded
in step 2 of § 8b above):

```bash
browser-use <<'PY'
js("""
return [...document.querySelectorAll('a')]
  .filter(a => a.href && a.href.includes('ad-groups') && a.href.includes('targeting'))
  .map(a => a.href);
""")
PY
# Each href encodes the ad-group ID. For multi-ad-group campaigns
# the agent iterates all of them; most SP-Auto / SBV campaigns
# have one ad group.
```

**Auto campaign ad group ID ≠ campaign ID.** For auto
campaigns, the ad group ID ALWAYS differs from the campaign ID.
Navigating to `…/campaigns/<campId>/ad-groups/<campId>/targeting`
renders a blank page. Extract the real ad group ID from the
campaign detail page:
```bash
browser-use <<'PY'
js("""
var links = document.querySelectorAll('a[href*="/ad-groups/"]');
var agIds = [];
links.forEach(function(l) {
  var m = l.href.match(/\\/ad-groups\\/([^?/]+)/);
  if (m && agIds.indexOf(m[1]) === -1) agIds.push(m[1]);
});
return JSON.stringify(agIds);
""")
PY
```

**ag-Grid pinned-left + center merge for Targeting tab.**
The Targeting tab's ag-Grid renders keyword text in a
**pinned-left container** (`.ag-pinned-left-cols-container`)
while metric columns render in the **center container**
(`.ag-center-cols-container`). `innerText` on the full row
misses the keyword because it's in a different DOM subtree.
Merge by `row-index`:

```bash
browser-use <<'PY'
js("""
return (() => {
  var pc = document.querySelector('.ag-pinned-left-cols-container');
  var pr = pc ? pc.querySelectorAll('[role=row]') : [];
  var bc = document.querySelector('.ag-center-cols-container');
  var br = bc ? bc.querySelectorAll('[role=row]') : [];
  var result = [];
  pr.forEach(function(row) {
    var idx = row.getAttribute('row-index');
    if (idx) {
      var kw = row.innerText.trim().replace(/\\n/g, ' ');
      br.forEach(function(b) {
        if (b.getAttribute('row-index') === idx) {
          var met = b.innerText.trim().replace(/\\n/g, ' | ');
          if (met) result.push(kw + ' ||| ' + met);
        }
      });
    }
  });
  return result.join('@@@');
})()
""")
PY
```

Use `@@@` as delimiter (not `\n`) because `js()` (like the old
`eval`) only tags the first line of a printed result — newlines drop
subsequent rows from shell pipelines.

**Canonical column layout — SP-Manual-Keyword Targeting tab.**
After splitting the metric string on ` | `, the cell ordering is
fixed and **must be indexed by position**, not by parsing currency
symbols. Verified-in-the-wild defect: agents read the *first*
`<ccy> x.xx` value (position 2) as the bid and recorded the
suggested-bid midpoint as if it were the keyword's bid, then
proposed bid changes "from the suggested midpoint" — which is a
no-op.

```
position 0:  Status                 ("Delivering" | "Paused" | ...)
position 1:  (empty spacer)         (always "")
position 2:  Suggested bid + range  ("USD 0.60 (USD 0.45-USD 0.75)")
position 3:  Apply button           ("Apply")
position 4:  Rules indicator        ("—" when no rules)
position 5:  Bid                    ("USD 3.00")   ← THIS is the bid
position 6:  Bid (duplicate)        ("USD 3.00")   ← same value, alt render
position 7:  Clicks                 ("200")
position 8:  Spend                  ("USD 500.00")
position 9:  Orders                 ("30")
position 10: Sales                  ("USD 1,200.00")
position 11: ACOS                   ("41.67%")
position 12: ROAS                   ("2.40")
```

Worked example (verified against `A33333333` / wireless
mouse Broad): the row text returned by the merge eval is

```
wireless mouse ||| Delivering |  | USD 0.60 (USD 0.45-USD 0.75) | Apply | — | USD 3.00 | USD 3.00 | 200 | USD 500.00 | 30 | USD 1,200.00 | 41.67% | 2.40
```

Correct extraction:
- `keyword = "wireless mouse"` (left of `|||`)
- `cells = right.split(" | ")` (length 13)
- `status = cells[0]`            → `"Delivering"`
- `suggested = cells[2]`         → `"USD 0.60 (USD 0.45-USD 0.75)"`
  - Parse: midpoint `0.60`, low `0.45`, high `0.75`
- `bid = cells[5]`               → `"USD 3.00"` (parse → `3.00`)
- `clicks = cells[7]`            → `200`
- `spend = cells[8]`             → `500.00`
- `orders = cells[9]`            → `30`
- `sales = cells[10]`            → `1200.00`
- `acos = cells[11]`             → `0.4167`
- `roas = cells[12]`             → `2.40`
- `actual_cpc = spend / clicks`  → `2.50`

**Common defect pattern**: grabbing the first currency value via
regex (`/<ccy> ([\d.]+)/`) returns `0.60` because Suggested-bid
appears before Bid in the row text. The midpoint of the
suggested-bid range is NOT the keyword's current bid. Index by
position, not by "first currency value".

If the row contains paused keywords with `0` clicks, positions
7–12 may render as `—` instead of numeric values; the position
layout is still the same.

**SP-Auto Targeting tab — capture the 4 auto-target-groups.**
The page uses `[role=row]` (no `<table>`). Group labels (Close
match / Substitutes / Loose match / Complements) and data rows
are sibling rows, paired positionally. The data row's last 7
cells map cleanly to bid / clicks / spend / orders / sales /
ACOS / ROAS regardless of currency or row state. The pinned-left
+ center merge eval above also works here — group names appear
in the pinned-left container.

```bash
browser-use <<'PY'
js("""
return (function() {
  const rows = [...document.querySelectorAll('[role=row]')];
  const LABELS = ['Close match', 'Loose match', 'Complements', 'Substitutes'];
  const labelRows = rows.filter(r => LABELS.includes(r.innerText.trim()));
  const dataRows = rows.filter(r => {
    const t = r.innerText.trim();
    return (t.startsWith('Delivering') || t.startsWith('Paused'));
  });
  return JSON.stringify(labelRows.map((lr, i) => {
    const cells = (dataRows[i]?.innerText || '').split('\\n').map(c => c.trim()).filter(Boolean);
    return {
      group:  lr.innerText.trim(),
      status: cells[0],
      bid:    cells.at(-7), clicks: cells.at(-6), spend: cells.at(-5),
      orders: cells.at(-4), sales:  cells.at(-3), acos:  cells.at(-2),
      roas:   cells.at(-1)
    };
  }));
})()
""")
PY
```

Paused rows with no impressions return `—` for clicks/spend/etc.
— that's the correct truthy answer; preserve in the report as
`—` rather than 0.

**Targeting tab metric column ORDER can differ between marketplaces.**
The center-container metric columns vary by marketplace for
both SP-Manual and SP-Auto campaigns — both the order AND which
metrics are present (e.g. one marketplace may include
**Impressions** and lead with ACOS; another may include **Sales**
and lead with Clicks):

| Marketplace | Metric column order (after Bid) |
|---|---|
| `<marketplace A>` | `Clicks \| Total cost \| Purchases \| Sales \| ACOS \| ROAS` |
| `<marketplace B>` | `ACOS \| Clicks \| Purchases \| ROAS \| Impressions \| Total cost` |

**Do not assume a fixed column order. Read the header row and match
columns by their header text** — column-index-based extraction
(e.g. `cells.at(-7)`) produces **silently wrong data** when the
script runs against a marketplace whose layout differs. Use the
`innerText` split approach or verify column headers before picking
indices.

**New campaigns show "—" metrics when date range predates
creation.** Campaign detail pages default to a date range set
at the campaign-manager level. Campaigns created after the
range end date show all metrics as "—". Change the detail
page's date range to include the campaign's start date.

**SB / SBV Targeting tab.** Layout differs from SP — uses
`[role=grid]` with a horizontally-split dual-pane: `grids[2]` =
left pane (keyword text), `grids[3]` = right pane (metrics).
Paused rows insert a "Details" token after status that must be
skipped by value, not by fixed offset.

```bash
browser-use <<'PY'
js("""
return (function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const kwLines = (grids[2]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const metLines = (grids[3]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const keywords = kwLines.filter(l => l !== 'select');
  const MATCH = new Set(['Phrase', 'Exact', 'Broad']);
  const rows = [];
  let i = 0;
  while (i < metLines.length && rows.length < 200) {
    if (!MATCH.has(metLines[i])) { i++; continue; }
    const match_type = metLines[i++], status = metLines[i++];
    if (metLines[i] === 'Details') i++;
    if (metLines[i] === 'No current data') i++;
    if (metLines[i] === 'adjust value') i++;
    if (metLines[i] === 'SAR' || metLines[i] === 'AED' || metLines[i] === 'USD') i++;
    rows.push({
      keyword: keywords[rows.length] || null,
      match_type, status,
      top_search_IS: metLines[i++], cpc: metLines[i++], clicks: metLines[i++],
      spend: metLines[i++], orders: metLines[i++], sales: metLines[i++],
      acos: metLines[i++], roas: metLines[i++], ntb: metLines[i++]
    });
  }
  return JSON.stringify(rows);
})()
""")
PY
```

SB Targeting columns (in order): keyword / match_type / status /
top-of-search IS% / **CPC** / clicks / spend / orders / sales /
ACOS / ROAS / **NTB%** (new-to-brand — SB-only). Earlier skill
notes claimed a "DPV" column existed here; live verification on
SBV showed only the 12 columns above — no separate DPV.

**SB / SBV Search-terms tab.** Same dual-grid pattern but the
right-pane row is a flat 5-cell sequence (clicks/spend/orders/
sales/ACOS — no ROAS, no DPV).

```bash
browser-use <<'PY'
js("""
return (function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const left  = (grids[2]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const right = (grids[3]?.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const leftRows = [];
  for (let i = 0; i + 2 < left.length; i += 3) {
    leftRows.push({ search_term: left[i], target_keyword: left[i+1], match_type: left[i+2] });
  }
  const FIELDS = 5;
  const metRows = [];
  for (let i = 0; i + FIELDS <= right.length; i += FIELDS) {
    metRows.push({ clicks: right[i], spend: right[i+1], orders: right[i+2], sales: right[i+3], acos: right[i+4] });
  }
  return JSON.stringify(leftRows.map((lr, i) => ({...lr, ...(metRows[i] || {})})));
})()
""")
PY
```

**Search-terms virtualizes on SP and SB alike.** The page header
shows the `Total: N` count. If N > what the eval returned, the
rest are below the fold. Verified: SP-Auto returned 13 of 35 on
first eval; SP-Manual returned 15 of 47; SBV returned 23 of 48.
Either click `Export` (file lands in `~/.vibe-seller/downloads/
<store>/`) or scroll the inner grid container and re-eval until
you see all `N` rows. **Don't trust a truncated capture** — the
top-spending tail you missed is often where the negate / harvest
candidates live.

**SP-Manual Targeting tab does NOT render Match type in the row
DOM.** The header omits the column entirely (verified live: row
text is `<status> / <suggested-bid> / <bid> / <clicks> / ...`).
The Search-terms tab on SP shows the source keyword's match type
in the center container (as `Match type: Broad|Phrase|Exact`),
so match type IS available via the Search-terms merge eval (see
§ 8f). But for a direct Targeting tab match-type read without
cross-referencing Search-terms, CSV export is required.
**Match type recovery for SP-Manual Targeting tab = CSV export
or cross-reference from Search-terms tab.** Use the per-table
CSV Export button on the Targeting tab (§ 2c) or the bulk-sheet
download (§ 2d). Both include the `Match type` column.

**SB / SBV campaign-detail top tiles use a different DOM than
SP.** SP exposes the four tiles (Total cost / Sales / ROAS /
Purchases) as `button[pressed]` toggles for the chart series
(per § 8b step 2 above). SB's tiles are **static, with the label
on its own line and the value on the next line concatenated with
a `TOTAL` / `AVERAGE` suffix** — and the value embeds the
currency prefix or `%` sign (`USD96.00TOTAL`, `80.01%AVERAGE`).
The pressed-button eval gets ~0–2 of these. Use a line-pair
scan:

```bash
browser-use <<'PY'
js("""
return (function() {
  const lines = document.body.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
  const tiles = {};
  for (let i = 0; i < lines.length - 1; i++) {
    // Label is a Title-Case phrase; value line ends in TOTAL or AVERAGE.
    const labelLooksOk = /^[A-Z][A-Za-z ]{2,30}$/.test(lines[i]);
    const m = lines[i + 1].match(/^([A-Z]{3}\\s*)?([\\d,\\.]+)%?(TOTAL|AVERAGE)$/);
    if (labelLooksOk && m) tiles[lines[i]] = lines[i + 1];
  }
  return JSON.stringify(tiles);
})()
""")
PY
```

Returns e.g. `{"Total cost":"USD96.00TOTAL", "Sales":"USD119.98TOTAL",
"ACOS":"80.01%AVERAGE", "Impressions":"6,090TOTAL"}`. Strip the
trailing `TOTAL`/`AVERAGE` and currency prefix when assembling
the report. The set of tiles SB shows varies by Goal / Cost type —
`Drive page visits` and `Drive sales` expose different tiles —
so don't hard-code a fixed set.

**Date range — capture per-page**, since each detail page has its
own picker that defaults differently per campaign. Two patterns
exist depending on page type. Try them in order:

```bash
browser-use <<'PY'
js("""
return document.body.innerText.match(/Date range:\\s*[A-Za-z0-9 ,\\-–]+\\d{4}/)?.[0] ||
document.body.innerText.match(/Data ranges from\\s+(\\d{4}-\\d{2}-\\d{2}).*?to\\s+(\\d{4}-\\d{2}-\\d{2})/s)?.[0]?.replace(/\\s+/g,' ') ||
document.body.innerText.match(/\\d{1,2}\\s+[A-Z][a-z]{2}\\s*[-–]\\s*\\d{1,2}\\s+[A-Z][a-z]{2},?\\s*\\d{4}/)?.[0] ||
'unknown';
""")
PY
```

The first pattern catches SB campaign-detail ('Date range: 22 Apr
- 8 May 2026'). The second catches SP campaign-detail (chart
ARIA text 'Data ranges from 2026-04-23 00:00:00 to 2026-05-08
00:00:00'). The third is a generic fallback. Surface the captured
window in each campaign section's data header so the reader knows
which dates the metrics apply to. For cross-campaign comparison
work where misaligned windows would mislead, set each detail
page's picker to the session window before reading.

### 8c. Campaign detail page

URL: `/cm/sp/campaigns/<campaign-id>/ad-groups`.

Left sidebar (campaign-level tabs):
- **Ad groups** (default landing) — list of ad groups inside this
  campaign with per-group metrics (default bid, total targets,
  products, total cost, purchases, sales, ACOS, ROAS).
- **Bid adjustments** — per-placement modifier table (Top of search,
  Rest of search, Product pages); see `8e`.
- **Negative targeting** — campaign-level negative keywords +
  negative product targets, two sub-tabs.
- **Budget rules** — scheduled budget bumps for events (Black
  Friday, Ramadan, etc.).
- **Campaign settings** — name, schedule, daily budget, bidding
  strategy.
- **History** — change log.

Top of page: 4 metric tiles (Total cost, Sales, ROAS, Purchases) +
performance chart over the date range.

A `js("…click()")` on these sidebar buttons OFTEN fails — they're
shadow-DOM kat-button components whose React handlers don't fire
on synthetic click events. **Use coord-click** (`click_at_xy(x, y)`)
instead. Find coordinates via element bounding-rect
(`js("return …getBoundingClientRect()")`) first.

### 8d. Ad group detail page

URL: `/cm/sp/campaigns/<campaign-id>/ad-groups/<ad-group-id>/ads`.

Left sidebar (ad-group level):
- **Ads** (default landing) — list of advertised products (SKUs).
- **Targeting** — keywords + product targets table; columns include
  `Active`, `Keyword`, `Match type`, `Status`, `Suggested bid` (+
  Apply button), `Bid`, `Clicks`, `CTR`, `Total cost`, etc.
- **Negative targeting** — ad-group level negatives.
- **Search terms** — the customer-query report; see `8f`.
- **Ad group settings** — default bid, ad group bidding rule.
- **History** — change log.

### 8d-2. Creating a new ad group in an existing campaign

URL: `/cm/sp/campaigns/<campaign-id>/ad-groups`.

Click the **"Create ad group"** button on the ad-groups page. A
full-page creation form opens at URL pattern `/cb/sp/campaigns/<id>/adGroups`
(same form as campaign creation's ad group step, but scoped to this
campaign).

**Form fields:**
1. **Ad group name** — input `#sspa_sp_adGroupSettings_adGroupName`.
   Pre-filled with a timestamp; overwrite with the naming convention.
2. **Products** — search by ASIN/SKU, click "Add" on the matching row.
   **Use `click_at_xy` on the "Add" button** (read its rect first), not
   a `js("…click()")` — the React form state may not register
   js-triggered adds.
3. **Default bid** — input `#sp-defaultBid`. Pre-filled with Amazon's
   suggested bid (may be higher than expected, e.g. USD 4.00 vs
   target USD 2.00).
4. **Targeting type** — **CRITICAL**: the form defaults to **product
   targeting** even in manual keyword campaigns. You MUST explicitly
   click the `manualTargetingType-KEYWORD` radio button via `click_at_xy`
   (not a `js("…click()")`) to enable keyword targeting. Without this, the
   ad group will have a product-targeting tab instead of keyword
   targeting, and "Add keywords" won't work.
5. **Keywords** (after setting targeting type to keyword) — type
   keywords and set per-keyword bids.

**Shadow DOM bid pitfall.** When adding keywords with per-keyword
bids, the bid input cells (e.g. `#kwp:kwp-bid-cell-field-0`) are
inside `|SHADOW(open)|` elements. Using `fill_input` sets the
DOM `.value` but the SPA framework (React) does not register the
change — the form still considers the bid as empty/missing. On
submit, validation fails with "Required" on the bid field.

**Workaround — click the suggested bid "Apply" button:**
If the per-keyword row shows a suggested bid with an "Apply" link,
clicking it is a framework-native action that sets the bid correctly.
Prefer this when the suggested bid is acceptable. This is more
reliable than the nativeInputValueSetter approach in the creation
form context.

**Do NOT run `js(...)` on the main page while the creation
form is open.** The SPA may interpret the js() call as navigation and
redirect to `about:blank`, losing all form state. Use `page_info()`
for reading and `fill_input`/`click_at_xy` for interacting — both are
safe.

**Form state caching.** After creating an ad group, Amazon's SPA may
cache the creation form state. Subsequent navigations to the
campaign's ad-groups page may redirect back to the creation form
(`/cb/sp/...`). Recovery: navigate to `campaign-manager/all-campaigns`
first, then back to the campaign.

### 8e. Bid Adjustments page (placement modifiers)

URL: `/cm/sp/campaigns/<campaign-id>/bid-adjustments`.

Page header text: **"Increase your bid for specific placements.
These placements include Amazon Business."**

Three default placement rows:
- **Top of search (first page)** — usually the highest-CTR
  placement.
- **Rest of search** — middle of search results.
- **Product pages** — appearing on competitor PDPs.

Columns: Placement Name, Campaign bid strategy, Bid adjustment
(editable %), Impressions, Clicks, CTR, Total cost, CPC, Purchases,
Sales, ACOS.

**Date range — read this BEFORE reading the table.** This page has
its OWN date-range picker in the page header (e.g. `YYYY-MM-DD - YYYY-MM-DD,
2026`). It is **independent** of the date range you set on the
campaign-list page or on the campaign top-tile. Defaults can drift
across pages and across visits. The placement breakdown only
reconciles to the campaign top-tile (impressions, clicks, spend,
orders, sales, ACOS sums match) **when both pages are set to the
exact same date range.**

Reconciliation example (illustrative numbers): with the page-header
date picker pinned to a 30-day window, summing the per-placement
Purchases / Sales / Total cost columns yields the same totals as the
campaign-detail top tiles (e.g. `<X> orders / <ccy Y.YY> sales /
<Z%> ACOS` matches to the cent). With a different date filter (or
the default rolling window), the per-placement orders sum to less
than the campaign top-tile and the analyst incorrectly concludes
there's an "irreducible attribution gap". There isn't — align the
date ranges first.

**Required workflow on this page:**
1. Read the date range shown in the page header.
2. Compare to the date range used for the campaign top-tile reading.
3. If they differ, click the page-header date picker on this page,
   set it to match, wait for the table to refresh, then read.
4. Sum the per-placement Purchases / Sales / Total cost columns and
   verify they equal the campaign top-tile. If they don't, the dates
   still aren't aligned (or the page hasn't finished refreshing).

**Editing the % cell**: click the `0%` button → kat-numberinput
edit popup appears with `Save` / `Cancel` buttons and an example.
Popup also shows the validation message in the help text below.

**Field input range**: **0% to +900%, INCREASE-only.** Amazon's
own popup text: *"Choose a percentage between 0 and 900%"*. Negative
values rejected by the input. To suppress a bad placement (lower
spend), this lever is **not the right tool** — use bidding strategy
change, per-keyword bid trim, or negative-ASIN targeting instead.

Some rows show `Recommended: X%` below the current value — Amazon
suggesting a positive bump (e.g. `Recommended: 25%`). Apply the
recommendation by clicking it (or typing the value manually).

### 8f. Search terms report

Reach via Ad group → `Search terms` left sidebar (use `click_at_xy`).

**Full capture = Export CSV. Always.** The grid is virtualized — the
DOM only ever holds ~13–16 rows of what is often 200+ terms, and
scroll-capture misses rows (verified live: DOM showed 16, Export CSV
held 213). Procedure:
1. **Set the date range FIRST** (same 30-day window as the Targeting
   tab — this is what makes the 对账 reconciliation line come out ✓).
2. Click the **Export** button (top-right of the table) → CSV lands in
   `~/.vibe-seller/downloads/<store>/Sponsored_Products_SearchTerm_*.csv`.
3. Parse the CSV for ALL rows (every term with impressions). Sum
   `Total cost` and `Clicks` and reconcile against the Targeting-tab
   totals — match within ~15% or the windows are misaligned.
4. Note: the CSV does NOT carry match type directly; join the
   `Keywords` column against the Targeting table to attribute source
   keyword + match type.

Columns: `Actions`, `Added as`, `Customer search term`, `Keywords`
(with match type below), `Target bid`, `Clicks`, `Total cost`,
`Purchases`, `Sales`, `ROAS`.

**Per-row "Add as ⌄" dropdown**:
- `Add as keyword` — promotes the search term to an exact-match
  positive keyword in this ad group.
- `Add as negative exact` — adds as a negative-exact rule.
- `Add as negative phrase` — adds as a negative-phrase rule.
- **No `Add as negative broad`** — Amazon SP doesn't support
  negative-broad match type.

For ASIN-targeting on Product-pages — search terms starting with
`B0...` (10 chars) ARE ASIN targets, not text queries. They can be
used to find which competitor PDPs the ad showed on, then pasted
into Negative targeting → Negative products (campaign-level) to
block.

**SP-Auto search terms: ASIN is embedded after product title.**
Auto campaign search-term cells contain `<full product title>
ASIN : <B0XXXXXXXX>` — not just the ASIN. A regex anchored on
`^B0...` will miss them. Extract with:
```javascript
/ASIN\s*:\s*(B0[A-Z0-9]{8})/
```

**ag-Grid pinned-left + center merge for Search-terms tab.**
Same dual-container layout as the Targeting tab. The pinned-left
container holds the customer search-term cell; the center
container holds source keyword + match type + bid + metrics.
Use the same `row-index` merge eval as Targeting, but pair with
`=== ` as delimiter:

```bash
browser-use <<'PY'
js("""
return (() => {
  var pc = document.querySelector('.ag-pinned-left-cols-container');
  var pr = pc ? pc.querySelectorAll('[role=row]') : [];
  var bc = document.querySelector('.ag-center-cols-container');
  var br = bc ? bc.querySelectorAll('[role=row]') : [];
  var result = [];
  pr.forEach(function(row) {
    var idx = row.getAttribute('row-index');
    if (idx) {
      var term = row.innerText.trim().replace(/\\n/g, ' ');
      br.forEach(function(b) {
        if (b.getAttribute('row-index') === idx) {
          var met = b.innerText.trim().replace(/\\n/g, ' | ');
          if (met) result.push(term + ' === ' + met);
        }
      });
    }
  });
  return result.join('@@@');
})()
""")
PY
```

**Match type is visible in the Search-terms center container**
(as `Match type: Broad|Phrase|Exact` prefix in each row). This
provides the source keyword's match type for each search term.

**Harvesting search terms as keywords.** For **manual campaigns**:
click "Add as" button on the search term row → "Add as keyword"
from dropdown. The modal defaults to **all three match types**
checked (Exact, Phrase, Broad) — uncheck Phrase and Broad if only
Exact is wanted. Click "Add keywords" to confirm. After adding,
the row's "Added as" column changes to "Keyword" and the "Add as"
button disables.

For **auto campaigns**: auto ad groups cannot accept keyword
targets. Harvested search terms must be added to a **manual
campaign's** ad group instead. Use the **dedicated harvest ad
group** pattern (see below). The "Add as keyword" UI button in an
auto campaign's search-terms tab may exist but is not the correct
path for auto→manual harvest.

**Harvest ad group naming convention.** Every auto campaign that
has harvest candidates needs a dedicated ad group in a manual
campaign for the same product. The ad group name follows the
pattern:

```
<campaign-name-slug>-harvest-kw
```

For example, if the auto campaign is named "product-abc auto US",
the harvest ad group would be `product-abc-auto-us-harvest-kw`.
The `-harvest-kw` suffix distinguishes agent-managed harvest groups
from human-created ones.

**Auto→manual harvest workflow:**

1. Identify the auto campaign's product(s) from the Products tab.
2. Find an existing **manual** campaign that targets the same
   product(s). If none exists, create one (see § 3).
3. In that manual campaign's ad-groups tab, check if an ad group
   named `*-harvest-kw` already exists:
   ```javascript
   JSON.stringify(
     Array.from(document.querySelectorAll('a[href*="/ad-groups/"]'))
       .map(a => ({name: a.textContent.trim(),
                   href: a.href}))
       .filter(o => o.name.includes('harvest-kw'))
   )
   ```
4. **If not found**: create a new ad group. On the campaign's
   ad-groups page, click "Create ad group" → set the name to
   `<campaign-name-slug>-harvest-kw` → add the same
   product(s) as the auto campaign → **select keyword targeting**
   (click `manualTargetingType-KEYWORD` radio via `click_at_xy` — the
   form defaults to product targeting even in manual keyword campaigns)
   → set a default bid at the suggested-bid midpoint → save.
   See § 8d-2 for the creation form mechanics including the
   targeting-type trap, Shadow DOM bid pitfall, and form-state
   caching.
5. **If found**: reuse it — add Exact match keywords to this
   existing ad group.
6. In the harvest ad group's Targeting tab, add each harvested
   search term as an **Exact match** keyword with bid at the
   suggested-bid midpoint.
7. Add the same search term as **negative exact at the auto
   campaign level** (not the auto ad group) to prevent
   cannibalization.

The grid uses virtual scrolling — off-screen rows exist in DOM
but clicks don't reach them. Scroll via JS first:
```javascript
document.querySelector(".ag-body-viewport").scrollTop = N
```

### 8g. Bidding strategy edit

Campaign settings → **Campaign bidding strategy** section.

Radio options:
- **Fixed bids** — Amazon never adjusts.
- **Dynamic bids - down only** — Amazon lowers in real-time when
  conversion looks unlikely.
- **Dynamic bids - up and down** — Amazon raises or lowers up to
  ±100%.
- **Rule-based bidding** — pursue a target ROAS; Amazon adjusts.
  When selected, shows a sub-form like `Drive sales while seeking
  to keep ROAS at or above 1.80` with an `Edit` link to change the
  number.

Saving the strategy change requires confirming the campaign is not
currently mid-edit elsewhere; some accounts have a confirmation
modal.

### 8h. Daily budget edit

Campaign settings → **Budget** section → kat-numberinput showing
e.g. `<ccy> 15`. Click the value, clear it, type new amount, then
click the **inline Save button** that appears below the input.
The change does NOT auto-save on blur — the explicit Save click
is required. Verified live: setting `value` + dispatching
`input`/`change`/`blur` events without clicking Save did not
persist. After Save, the campaign header updates immediately.

`Add budget rule` link adjacent — opens a separate flow for
scheduled budget bumps (Ramadan, BFCM, etc.). Reserved for the
new-product-launch + budget-rules use case; not part of routine
ad-tuning.

### 8i. Field input ranges (verified)

**Critical for the ad-tuning skill** — the agent must know the valid
range of every field before recommending a change. Recommending an
out-of-range value (e.g. negative placement modifier) is a skill bug.

| Field | Where | Range | Direction | Unit | Source |
|---|---|---|---|---|---|
| Placement bid adjustment | Campaign → Bid adjustments → cell | 0 to 900 | Increase only | % | Amazon popup: "Choose a percentage between 0 and 900%" |
| Keyword bid | Ad group → Targeting → bid cell | marketplace min (~0.50 in the marketplace currency) to 1000 | Either | currency | Amazon documentation; varies by marketplace |
| Daily budget | Campaign settings → Budget | marketplace min (~5 in the marketplace currency) to 21000 | Either | currency | Per Amazon SP docs |
| Default bid (ad group) | Ad group settings | marketplace min to 1000 | Either | currency | |
| Match type for keyword | keyword editor | Broad / Phrase / Exact | enum | n/a | |
| Match type for negative | search terms `Add as ⌄` | Exact / Phrase | enum | n/a | **Negative-broad NOT supported** |
| Bidding strategy | Campaign settings | Fixed / Dynamic-down-only / Dynamic-up-and-down / Rule-based | enum | n/a | |
| Rule-based ROAS target | Bidding strategy → Edit rule | positive number, typically 0.5 to 10 | Either | ratio | |
| Coupon money-off amount | Coupons UI | depends on marketplace + product price | positive | currency | See § 5 |
| Coupon budget | Coupons UI | positive | positive | currency | See § 5 |
| Coupon duration | Coupons UI | up to 30 days | positive | days | Verified empirically; 31 days fails. § 5g for context. |

When recommending any numeric change, the ad-tuning skill must:
1. State current value with unit.
2. State proposed value with unit.
3. State direction (increase / decrease / add / remove / change-mode).
4. Confirm the proposed value is in range from this table.

Rejecting an out-of-range value at submit time wastes the user's
plan-stop confirm; better to never propose it.

## 8b. Virtualized-grid extraction (SB-Video keywords, ag-Grid virtualScroll)

Some Amazon Ads grids — notably **Sponsored Brands Video Targeting**
— render rows on demand via React-Virtualized / ag-Grid
`enableVirtualization`. A naive `js()` against `[role=row]` returns
only the currently-visible rows (~6–10), and the agent that gives
up here writes audit rows like
*"13 keywords (React Virtualized grid — per-row extraction blocked)"*.
That output fails reviewer Rule 16.

The correct extraction pattern is **scroll-and-accumulate**: scroll
the grid container in chunks, `js()` after each scroll, dedupe rows
by their `row-index` attribute. Sketch:

```bash
browser-use <<'PY'
js("""
return (async () => {
  const grid = document.querySelector('.ag-center-cols-container, [role=grid] [role=rowgroup]');
  if (!grid) return JSON.stringify({error: 'no grid'});
  const scroller = grid.closest('.ag-body-viewport, [role=grid] [data-virtual]') || grid.parentElement;
  const collected = new Map();   // row-index → cell text
  const scrape = () => {
    document.querySelectorAll('[role=row][row-index]').forEach(r => {
      const idx = r.getAttribute('row-index');
      if (!collected.has(idx)) collected.set(idx, r.innerText.replace(/\\n/g,' | ').trim());
    });
  };
  scrape();
  // Scroll in 200-px chunks until scroller stops growing the row set.
  let last = 0, stable = 0;
  while (stable < 3 && scroller.scrollTop < scroller.scrollHeight) {
    scroller.scrollTop += 200;
    await new Promise(r => setTimeout(r, 250));
    scrape();
    if (collected.size === last) stable++; else { stable = 0; last = collected.size; }
  }
  return JSON.stringify({rows: collected.size, data: [...collected.entries()]});
})()
""")
PY
```

Run that, parse the `data` array, treat each row as the same shape
as the regular Targeting-tab merge eval (`mechanics.md § 8`). If
the grid genuinely has < N rows, stable returns early; no harm.
**The keyword count from the campaign header (e.g. "13 keywords")
is the ground truth — if your scrape returns fewer than that, you
didn't scroll enough; loop the scroll-and-js() again.**

For ag-Grid pinned-left + center pattern (Manual-Keyword Targeting),
the merge eval in § 8 already handles virtualization implicitly
because both panes scroll together; the pattern above is only
needed when a single virtualized container holds all rows.

## 8c. Brand Analytics ASIN-keyword report capture

When the `format-anchor.md` § Rule 15 Alternatives table cites
**Source C — Amazon Brand Analytics**, the agent must have
actually fetched the **ASIN-keyword** report (NOT a category
report) and saved the result to a TSV file the reviewer can
verify. This section documents the click path.

### Why ASIN-specific, not category

Category reports return generic terms like *"computer mouse"* /
*"wireless mouse"* / *"usb mouse"* — high-volume but uncoupled from any
specific listing. The Pause-and-redirect decision is per-ASIN:
*"this listing's auto-discovered traffic dried up; what queries
would buyers use to find THIS listing?"* The ASIN-keyword report
answers exactly that.

Category-broad rows fail Rule 15 even when the capture file
exists, because the file's filtered-ASIN column won't match the
ASIN cited in the row's Evidence cell. The reviewer treats this
mismatch as a fabrication.

### Domain note — sellercentral, NOT advertising

Brand Analytics lives under **`sellercentral.amazon.<tld>`**, not
under `advertising.amazon.<tld>`. The ads-tuning audit primarily
drives `advertising.amazon.<tld>`; switching to seller-central
may require a session change. **Cookies for the two subdomains
share the same Amazon account credential**, so once
`amazon-shared § 1` login has happened in this Chromium profile,
both subdomains see the same logged-in user.

If the first navigation to `sellercentral.amazon.<tld>` after a
fresh start lands on `/ap/signin`, run the standard login flow
from `amazon-shared § 1` (Ziniao auto-fills password on
`Sign in` click; OTP comes from the store's bound email account
— the workspace handles OTP retrieval). The flow is verified to
take ~10-20 s end-to-end.

### URL & navigation path

```
Landing:   https://sellercentral.amazon.<tld>/brand-analytics/dashboard
Sub-page:  https://sellercentral.amazon.<tld>/brand-analytics/<report>
           where <report> is one of:
               search-terms   (top search terms — category report)
               search-terms-asin   (top search terms — ASIN reverse-lookup)
               item-comparison
               demographics
               repeat-purchase
```

The **ASIN reverse-lookup report** is the one Rule 15 cites. URL
slug varies by locale rollout — in many marketplaces it's typically
labelled "Top Search Terms" with a tab/toggle for "Category" vs
"ASIN". If the direct URL `/search-terms-asin` 404s for a given
locale, navigate via:

```
/brand-analytics/dashboard
  → left-nav: "Top Search Terms" or "搜索词排名"
  → toggle: Category ↔ ASIN (or "ASIN Reverse Lookup")
```

Click flow (verified URL pattern 2026-05-24; other marketplaces
parallel):

1. Open `https://sellercentral.amazon.<tld>/brand-analytics/dashboard`.
   - If redirected to `/ap/signin`: run the
     `amazon-shared § 1` login flow.
   - If redirected to `/ap/mfa`: the workspace's email-OTP poll
     should pick up the OTP and fill it. If it doesn't (no
     bound email or authenticator-app required), this is an
     **infra-blocked** outcome — fall back to Rule 15
     Outcome (B) "Searched, none found" with a Brand Analytics
     proof file containing a `# infra_block: OTP_AUTH_APP_REQUIRED`
     header line.
2. Navigate to "Top Search Terms — ASIN" (URL slug
   `/search-terms-asin` if the locale supports it).
3. Filter inputs:
   - **ASIN**: the SKU's parent or child ASIN — the same ASIN
     that the paused campaign was targeting (find it on the
     campaign's Targets tab → Products column, or in the TSV
     under `entity_id` for product targets).
   - **Time window**: last 30 days (default).
4. Hit `Apply` / `Run`. Wait for the result table to render.
5. Export the table:
   - Click `Download / Export` → choose CSV/TSV.
   - The file downloads to the per-store Ziniao downloads dir
     (see `debug-store § "The Ziniao download dir"`).
6. Move/copy the export to the canonical path (the workspace
   auto-commits this write):

```
stores/<slug>/ads/brand-analytics/<ASIN>_<YYYY-MM-DD>.tsv
```

The first column (or a top-of-file metadata line) MUST clearly
identify the filtered ASIN — typically the export includes a
header like `Filtered ASIN: B0XXXXXXXX` or a column named
`asin` whose values all match. This is what the reviewer reads
to verify the filter was set correctly.

### Schema of the captured TSV

Amazon's export shape varies slightly by locale; the minimum
required columns the reviewer looks for:

```
asin       — filtered ASIN (constant across rows)
rank       — search-frequency rank within the ASIN's traffic
query      — the customer's search term verbatim
click_share — % of clicks from this query that landed on this ASIN
conversion_share — % of conversions, same as above
```

If the export schema doesn't match (locale or report-shape
change), the agent must add a TSV-header line normalising it
before writing:

```
# brand_analytics_asin_report
# asin: B0XXXXXXXX
# captured: 2026-05-24
# source: sellercentral.amazon.<tld>/brand-analytics/search-terms-asin
asin	rank	query	click_share	conversion_share
B0XXXXXXXX	1	...	...	...
```

### Evidence cell shape (what the agent writes in the audit)

Don't paste raw file paths — use a readable reference name plus
the file path in parens. The Evidence cell format:

```
Amazon <country> — Brand Analytics ASIN report for WIDGET-A
(stores/acme-store/ads/brand-analytics/B0XXXXXXXX_2026-05-24.tsv
 row 5): query "<keyword>", rank <R>, click_share <X%>
```

Reviewer reads BOTH the readable name (sanity) and the file path
(verification). See `format-anchor.md § Readable evidence
references` for the broader naming rule.

### Capture-file freshness

A Brand Analytics capture is valid for **7 days**. If
`stores/<slug>/ads/brand-analytics/<ASIN>_*.tsv` exists from a
recent audit and is within 7 days, reuse it — no need to re-fetch.
Stale files (> 7 days) must be re-captured before citation.

### What about the empty case

If the ASIN-keyword report returns zero rows for the filtered
ASIN (low-traffic listing, brand-new listing), still capture the
file with the header rows only. That empty file is the proof for
the "Searched, none found" block in Rule 15's Outcome (B).

### What if Brand Analytics is genuinely inaccessible

The store may not have Brand Registry (Brand Analytics is a
Brand-Registry-only feature). When the BA navigation lands on
"Brand Analytics is not enabled for this account" or similar,
capture proof:

```
stores/<slug>/ads/brand-analytics/_unavailable_<YYYY-MM-DD>.txt
<one line: "Brand Analytics: not enabled — store lacks Brand Registry">
```

Reviewer accepts the `_unavailable_*.txt` marker as proof of
search for Source C in Outcome (B) "Searched, none found" blocks.
Without it, Source C rows or empty-block claims for BA are
treated as fabrications.

## 8d. Amazon React-textarea workaround (Add Keywords / Add Negative Keywords modal)

Amazon's Add Keywords and Add Negative Keywords modals use a React
**controlled `<textarea>`** component. The "Add keywords" submit
button stays disabled until React's internal state matches the DOM
value — so just calling `element.value = "..."` (whether via
`fill_input`, the native value setter, or DOM assignment) is
ignored. The button never enables.

The agent's first execution session tried five methods and reported
all five failed:

- `fill_input("<selector>", "text")` — DOM value set, React state not updated
- `type_text("text")` — keyboard simulation, React state not updated
- JS native value setter + `dispatchEvent(new Event('input'))` — React ignored
- Clipboard `execCommand('paste')` — React ignored
- CDP `Input.insertText` / `Input.dispatchKeyEvent` — React ignored / timed out

What actually works (verified against
`UCM-SP-APP:ADGROUP_NEGATIVE_KEYWORDS:kwp:kwp-enter-list-text-input-area`
on `advertising.amazon.<tld>`):

```javascript
// Canonical React controlled-component workaround.
// `execCommand('insertText', ...)` fires `beforeinput` + `input` events
// with `inputType: 'insertText'` — which React's controlled handler
// listens to and updates the component's state from. After this:
//   - textarea.value reflects the new text
//   - "Add keywords" button transitions to enabled

const ta = document.querySelector('textarea#<modal-textarea-id>');
ta.focus();
ta.select();  // wipe any pre-fill so insertText replaces, not appends
const ok = document.execCommand('insertText', false, 'wireless mouse\nusb-c cable');
// ok === true, ta.value === 'wireless mouse\nusb-c cable',
// Add keywords button disabled === false
```

`execCommand('insertText')` is NOT the same as
`execCommand('paste')` — paste reads from the clipboard and ignores
the second argument; insertText synthesizes a real text-insertion
input event with the provided string. React's controlled-component
handler listens to the `input` event with `inputType === 'insertText'`
to update state — same code path it uses for real typing.

### When to use this

- Adding negative keywords (campaign or ad group level) via the
  `Add negative Keyword` modal.
- Adding positive keywords via the `Add keywords` modal (harvest,
  mirror, manual keyword expansion).
- Any future Amazon Ads modal that uses a `<textarea>` for
  multi-line input. The pattern is generic to React controlled
  components.

### Procedure (verified end-to-end)

```bash
# 1. Open the modal (click the "Add keywords" or "Add negative Keyword" button)
browser-use <<'PY'
js("""
  var btns = document.querySelectorAll('button, [role=button], a');
  btns.forEach(function(b){
    if ((b.innerText||'').trim().indexOf('Add negative Keyword') === 0) b.click();
  });
  return 'clicked';
""")
PY
sleep 2  # let the modal render

# 2. CLICK THE 'Enter list' TAB. The modal has 'Enter list' + 'Upload file'
#    tabs at the top. The default tab varies by marketplace (some default
#    to Enter list; others default to a different tab). If you skip this
#    step on a marketplace that does NOT default to Enter list, the textarea
#    exists in the DOM but is `visible: false` — execCommand
#    runs against an off-screen element and the Save button never enables.
#    Always click 'Enter list' explicitly; it's a no-op when already selected.
browser-use <<'PY'
js("""
  var clicked = false;
  document.querySelectorAll('span, button, [role=tab]').forEach(function(el){
    if ((el.innerText||'').trim() === 'Enter list' && !clicked) {
      var rect = el.getBoundingClientRect();
      if (rect.width > 0) { el.click(); clicked = true; }
    }
  });
  return 'enter-list tab clicked: '+clicked;
""")
PY
sleep 1

# 3. Find the textarea id (verify it's visible now)
browser-use <<'PY'
js("""
  var ta = document.querySelectorAll('textarea')[0];
  if (!ta) return 'no textarea';
  var r = ta.getBoundingClientRect();
  return JSON.stringify({id: ta.id, visible: r.width>0});
""")
PY

# 4. Insert text via execCommand (use getElementById because the id has colons)
browser-use <<'PY'
js("""
  var ta = document.getElementById('<id-from-step-3>');
  ta.focus();
  ta.select();
  document.execCommand('insertText', false, 'keyword1\\nkeyword2\\nkeyword3');
  return 'inserted: '+ta.value;
""")
PY

# 5. Click the intermediate 'Add keywords' button (moves staging-list →
#    confirmed-list). The 'Save' button enables AFTER this click — not
#    after step 4. The Add-keyword step can differ by marketplace: some
#    submit on a single 'Add keywords' click; others have Add (intermediate)
#    → Save (final) as a two-step. Read the live DOM to tell which applies.
browser-use <<'PY'
js("""
  document.querySelectorAll('button').forEach(function(b){
    if ((b.innerText||'').trim() === 'Add keywords' && !b.disabled) {
      var r = b.getBoundingClientRect();
      if (r.width > 0) b.click();
    }
  });
  return 'add clicked';
""")
PY
sleep 2

# 6. Verify Save (or equivalent final-submit) is now enabled, then click it
browser-use <<'PY'
js("""
  var btn = null;
  document.querySelectorAll('button').forEach(function(b){
    if ((b.innerText||'').trim() === 'Save') {
      var r = b.getBoundingClientRect();
      if (r.width > 0) btn = b;
    }
  });
  return btn ? JSON.stringify({disabled: btn.disabled}) : 'no save btn — modal may use Add as final';
""")
PY

# 7. If Save is enabled, click it. After save, return to the listing and
#    confirm the new keyword(s) appear in the targeting / negative-targeting
#    table. The Verification cell in EXECUTION_LOG.md quotes that read-back.
```

### Match-type radio buttons (negate Exact / Phrase)

After insertText fills the textarea, the modal usually shows a row
of match-type radio buttons (Exact, Phrase, Broad). For negatives,
"Exact" is the default. Confirm by reading the radio's
`aria-checked` attribute before clicking submit:

```javascript
var radios = document.querySelectorAll('input[type=radio][name*=matchType], [role=radio]');
// Find the one labeled "Negative exact" and verify it's checked.
```

### Caveat — the textarea id contains a colon

Amazon's id (`UCM-SP-APP:ADGROUP_NEGATIVE_KEYWORDS:kwp:kwp-...`)
has colons, which are CSS selector syntax. Either escape them
(`\\\\:`) when using `querySelector`, or use `getElementById`:

```javascript
document.getElementById('UCM-SP-APP:ADGROUP_NEGATIVE_KEYWORDS:kwp:kwp-enter-list-text-input-area');
```

`getElementById` accepts colons literally — usually the cleaner
choice for these compound ids.

## 8a. Execution-completion contract (a task is NOT "done" until this holds)

A partial run once passed as complete (~25 of 69 report bids
applied, the rest never attempted) because nothing compared LIVE state to
the full report. Three rules close that gap — follow all three:

1. **Write `EXECUTION_LOG.md` to the TASK working directory** (`./EXECUTION_LOG.md`,
   i.e. the task dir — NOT only the `store-data/.../EXECUTION_LOG_<date>.md`
   tree). The `ad_execution_fidelity` stop-gate reads `<task_dir>/EXECUTION_LOG.md`.
   One row per report (campaign, keyword/ASIN, match) you touched, with the
   target, the live read-back, and the action.

2. **Address EVERY report row in scope — apply OR explicitly skip with a
   reason.** For each row the report names for your campaigns: either apply
   it (and read the value back), or write a row marking it
   `skipped — <reason>` (e.g. `live 2.87 ≥ target 1.77 (only-raise)`,
   `keyword drifted / absent`, `ad-group paused`). A report row that appears
   NOWHERE in the log = a forgotten/stopped-early row. The gate now DENIES a
   submit whose log leaves in-scope report rows unaddressed, and lists them
   ("INCOMPLETE: …"). Do not stop mid-batch; finish every row or log why not.

3. **A self-reported log is NOT proof.** The gate checks the log against the
   report, but the log is what the agent *says* it did — it cannot catch
   "logged ✅ but the bid never actually changed." Before a platform/store is
   called complete, a **READ-ONLY live re-verification** (debug-store: open
   each campaign's live targeting table and read the actual bid/state) must
   confirm every row is at target (or correctly skipped). Live is the only
   ground truth; sampling a few campaigns is not enough for "all applied."

DIRECTION RULE (this is the one that bit us — read carefully). Every bid
recommendation carries a direction; obey it, do NOT apply "only-raise" to a
lowering row:
- **`提高至 / 上调至 X` (RAISE)**: set to X **only if live < X**; if live ≥ X,
  skip and log `skipped — live ≥ target (only-raise)`. Never lower a raise row.
- **`降至 / 下调至 / 调低至 X` (LOWER)**: set to X **if live > X** (these are
  high-ACOS bids the report wants CUT — applying the cut saves spend); if live
  ≤ X already, skip and log `skipped — already ≤ target`. **"only-raise" does
  NOT apply to a 降至 row** — skipping a lowering row because "live ≥ target" is
  exactly backwards and leaves the bid overspending. (An observed production
  run executed "only-raise" and silently skipped all 18 降至 recommendations,
  leaving high-ACOS terms overspending — a real gap caught only by live audit.)
Mixed campaigns have BOTH raise and lower rows; process each by its own head.

4. **NEVER un-pause / re-enable a live ad to satisfy a gate or "complete" a
   row.** If a report bid row's keyword is currently **Paused** (or drifted /
   on a paused ad-group / inapplicable), the bid change is **moot** — log it
   `skipped — already-paused (bid moot)` and move on. Re-enabling a paused ad
   is a state change the report did **not** request; turning spend back on for
   a row someone paused can burn real money. If a stop-gate denies your submit
   (INCOMPLETE / OVER-PAUSE / OFF-REPORT), the correct response is to **fix the
   EXECUTION_LOG** (add the skip row + reason) or **flag for owner** — never to
   change an ad's on/off state to make the deny go away. The only state change
   you may safely undo is one **you yourself applied this run in error**. (A
   production run once un-paused a live keyword trying to satisfy a buggy gate;
   the gate was fixed to never instruct a re-enable, but the agent rule stands
   regardless of the gate.)

## 9. Things that are NOT in this reference (intentionally)

- The high-level "what to do for a new product launch" → that's the
  separate `new-product-launch` skill. Don't duplicate the workflow
  here.
- The high-level "what to tune in already-running campaigns" → see
  `tuning-workflow.md` (sibling reference in this same skill). This
  file is mechanics; the workflow + thinking is over there.
- Listing creation/edit, FBA shipments, account onboarding → out of
  scope.
- Sponsored Display specifics — not yet verified end-to-end here.
- Sponsored Brands bid-edit, bid-lower, and pause are now fully
  verified end-to-end; see §10 below.

## 10. Sponsored Brands (SB / SBV) keyword bid-edit and pause recipe

**VERIFIED 2026-06-15 — 5 keyword changes applied
across 3 SB/SBV campaigns (HTTP 207 confirmed each time).**

### 10a. Navigation — click-through only, never deep-link

Deep-linking to `.../cm/sb/campaigns/<id>/keywords?entityId=…`
leaves the keyword grid empty (no auth token for the SB grid XHR).
Navigate by clicking through the SPA:

```bash
# 1. Open the all-campaigns list
browser-use <<'PY'
new_tab("https://advertising.amazon.<tld>/campaign-manager/all-campaigns?entityId=ENTITY…")
wait_for_load()
PY

# 2. In the "Find a campaign" search box — use js() to set value + dispatch
#    the 'input' event (the shadow DOM input at
#    id=globalBetaAllCampaigns:quickFilter:input accepts a native-setter
#    approach; do NOT try type_text on it directly):
browser-use <<'PY'
js("""
(function() {
  const inp = document.getElementById('globalBetaAllCampaigns:quickFilter:input');
  const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  nativeSetter.call(inp, 'MY CAMPAIGN NAME');
  inp.dispatchEvent(new Event('input', {bubbles:true}));
})()
""")
PY

# 3. After ~2s wait, find the campaign anchor by text and click it:
browser-use <<'PY'
js("""
[...document.querySelectorAll('a')]
  .find(a => a.innerText.trim() === 'MY CAMPAIGN NAME' && a.getBoundingClientRect().y > 0)
  ?.click()
""")
PY

# 4. This lands on the ad-groups list. Find the targeting link (the
#    number-link in the row that ends with '/targeting?entityId=…') and
#    click it:
browser-use <<'PY'
js("""
[...document.querySelectorAll('a')]
  .find(a => a.href?.includes('/targeting') && a.getBoundingClientRect().y > 0)
  ?.click()
""")
PY

# 5. Wait ~2-3s. Confirm: document.title should include 'Ad Group: …'
#    and URL should include '/targeting?entityId=…'.
```

Alternatively (step 3): if you are already inside a campaign and want to
go back, click the **Campaigns** breadcrumb `<a aria-label="Campaigns.">` —
`page_info()` shows it near the top of the snapshot; read its rect via
`js("return …getBoundingClientRect()")` and `click_at_xy(x, y)`.

**SPA navigation via `window.history.pushState` does NOT work** — the
React router does not pick up the popstate for URL changes initiated
from `js()`; the page stays on the current route.

### 10b. Reading all keyword rows

The SB targeting tab uses a **ReactVirtualized dual-pane grid**, not ag-Grid:
`grids[2]` = left pane (keyword text), `grids[3]` = right pane (metrics + bid).

```javascript
// Pass to js("""…""") (double any \n → \\n) to read all visible rows
(function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const kwLines = (grids[2]?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const metLines = (grids[3]?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const keywords = kwLines.filter(l => l !== 'select');
  const MATCH = new Set(['Phrase', 'Exact', 'Broad']);
  const rows = [];
  let i = 0, rowNum = 0;
  while (i < metLines.length && rowNum < 200) {
    if (!MATCH.has(metLines[i])) { i++; continue; }
    const mt = metLines[i++], st = metLines[i++];
    if (metLines[i] === 'Details') i++;
    if (metLines[i] === 'No current data') i++;
    if (metLines[i] === 'adjust value') i++;
    const bid = metLines[i++];
    if (['SAR','AED','USD'].includes(metLines[i])) i++;
    rows.push({kw: keywords[rowNum]||null, rowNum, mt, bid});
    i += 7; rowNum++;
  }
  return JSON.stringify({kwCount: keywords.length, rows});
})()
```

The **live bid** for each row is the `<ccy> x.xx` token in `bid`. The
`currency_renderer_input` shadow input `value=` attribute may show a
stale or default value — use the parsed `bid` field as ground truth.

### 10c. Bid-edit recipe (SB — `currency_renderer_input` popover pattern)

Unlike SP which uses `kat-number-input` (inline commit via Enter), SB/SBV
uses `currency_renderer_input` with a **popover + Save button**:

1. **Find the shadow input** for the target row by its stable id
   (visible in `page_info()`):
   ```
   |SHADOW(open)|<input type=text id=CAMPAIGN_KEYWORDS:table_cell_<ROW>-3_keywordBid:currency_renderer_input .../>
   ```
   Read its rect centre — 0.13 has no element indices:
   ```bash
   browser-use <<'PY'
   js("return document.getElementById('CAMPAIGN_KEYWORDS:table_cell_<ROW>-3_keywordBid:currency_renderer_input').getBoundingClientRect()")
   PY
   ```

2. **Click the shadow input** to open the popover — trusted click:
   ```bash
   browser-use <<'PY'
   click_at_xy(x, y)
   PY
   ```
   A popover (`AACChromePortal`) appears with:
   - An editable `<input type=number>` (the real edit target)
   - `Save` and `Cancel` buttons

3. **Set the value** via js() + native setter (the number input inside the
   portal has no stable id; use querySelector):
   ```bash
   browser-use <<'PY'
   js("""
   (function() {
     const inp = document.querySelector('input[type=number]');
     const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
     nativeSetter.call(inp, '1.33');
     inp.dispatchEvent(new Event('input', {bubbles:true}));
     inp.dispatchEvent(new Event('change', {bubbles:true}));
     return 'set to ' + inp.value;
   })()
   """)
   PY
   ```

4. **Find and click the Save button** — locate it in `page_info()` (it
   appears in AACChromePortal near the top of the snapshot), read its rect
   centre via `js("return …getBoundingClientRect()")`, then:
   ```bash
   browser-use <<'PY'
   click_at_xy(x, y)
   PY
   ```

5. **Confirm via PATCH monitoring** (install once per page load before
   step 1, by passing this to `js("""…""")`):
   ```javascript
   // Run once per page load to install monitoring
   window._patches = [];
   var origOpen = XMLHttpRequest.prototype.open;
   var origSend = XMLHttpRequest.prototype.send;
   XMLHttpRequest.prototype.open = function(m,u){this._url=u;this._method=m;return origOpen.apply(this,arguments);};
   XMLHttpRequest.prototype.send = function(b){
     if(this._method==='PATCH'){
       var self=this;
       this.addEventListener('load',function(){
         window._patches.push({url:self._url,s:self.status,resp:self.responseText.substring(0,800)});
       });
     }
     return origSend.apply(this,arguments);
   };
   ```
   After clicking Save, check with
   `js("return JSON.stringify(window._patches)")`.
   Success = HTTP 207 + `"successfulCount":1` in the response body.

   **The display cell does NOT update** after the PATCH — the ReactVirtualized
   cell keeps showing the old `<ccy> x.xx` span. This is normal; the server
   has the new value. Confirm only via the PATCH response, not the display.

6. **Re-install monitoring** whenever a `new_tab(...)` or a hard navigation
   occurs — the monitoring is lost on page reload. Prefer in-app
   `js("location.href=…")` / click navigation over `new_tab(url)` to
   preserve monitoring context.

### 10d. Pause / Enable a keyword

The state toggle per row is a `<button role=switch>` with id
`CAMPAIGN_KEYWORDS:table_cell_<ROW>-1_state:state-switch-renderer`.

Find it in `page_info()` output:
```
<button id=CAMPAIGN_KEYWORDS:table_cell_<ROW>-1_state:state-switch-renderer
    role=switch aria-checked=true title=Enabled checked=true />
```

Read its rect centre via `js("return document.getElementById('…-1_state:state-switch-renderer').getBoundingClientRect()")`, then click it to toggle (trusted click):
```bash
browser-use <<'PY'
click_at_xy(x, y)
PY
```

Confirm: re-read via `page_info()` and verify `aria-checked=false title=Paused`, AND
check `window._patches` for a PATCH with `"state":"PAUSED"` in the request
body and HTTP 207 + `successfulCount:1` in the response.

### 10e. Campaign alphanumeric IDs

Amazon SB URLs use an alphanumeric campaign ID (e.g. `A0EXAMPLE0000000000`)
that differs from the numeric ID shown in bulk reports (e.g. `100000000000000`).
To map from numeric → alphanumeric: navigate to the campaign list, search
by campaign name, and read the `href` of the campaign anchor — it contains
the alphanumeric ID. You cannot construct the URL from the numeric ID alone.

### 10f. Grid virtualisation

The ReactVirtualized grid renders only ~13 rows at a time. With 23+ keywords
the Broad rows may be below the fold. All rows ARE included in `grids[2]/grids[3]
.innerText` without scrolling — the js() above reads all rendered text nodes
including those below the visible viewport, so a single js() call captures all
rows. Scrolling is not required for the read step.

For the **edit step**, the shadow input for a below-the-fold row appears in
`page_info()` even if the row is not visible on screen — reading its rect and
`click_at_xy` works regardless of scroll position.
