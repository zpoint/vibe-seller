# Bulk export / import — the default create + bid-tune path

> **Load this when** the task is to **create a Sponsored Products
> campaign** or **apply bid changes across keywords/campaigns**. Bulk
> export → edit → import is the **default** for both. The click-through
> mechanics in `mechanics.md` §3 (create) and §4a0 (ag-Grid edits) are
> the **fallback** — reach for them only when the user asks, when a
> single one-off edit isn't worth a round trip, or after a bulk import
> fails twice for the same file.

## Why bulk is the default

The click path works (mechanics §4a0 has verified trusted-event
recipes) but it is slow and fragile: shadow-DOM `kat-*` components,
hover-to-reveal menus, per-field modals, one entity at a time. Amazon's
own **Bulk Operations** is the supported batch interface — one XLSX
round trip creates a whole campaign or re-bids every keyword at once.
The historical reason bulk was *avoided* was the **ASIN-as-SKU trap**
(§ below), which silently dropped ad groups and made uploads look
randomly unreliable. `scripts/ads_bulk.py` closes that trap in code, so
bulk is now the first choice, not the last resort.

## The round trip

```
Download campaigns (export)  →  ads_bulk.py edits the sheet  →  Upload campaigns (import)  →  verify
```

### 1. Export (Download campaigns)

Bulk Operations → **Download campaigns**. See `mechanics.md` §2d for the
modal checkboxes and the 5–15 min job time. When the row's status flips
to Success, click its **Download** link. The file lands in:

```
~/.vibe-seller/downloads/<store-slug>/          # vibe-seller-monitored (reliable)
```

(On some setups Ziniao also drops a copy under its own profile download
dir; the vibe-seller path is the one to read.)

### 2. Edit with `ads_bulk.py`

```bash
PY=~/.vibe-seller/.venv/bin/python3          # any venv with openpyxl
S=<skills>/amazon-ads/scripts/ads_bulk.py
EXPORT=~/.vibe-seller/downloads/<slug>/<the-export>.xlsx

# Summarise: entity counts, per-campaign spend/sales/ACOS, and the
# store's real SKU naming scheme (Product Ad rows). Feed for a report.
$PY "$S" inspect "$EXPORT"

# CREATE a new manual-keyword campaign by cloning an existing one's
# keywords onto a new product. Emitted paused. NOTE: --daily-budget must
# be >= the marketplace minimum or the Campaign row is rejected and the
# whole upload fails (verified: 1 SAR rejected on KSA; use the store's
# existing floor, e.g. 10). "min budget" means the store's min, not 1.
$PY "$S" clone-campaign "$EXPORT" \
    --src "acme widgets 004 manual keyword US" \
    --new "acme widgets 006 manual keyword US" \
    --sku WIDGET-006-Blue-M --asin B0EXAMPLE1 \
    --daily-budget 10 --default-bid 0.75 \
    --out /tmp/<slug>/bulk_create.xlsx

# BID UPDATE: re-bid every keyword in a campaign (scale or set).
$PY "$S" bid-update "$EXPORT" \
    --campaign "acme widgets 004 manual keyword US" --scale 0.85 \
    --out /tmp/<slug>/bulk_bid_update.xlsx

# ARCHIVE (disable + clean up) a campaign — needs only its Campaign Id,
# read from the export.
$PY "$S" archive-campaign "$EXPORT" \
    --campaign "acme widgets 006 manual keyword US" \
    --out /tmp/<slug>/bulk_archive.xlsx

# CLONE an AUTO campaign — same command; it detects auto from the source
# (which has product-targeting rows, not keywords) and copies those match
# groups (close-match/loose-match/substitutes/complements) verbatim. Do
# NOT hand-write auto-target tokens; clone from a working auto campaign.
$PY "$S" clone-campaign "$EXPORT" \
    --src "acme widgets 004 auto US" --new "acme widgets 006 auto US" \
    --sku WIDGET-006-Blue-M --daily-budget 10 --out /tmp/<slug>/bulk_auto.xlsx

# NEGATE zero-sales keywords on a campaign (bulk — NOT by scraping the
# on-screen grid). Campaign-level by default; --level adgroup for ad-group.
$PY "$S" negate "$EXPORT" \
    --campaign "acme widgets 006 manual keyword US" \
    --keywords "generic term a,generic term b" --match negativePhrase \
    --out /tmp/<slug>/bulk_negate.xlsx
```

> **Negation is a bulk op, never a grid scrape.** The on-screen keyword /
> search-term table is a **virtualized ag-Grid** (~13 of hundreds of rows
> render; `innerText` is often undefined mid-virtualization) — reading or
> clicking it to negate **does not work and wastes the run**. Decide *what*
> to negate from the **export / Search-Terms CSV**, then apply via `negate`
> (Sponsored Products). Sponsored **Brands** / SB-video negatives aren't in
> the SP sheet — use the SB bulk sheet or the console targeting UI's
> add-negative control; the principle (bulk/console, never scrape) is the
> same.

