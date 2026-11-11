# noon — file-based create (NIS import)

Read from `noon-listing` § 1. The NIS (noon Item Sheet) importer creates
SKUs in bulk from a spreadsheet. Everything the click wizard sets through
an Ant-Design dropdown is a plain cell here, so this path avoids the
selects that need trusted mouse clicks.

## Contents

1. Which import for which field
2. Downloading the NIS template
3. Filling the sheet (parent/child, sizes, blanks, bilingual pairs)
4. Pre-flight check
5. Uploading and reading the result
6. Pricing import — the high-base + long-sale pattern

## 1. Which import for which field

**Flow:** `Imports` → **Add Import** → **Type** → **Subtype** → pick the
category → **Download** the template → fill it → **Next** → upload → SKUs
go to async Quality Check (QC).

URL: `https://noon-catalog.noon.partners/en/imports/create?project=PRJ{project_id}`

The Type / Subtype selects are Ant-Design dropdowns: open each with a
trusted `click_at_xy` on the field, then click the item in
`.ant-select-dropdown .ant-select-item`. Type-to-filter does not work on
them (see the `noon-shared` skill).

| Type | Subtype | Creates / sets |
|------|---------|----------------|
| Content | **NIS Create/Update** | Creates new SKUs: identity, sizes, attributes, images. This is the create step. |
| Content | Product Import | Updates content (title/description/attributes) of existing SKUs only. |
| Pricing | **Price Update** | Base price + sale price/window for existing SKUs (§ 6). |
| Pricing | Price Range Update | Long-dated sale windows (§ 6). |
| Stock | (stock subtype) | On-hand quantity for existing SKUs. Match the store's warehouse type: an FBP Stock Update fails ("no FBP warehouse") when the store has none, and FBN stock goes through an ASN (the `noon-fbn` skill), not a stock import. Check the SKU's Offer → Stock section first. |
| Warranty | (warranty subtype) | Warranty type for existing SKUs (file equivalent of choosing "No Warranty"). |

A full file-based create is **NIS Create/Update** (identity + images),
then **Price Update** and **Stock** as follow-up imports keyed on the
child `seller_sku` / `partner_sku`. Each import's "ABOUT THIS UPLOAD"
panel lists its exact required and optional columns; read it in-page
before filling.

## 2. Downloading the NIS template

- "Download templates for" → **Specific category** → drill the tree (top
  category → product type) and select it, plus the target
  store/marketplace. That enables the per-category **Download English** /
  **Download English + Arabic** buttons. AE stores need the + Arabic
  template for the local title. "All categories" gives a generic shell
  without the category's attribute columns, so use it only to see the
  structure.
- Leave **With Instructions** on the first time; it adds a
  column-guidance row.
