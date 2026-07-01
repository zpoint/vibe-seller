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
# keywords onto a new product. Emitted paused + minimal budget.
$PY "$S" clone-campaign "$EXPORT" \
    --src "acme widgets 004 manual keyword US" \
    --new "acme widgets 006 manual keyword US" \
    --sku WIDGET-006-Blue-M --asin B0EXAMPLE1 \
    --daily-budget 1 --default-bid 0.75 \
    --out /tmp/<slug>/bulk_create.xlsx

# BID UPDATE: re-bid every keyword in a campaign (scale or set).
$PY "$S" bid-update "$EXPORT" \
    --campaign "acme widgets 004 manual keyword US" --scale 0.85 \
    --out /tmp/<slug>/bulk_bid_update.xlsx
```

The output XLSX contains **only** the rows to act on (Create / Update),
with `Operation` set accordingly. It reuses the exact header row and
sheet name from the export you fed it (see locale note below).

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
- When you must locate an element by label in the UI, match by stable
  **id / structural selector** or dual-language label
  (`Find a campaign|查找广告活动`), never by one language's text alone.
- Option when testing: switch the ad console to English to capture
  stable English labels, then confirm the positional path still holds
  in the original language.

**Known open item:** keyword `Match Type` and campaign `State` are
copied through from the export verbatim, which means a localised token
(e.g. a non-English match-type string) is written back on Create. Amazon
accepts what it exported for the *same* account/locale, but a
cross-locale sheet may need these normalised to `broad`/`phrase`/`exact`
and `enabled`/`paused`. Verify on first import for a new locale.

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