> **Clone re-points the product; verify name ↔ product.** `clone-campaign`
> sets the new Product Ad to the `--sku` you pass — always the NEW
> product's own seller SKU (resolve it from inventory by the new ASIN
> first). A campaign named `…006…` that still advertises the 005/004 SKU is
> the classic clone-rename bug; after upload, verify the created campaign's
> Product Ad SKU/ASIN, not just its name.

> **Create emits `paused`; enable when the task says to go live.** New
> campaigns are emitted paused so nothing spends on upload. If the task is
> to launch them, follow with a `bid-update`-style Update row setting
> `State=enabled` (or enable in the console) — do not leave them paused and
> call the task done when it asked you to run them.

The output XLSX contains **only** the rows to act on (Create / Update /
Archive), with `Operation` set accordingly. It reuses the exact header
row and sheet name from the export you fed it (see locale note below).

> **Editing a paused campaign (bid-update / archive) needs its real
> IDs**, which only appear in an export taken with **"zero-impression
> items" checked** (and **"terminated campaigns" checked** to see an
> archived one). A freshly-created paused campaign is otherwise dropped
> from the export (§ download / mechanics §2d), so you can't get its
> Campaign Id / Keyword Ids. Full verified lifecycle: create (budget ≥
> floor) → zero-impression export → bid-update → archive → zero-impression
> + terminated export to confirm.

### 3. Import (Upload campaigns)

Bulk Operations → **Upload campaigns** → pick the output file. The job
runs asynchronously; the history table shows `Success` / `Failed` /
`File not uploaded` (these status strings are **English regardless of
console language**). When a row fails, **download its result report**
from the row's Download link — it lists the row-level validation error.
Capturing that report is how you learn *why* an import failed rather
than guessing.

### 4. Verify

Re-export (or open the campaign in the UI) and confirm the new campaign
exists (paused) / the bids changed. Don't trust the `Success` status
alone — the ASIN-as-SKU trap commits the Campaign shell and reports
`Success`-then-`Failed` with the ad group silently gone.

## Locale-generality (this is load-bearing)

UI language varies per user (账号 may be zh_CN, EN, or other). The
script is built to not care:

- **The SP sheet has a fixed 52-column order in every language** — only
  the sheet *name* and *header text* are localised. `ads_bulk.py`
  addresses every column by **position**, never by header text, and
  only *warns* (never fails) if a header label is unfamiliar. Add new
  localisations to its `SHEET_NAMES` / `ENTITY` / `HEADER_CHECK` tables
  as you observe them.
- **The output sheet clones the export's header row + sheet name
  verbatim.** That header is the exact one this account's Amazon
  emitted, so it's the only one we know its upload validator accepts —
  no guessing at localised header strings.
- **Bulk Operations table headers + Status enum + row "Download" links
  render in English regardless of locale** — match those by their
  English text.

### The Bulk Operations UI: drive it by language-INDEPENDENT selectors

**Do not match localized button/label text** to drive this page. The
displayed language can be anything and the user can change it at any time
(account menu → *Your preferences* → *Language* dropdown → the *Change
Language* apply button — note that apply button is **disabled until you
pick a different language**, which is easy to miss). So a run may open in
`zh_CN`, `en_US`, `العربية`, etc. Every step below therefore uses a
stable id / element type / structure / the always-English status strings
— never a localized label. **Verified in both zh_CN and en_US**: the same
structural anchors below (button order, checkbox order,
`input[type=file]`, English `Status` enum) hold identically in each.

| Step | Language-independent anchor |
|---|---|
| The bulk page component | custom element `<bulk-storm-dashboard>` |
| Upload vs Download buttons | the dashboard's two header buttons, in **fixed order: 1st = Upload, 2nd = Download**. Confirm by the modal each opens (Upload → a file input; Download → a date-range picker + checkboxes). |
| Upload file picker | **`input[type=file][accept=.xlsx]`** inside the upload modal — fully language-independent; `upload <idx> <file>` sets it. |
| Modal submit / cancel | the modal's **two footer buttons, order: 1st = Cancel, 2nd = submit** (Upload/Download). Click the 2nd. |
| Download-modal checkboxes | **fixed order**: Terminated, Paused, Zero-impression, Placement, SP, SB, SB-multi-adgroup, SD, SP-guide, SP-search-terms, SB-search-terms, Budget-rules. Select by position, not label. (Check *Zero-impression* to include a paused new campaign; *Terminated* to include an archived one.) |
| History table | **column headers + `Status` enum render in English regardless of locale** — match `Success` / `Failed` / `File not uploaded` / `Downloading` by that English text. |
| A row's result-report link | the link element in the **Download column** (English header) of that row — match by column, not the link's (localized) text. |
| A row's result summary | the info-tooltip text is `"K of N uploaded"` — **numeric**, language-independent. `0 of N` = structural failure; `K of N` (K>0) = per-row. |
| Console language switch | `button[name=aac-select-language]` (if you ever need it) |