- Core required columns (from the template's `valid values` sheet):
  `family`, `product_type`, `product_subtype`, `seller_sku`,
  `item_condition` (`New`), `parent_child_variation` (`Parent`/`Child`),
  and per-marketplace `vat_rate_ae` / `vat_rate_sa` / `vat_rate_eg`
  (`Std`). Category attribute columns and image-URL columns follow; fill
  them per the in-sheet guidance and noon's "How to fill out the NIS
  sheet" article.

## 3. Filling the sheet

- **Every product is a parent row plus at least one child row.** A lone
  `parent` row imports "successfully" but creates nothing; the error
  file's `creation_error` reads `child is missing`. Give both rows the
  same `parent_group_key`. The child is the sellable variant with its own
  distinct `seller_sku` and a `size_variation` / `size_map` (e.g.
  `One Size`), and it carries the offer, so Pricing/Stock imports key on
  the child `seller_sku`.
- **Several sizes = one parent row + one child row per size**, all on the
  parent's `parent_group_key`. noon creates one parent noon-SKU with
  children `-1`, `-2`, `-3` …. `size_map` is the noon size name and can
  differ from your label (e.g. `XXL` → noon `2XL`); check the template's
  `valid values` or the live Sizes tab. This is noon's current variation
  style; use it for every new listing.
- **NIS has no Arabic-size column.** After import, the Sizes tab's
  mandatory **Seller Size (AR)** holds a copy of the English
  `size_variation`, so it looks filled while showing English text. Edit
  each size on the live Sizes tab and enter the Arabic seller size.
- **Leave a field blank unless the task gave you its value.** A NIS sheet
  has ~150 mostly optional columns, and a value filled "because the
  column is there" is data the seller never agreed to — a guessed
  `hs_code`, an attribute nobody chose, one answer of "100 AED" fanned out
  to `msrp_ae`, `msrp_sa` and `msrp_eg`. On noon such a value is
  effectively permanent: a blank cell in a later NIS update means "no
  change", not "clear", and clearing on the Content tab does not persist
  either. If a field seems needed and the task did not give it, ask the
  user (AskUserQuestion) rather than inferring it from the English copy,
  other marketplaces, or an Amazon listing.
- **Delete the template's sample row(s).** The template ships a row with
  only `family` / `product_type` / `product_subtype` and no `seller_sku`.
  If it stays, noon's error file reports only that row's
  `seller_sku is missing` and drops every real row's `content_error`,
  while the counters read `1 error / N processed`.
- **Fill both halves of every bilingual pair** in the English + Arabic
  template (`product_title_*`, `long_description_*`,
  `feature_bullet_N_*`, `colour_name_*`). An `_en`-only sheet imports
  with no error or warning and ships a half-translated listing.
- Each row needs a unique `seller_sku`. Failures are per row: good rows
  still create, so fix the error rows and re-upload just those.

## 4. Pre-flight check

Run the validator on the filled sheet before every upload. A live import
takes minutes and its error reporting is unreliable; this takes seconds.

```bash
# The CWD is the task workspace; address the script through the synced
# skill path. --markets is the marketplaces the TASK covers, not the ones
# the store has: "only list on the UAE site" means --markets AE.
S=.claude/skills/noon-listing/scripts
python3 $S/validate_nis.py FILLED.xlsx --markets AE
```

It fails the file on a leftover sample row, a select value missing from
the template's `valid values` sheet, an out-of-range `hs_code`, an English
field whose Arabic half is empty, and a per-marketplace field (`msrp_*`,
`vat_rate_*`) set outside `--markets`. It warns when an Arabic half is
suspiciously short.

## 5. Uploading and reading the result

**Upload:** Imports → Add Import → Type = Content, Subtype = NIS
Create/Update → **Next** → attach the `.xlsx` (the hidden
`input[type=file]` accepts `.csv,.xlsx`; stage the file in
`~/.vibe-seller/downloads/<slug>/` so the browser can read it — see the
`browser-harness` skill's upload section) → **Submit**. The import runs
asynchronously under an `IMP…` code.

**What counts as created.** `Status: Complete` and `Total Processed
Rows: N` are not evidence: the counter includes rows that later failed
content validation, and an import that created nothing shows both. The
import's **Report File** is the evidence — one row per SKU with a filled
`catalog_sku` (and `sku_parent` for a variation family). A failed
import's Report File is 4 bytes (a BOM and a newline). Download it every
time, and confirm the SKUs appear in My Catalog.

**Debugging a failed import.** Open the import's error CSV; its trailing
`partner_error` / `content_error` / `creation_error` columns name the
problem. Fix those cells using the exact valid-value strings and
re-upload. If the error file names only a row you did not write, a sample
row is still in the sheet (§ 3).

## 6. Pricing import — the high-base + long-sale pattern

Some sellers list a high base price with a long-dated sale so the page
shows a large discount while the sale price is the everyday price. Apply
this only when the seller asks for it.

After the SKU exists, run **Pricing → Price Update** (required
`country_code`, `id_partner`, `partner_sku`; optional `price`,
`sale_price`, `sale_start`, `sale_end`, `is_active`):

- `price` = the high base (e.g. `100`)
- `sale_price` = the real everyday price (e.g. `50`)
- `sale_start` = today, `sale_end` = a far-future date (e.g. +5 years)

For a rolling window use **Pricing → Price Range Update**. Without the
seller asking for the pattern, set `price` to the real price and leave
the sale columns blank.
