# Bulk export / import — the default create + bid-tune path

Load this when the task is to create a Sponsored Products campaign or
apply changes across many keywords or campaigns. Bulk export → edit →
import is the default for both. The click paths in `mechanics-create.md`
(create) and `mechanics-edit.md` (edits) are the fallback: when the user
asks, when a single edit isn't worth a round trip, or after a bulk import
fails twice for the same file.

- [Why bulk is the default](#why-bulk-is-the-default)
- [The round trip](#the-round-trip) — [export](#1-export-download-campaigns),
  [edit](#2-edit-with-ads_bulkpy), [import](#3-import-upload-campaigns),
  [verify](#4-verify)
- [Locale-generality](#locale-generality) —
  [UI selectors](#the-bulk-operations-ui-drive-it-by-language-independent-selectors),
  [English API tokens](#upload-wants-english-api-tokens--the-export-only-displays-localised-ones),
  [required fields](#required-fields-for-createupdate-from-the-config-sheet)
- [The ASIN-as-SKU trap](#the-asin-as-sku-trap-guarded-in-code)
- [When to fall back to clicks](#when-to-fall-back-to-clicks)

## Why bulk is the default

The click path works (`mechanics-edit.md` §1–2 has trusted-event
recipes) but it is slow and fragile: shadow-DOM `kat-*` components,
hover-to-reveal menus, per-field modals, one entity at a time. Amazon's
own **Bulk Operations** is the supported batch interface — one XLSX
round trip creates a whole campaign or re-bids every keyword at once.
The ASIN-as-SKU trap (below) used to make uploads look randomly
unreliable; `scripts/ads_bulk.py` guards it in code.

## The round trip

```
Download campaigns (export)  →  ads_bulk.py edits the sheet  →  Upload campaigns (import)  →  verify
```

### 1. Export (Download campaigns)

Bulk Operations → **Download campaigns**. See `mechanics-reading.md` §2 for the
modal checkboxes and the 5–15 min job time. When the row's status flips
to Success, click its **Download** link. The file lands in:

```
~/.vibe-seller/downloads/<store-slug>/          # vibe-seller-monitored (reliable)
```

(On some setups Ziniao also drops a copy under its own profile download
dir; the vibe-seller path is the one to read.)

### 2. Edit with `ads_bulk.py`

```bash
PY=python3   # the agent venv's python; install requirements.txt if openpyxl is missing
S=.claude/skills/amazon-ads/scripts/ads_bulk.py
EXPORT=~/.vibe-seller/downloads/<slug>/<the-export>.xlsx

# Summarise: entity counts, per-campaign spend/sales/ACOS, and the
# store's real SKU naming scheme (Product Ad rows). Feed for a report.
$PY "$S" inspect "$EXPORT"

# CREATE a new manual-keyword campaign by cloning an existing one's
# keywords onto a new product. Emitted paused. NOTE: --daily-budget must
# be >= the marketplace minimum or the Campaign row is rejected and the
# whole upload fails (1 is below the floor on some markets; use the
# store's existing budget floor, e.g. 10).
$PY "$S" clone-campaign "$EXPORT" \
    --src "acme widgets 004 manual keyword US" \
    --new "acme widgets 006 manual keyword US" \
    --sku WIDGET-006-Blue-M --asin B0EXAMPLE1 \
    --daily-budget 10 --default-bid 0.75 \
    --out ~/.vibe-seller/downloads/<slug>/bulk_create.xlsx

# BID UPDATE from an approval: each approved raise/lower row of
# CHANGES.tsv at its own target_bid, nothing else (exits if an approved
# row isn't in the export).
$PY "$S" bid-update "$EXPORT" --from-changes CHANGES.tsv \
    --out ~/.vibe-seller/downloads/<slug>/bulk_bid_update.xlsx

# --scale / --set-bid move EVERY keyword in the campaign: use them only
# when the user approved exactly that ("cut all of campaign X by 15%").
$PY "$S" bid-update "$EXPORT" \
    --campaign "acme widgets 004 manual keyword US" --scale 0.85 \
    --out ~/.vibe-seller/downloads/<slug>/bulk_bid_update.xlsx

# ARCHIVE (disable + clean up) a campaign — needs only its Campaign Id,
# read from the export.
$PY "$S" archive-campaign "$EXPORT" \
    --campaign "acme widgets 006 manual keyword US" \
    --out ~/.vibe-seller/downloads/<slug>/bulk_archive.xlsx

# CLONE an AUTO campaign — same command; it detects auto from the source
# (which has product-targeting rows, not keywords) and copies those match
# groups (close-match/loose-match/substitutes/complements) verbatim.
# Clone from a working auto campaign rather than hand-writing the tokens.
$PY "$S" clone-campaign "$EXPORT" \
    --src "acme widgets 004 auto US" --new "acme widgets 006 auto US" \
    --sku WIDGET-006-Blue-M --daily-budget 10 --out ~/.vibe-seller/downloads/<slug>/bulk_auto.xlsx

# NEGATE zero-sales terms on a campaign (decided from the export, not the
# on-screen grid). Campaign-level by default; --level adgroup for ad-group.
$PY "$S" negate "$EXPORT" \
    --campaign "acme widgets 006 manual keyword US" \
    --keywords "generic term a,generic term b" --match negativePhrase \
    --out ~/.vibe-seller/downloads/<slug>/bulk_negate.xlsx
```

> **Negate from the export, not the grid.** The on-screen keyword and
> search-term tables are virtualized (about 13 of hundreds of rows
> render), so reading or clicking them to negate misses rows. Decide what
> to negate from the export or Search Terms CSV, then apply with `negate`
> (Sponsored Products). Sponsored Brands / SB video negatives aren't in
> the SP sheet — use the SB bulk sheet or the console's add-negative
> control.

> **Clone re-points the product; verify name ↔ product.** `clone-campaign`
> sets the new Product Ad to the `--sku` you pass — the new product's own
> seller SKU (resolve it from inventory by the new ASIN first). A campaign
> named `…006…` that still advertises the 004 SKU is the usual
> clone-rename slip; after upload, check the created campaign's Product
> Ad SKU/ASIN, not just its name.

> **Create emits `paused`; enable when the task says to go live.** New
> campaigns are emitted paused so nothing spends on upload. If the task is
> to launch them, follow with a `bid-update`-style Update row setting
> `State=enabled` (or enable in the console); a task that asked for live
> campaigns isn't done while they are paused.

The output XLSX contains only the rows to act on (Create / Update /
Archive), with `Operation` set accordingly. It reuses the exact header
row and sheet name from the export you fed it (see locale note below).

> **Editing a paused campaign (bid-update / archive) needs its real
> ids**, which only appear in an export taken with "zero-impression
> items" checked (and "terminated campaigns" to see an archived one). A
> freshly created paused campaign is otherwise missing from the export
> (`mechanics-reading.md` §2). Lifecycle: create (budget ≥ floor) →
> zero-impression export → bid-update → archive → zero-impression +
> terminated export to confirm.

### 3. Import (Upload campaigns)

Bulk Operations → **Upload campaigns** → pick the output file (from the
downloads dir the `--out` paths above target — the browser must read it to
attach it; see `browser-harness` § "Uploading a file"). The job
runs asynchronously; the history table shows `Success` / `Failed` /
`File not uploaded` (English in every console language). When a row
fails, download its result report from the row's Download link to learn
why, rather than guessing.

### 4. Verify

Re-export (or open the campaign in the UI) and confirm the new campaign
exists (paused) / the bids changed. Don't trust the `Success` status
alone — the ASIN-as-SKU trap commits the Campaign shell and reports
`Success`-then-`Failed` with the ad group silently gone.

## Locale-generality

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

### The Bulk Operations UI: drive it by language-independent selectors

Drive this page by structure, not localized labels. The
displayed language can be anything and the user can change it at any time
(account menu → Your preferences → Language → Change Language; the
apply button stays disabled until you pick a different language). So a
run may open in `zh_CN`, `en_US`, `العربية`, etc. The anchors below
(button order, checkbox order, `input[type=file]`, the English `Status`
enum) hold identically in zh_CN and en_US.

| Step | Language-independent anchor |
|---|---|
| The bulk page component | custom element `<bulk-storm-dashboard>` |
| Upload vs Download buttons | the dashboard's two header buttons, in fixed order: 1st = Upload, 2nd = Download. Confirm by the modal each opens (Upload → a file input; Download → a date-range picker + checkboxes). |
| Upload file picker | `input[type=file][accept=.xlsx]` inside the upload modal — fully language-independent; `upload <idx> <file>` sets it. |
| Modal submit / cancel | the modal's two footer buttons, order: 1st = Cancel, 2nd = submit (Upload/Download). Click the 2nd. |
| Download-modal checkboxes | fixed order: Terminated, Paused, Zero-impression, Placement, SP, SB, SB-multi-adgroup, SD, SP-guide, SP-search-terms, SB-search-terms, Budget-rules. Select by position, not label. (Check *Zero-impression* to include a paused new campaign; *Terminated* to include an archived one.) |
| History table | column headers and the `Status` enum render in English in every locale — match `Success` / `Failed` / `File not uploaded` / `Downloading` by that English text. |
| A row's result-report link | the link element in the **Download column** (English header) of that row — match by column, not the link's (localized) text. |
| A row's result summary | the info-tooltip text is `"K of N uploaded"` — numeric, language-independent. `0 of N` = structural failure; `K of N` (K>0) = per-row. |
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

### Upload wants English API tokens — the export only displays localised ones

The export shows localised enum values (a zh_CN account shows
`商品推广`, `已启用`, `自动`, `广泛`, `动态竞价 - 提高和降低`), but the
uploader requires the English API tokens; writing a localised value back
gets the row rejected (`广泛` match tokens → "0 of 6 uploaded").

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

### Required fields for Create/Update (from the `Config` sheet)

The `Config` sheet's `*RequiredHeaders` rows are authoritative. The
non-obvious ones that fail every row ("0 of N uploaded"):

- **`Start Date`** (YYYYMMDD) is required on a Create Campaign row.
  Omitting it fails the campaign, which cascades to every child row.
- **`Campaign Id` / `Ad Group Id` are required on Create rows** — as
  placeholder ids you invent (any unique string; the script reuses the
  names). Amazon assigns real ids on creation and uses these only to
  link parent → child within the sheet; linking by Campaign Name alone
  isn't enough.
- **`State`** is required on Update rows (e.g. a bid change) — preserve
  the entity's current state as the API token.

Read the failure count precisely: "0 of N" = a structural problem
(wrong enum, missing required field on the campaign, cascade); "K of N"
with K>0 = per-row problems on the N−K that failed. The
downloaded result report on this account only echoes the input (no
per-row annotation), so the count in the status tooltip is your main
signal — plus a re-export to see what actually committed.

## The ASIN-as-SKU trap (guarded in code)

**Put the seller SKU, not an ASIN, in a Product Ad `SKU` cell.** Amazon silently
rejects the Product Ad and then drops *every entity rooted under that ad
group* (Ad Group, Product Ad, all Keyword/Target rows). The Campaign and
Bidding Adjustment rows still commit, leaving an empty paused shell that
can't deliver, and the status only flips to `Failed` after a long delay.

`ads_bulk.py clone-campaign` refuses a `--sku` that matches the ASIN
pattern (`B0` + 8 alphanumerics). Pass the real seller SKU; use `--asin`
for the (informational) ASIN column. To find the real SKU: run `inspect`
on an export and read the Product Ad rows' `SKU` column (that's the
store's canonical naming), or open
`…/skucentral?mSku=<exact-child-sku>&condition=New` (`mechanics-create.md` §12).

## When to fall back to clicks

- A single bid/state tweak — not worth a 5–15 min export/import.
  Use the trusted-event recipe (`mechanics-edit.md` §1–2).
- An operation with no bulk column (e.g. some SB/SD creative
  fields, coupons).
- A campaign paused with no recent activity may be missing from the
  export (`mechanics-reading.md` §2) — edit it via its UI tabs.
- Two consecutive import failures on the same file whose result report
  points at something the sheet can't express — switch to the UI create
  flow (`mechanics-create.md`) and report the failure mode in your result.