When a genuinely text-only match is unavoidable, use a dual-language
set — but prefer the anchors above. Confirmed labels (`en_US` | `zh_CN`),
for reference / fallback only:

- `Upload campaigns` | `上传广告活动`  ·  `Download campaigns` | `下载广告活动`
- `Cancel` | `取消`  ·  submit `Upload`/`Download` | `上传`/`下载`
- Download-modal checkboxes: `Terminated campaigns` | `已终止的广告活动`,
  `Paused campaigns` | `已暂停的广告活动`,
  `Campaign items with zero impressions` | `展示量为零的广告活动项目`
- Status enum is English in every locale: `Success` / `Failed` /
  `File not uploaded` / `Downloading`.

### Upload wants ENGLISH API tokens — the export only DISPLAYS localised ones (VERIFIED LIVE)

This is the single biggest gotcha, and it's counter-intuitive. The
export *shows* localised enum values (a zh_CN account shows
`商品推广`, `已启用`, `自动`, `广泛`, `动态竞价 - 提高和降低`), but the
**uploader requires the English API tokens**. Writing the localised
display value back gets the row rejected. Verified live on a zh_CN
account: an upload carrying `广泛` match tokens returned **"0 of 6
uploaded"**.

The authority is the **`Config` sheet inside every export** — it lists
the exact valid upload tokens, and they are English:

| Field | Config key | Valid tokens |
|---|---|---|
| Product | `SponsoredProductsProductNames` | `Sponsored Products` |
| Operation | `…OperationNames` | `Create` / `Update` / `Archive` |
| State | `…Create/Update…States` | `enabled` / `paused` / `archived` |
| Targeting Type | `…CreateCampaignTargetingTypes` | `AUTO` / `MANUAL` (uppercase) |
| Match Type | `…CreateKeywordMatchTypes` | `broad` / `phrase` / `exact` |
| Bidding Strategy | `…CreateCampaignStrategys` | `Dynamic bids - down only` / `… up and down` / `Fixed bid` |

`ads_bulk.py` normalises copied display tokens to the API token
(`match_type_api`, `state_api`) and writes English enums throughout.
Add new localisations to those maps as you meet them.

### Required fields for Create/Update (from the `Config` sheet — VERIFIED LIVE)

The `Config` sheet's `*RequiredHeaders` rows are authoritative. The
non-obvious ones that cause **all-rows-fail** ("0 of N uploaded"):

- **`Start Date`** (YYYYMMDD) is required on a Create Campaign row.
  Omitting it fails the campaign, which cascades to every child row.
- **`Campaign Id` / `Ad Group Id` are required on Create rows** — as
  **placeholder** ids you invent (any unique string; the script reuses
  the names). Amazon assigns real ids on creation and uses these only to
  link parent→child *within the sheet*. Linking by Campaign **Name**
  alone is NOT enough. This was the difference between "0 of 6" and
  "5 of 6" in live testing.
- **`State`** is required on Update rows (e.g. a bid change) — preserve
  the entity's current state as the API token.

Read the failure count precisely: **"0 of N"** = a structural/global
problem (wrong enum, missing required field on the campaign, cascade);
**"K of N"** with K>0 = per-row problems on the N−K that failed. The
downloaded result report on this account only echoes the input (no
per-row annotation), so the count in the status tooltip is your main
signal — plus a re-export to see what actually committed.

## The ASIN-as-SKU trap (guarded in code)

**Never put an ASIN in a Product Ad `SKU` cell.** Amazon silently
rejects the Product Ad and then drops *every entity rooted under that ad
group* (Ad Group, Product Ad, all Keyword/Target rows). The Campaign and
Bidding Adjustment rows still commit, leaving an empty paused shell that
can't deliver, and the status only flips to `Failed` after a long delay.

`ads_bulk.py clone-campaign` **refuses** a `--sku` that matches the ASIN
pattern (`B0` + 8 alphanumerics). Pass the real seller SKU; use `--asin`
for the (informational) ASIN column. To find the real SKU: run `inspect`
on an export and read the Product Ad rows' `SKU` column (that's the
store's canonical naming), or open
`…/skucentral?mSku=<exact-child-sku>&condition=New` (mechanics §4b).

## When to fall back to clicks

- A **single** bid/state tweak — not worth a 5–15 min export/import.
  Use the ag-Grid trusted-event recipe (mechanics §4a0).
- An operation with **no bulk column** (e.g. some SB/SD creative
  fields, coupons).
- A campaign **paused with no recent activity** may be dropped from the
  export entirely (mechanics §2d gotcha) — edit it via its UI tabs.
- Two consecutive import failures on the same file whose result report
  points at something the sheet can't express — switch to the UI create
  flow (mechanics §3) and file the failure mode here.
