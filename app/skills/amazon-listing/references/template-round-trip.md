# Listing template round trip — download → inspect → fill → upload → feedback

> **Load this for any listing CRUD.** The flat-file template is
> category-specific: the column set and valid values differ per product
> type, so `inspect` a fresh template before filling it.

## 0. The template geometry (verified on a real fptcustom template)

The `Template` sheet is a wide table with a **three-row header**:

```
Excel row 1   TemplateType=fptcustom | Version | Signature | group names
Excel row 2   Local Label Names   (LOCALISED — e.g. "Seller SKU")
Excel row 3   field API names     (item_sku, update_delete, ...)  <-- key on THIS
Excel row 4+  data rows
```

`listing_bulk.py` locates the field-name row structurally (the row that
contains `item_sku`) and addresses every field by API name — **never**
by the localised label above it. Data rows are appended from row 4.

The metadata sheets are the source of truth:

- **`Data Definitions`** — one row per field with `Required?`.
- **`Valid Values`** / **`Dropdown Lists`** — the accepted enum tokens
  (e.g. `variation_theme` ∈ {Color, color-size, Size, …}).
- **`Browse data`** — the valid `recommended_browse_nodes` for the
  category (a gated set; a guessed node id is rejected).

## 1. Download the template

Seller Central → **Catalogue → Add Products via Upload**
(`sellercentral.amazon.<tld>/listing/upload` → `/product-search/bulk`).
Open **Spreadsheet → Download Blank Template → Download Product
Spreadsheet**, search the product type (e.g. "socks" → **Select**
"Sock"), choose the language + target marketplaces, then **Generate
Spreadsheet**. The file lands in `~/.vibe-seller/downloads/<slug>/`
(a macro-enabled `.xlsm`).

> **Product-Type search gotcha (Beta modal):** the search box is a
> locked `kat-input` — typing the keyword alone (`browser-use input` /
> `type`) does **not** open the candidate list. You must click the
> **search icon** at the right of the input row to trigger the
> suggestions, then click **Select** on the matched product type.

> **The create template generates asynchronously** — "Generate
> Spreadsheet" does not always land a direct download. If it doesn't
> appear, use **Download Blank Template** (classic, direct), or collect
> the generated file from **Check Upload Status** (element id
> `spreadsheet-upload-status-kat-link`) / the account email.

## 2. Inspect

```bash
$PY $S/listing_bulk.py inspect ~/.vibe-seller/downloads/<slug>/TEMPLATE.xlsm
# one field's detail (columns / required / accepted values):
$PY $S/listing_bulk.py inspect TEMPLATE.xlsm --field variation_theme
```

Read off: the total/required field counts, the operation column, the
variation cluster (`parent_child`, `relationship_type`,
`variation_theme`, `parent_sku`, `external_product_id[_type]`), and the
required fields with their enums. `brand_name`'s valid values reveal
the account's **registered brand** — a create must use it (brand-gated).

## 3. The spec (parent + children)

`fill` takes a JSON spec. Top-level `product_type` and `brand` are
defaults applied to every row. Each row has `sku`, `operation`, and a
`fields` map keyed by **field API name**; the friendly keys `parentage`,
`parent_sku`, `variation_theme`, and `asin` fold into their flat-file
fields.

```json
{
  "product_type": "socks",
  "brand": "ACME",
  "rows": [
    { "sku": "WIDGET-001", "operation": "create", "parentage": "Parent",
      "variation_theme": "Color",
      "fields": { "relationship_type": "Variation",
                  "item_name": "ACME ...", "product_description": "...",
                  "recommended_browse_nodes": "<from Valid Values>",
                  "target_gender": "Male", "department_name": "mens",
                  "batteries_required": "No", "are_batteries_included": "No" } },
    { "sku": "WIDGET-001-WHT", "operation": "create", "parentage": "Child",
      "parent_sku": "WIDGET-001", "variation_theme": "Color",
      "fields": { "relationship_type": "Variation", "color_name": "White",
                  "main_image_url": "https://.../white.jpg",
                  "fulfillment_availability#1.fulfillment_channel_code": "DEFAULT",
                  "fulfillment_availability#1.quantity": "100",
                  "purchasable_offer[marketplace_id=<SA>]#1.our_price#1.schedule#1.value_with_tax": "29.00",
                  "purchasable_offer[marketplace_id=<AE>]#1.our_price#1.schedule#1.value_with_tax": "29.00" } }
  ]
}
```

### Parent vs Child — what goes where

- **Parent** row: shared catalogue data only (product type, brand,
  title, description, `parent_child: Parent`, `relationship_type:
  Variation`, `variation_theme`). It carries **no** offer, stock,
  images, colour/size, or product-id — those are child-level.
- **Child** rows: `parent_child: Child`, `parent_sku` = the parent's
  SKU, the differentiating attribute (`color_name` / `size_name`), plus
  the **offer** (`fulfillment_availability#1.*`) and **price**
  (`purchasable_offer[marketplace_id=…]#1.our_price#1.schedule#1.value_with_tax`,
  one block per target marketplace).

`fill` knows this: it suppresses child-level required-field warnings on
a Parent row, and suppresses battery/hazmat required-field warnings when
the row declares no batteries.

> **Child rows inherit the parent's catalogue attributes.** A child
> legitimately omits `item_name`, `product_description`,
> `recommended_browse_nodes`, gender/department/size/material/weight,
> etc. — Amazon fills them from the parent. So `fill` WILL print
> "missing required field(s)" warnings for those on child rows; that is
> **expected noise, not an error** (verified live: a create with minimal
> children — only `parent_sku`, the differentiator, offer + image —
> returned 0 errors / 0 warnings from Amazon). Likewise
> `external_product_id` is not needed when the brand is **GTIN-exempt**.
> Treat child-row required warnings as informational; trust Amazon's
> processing report for the real verdict.

### The operation column

`operation` maps to the `update_delete` cell: `create`→blank,
`update`→`Update`, `partialupdate`→`partialupdate`, `delete`→`delete`.
A **delete** row needs only `sku` + `operation: delete`. **Changing a
parent-child relationship** is an update: re-submit the child with the
new `parent_sku` / `variation_theme` (or clear `parent_sku` and set
`parent_child` appropriately to detach).

```bash
$PY $S/listing_bulk.py fill TEMPLATE.xlsm --spec SPEC.json --out /tmp/<slug>/out.xlsm
```

`fill` prints a warning (never fails) for an enum value not in the
template's valid list, a missing required field, or a field absent from
this template — so an unseen-but-valid token from a new category still
uploads. Read the warnings; they are the same errors Amazon would
reject on.

## 4. Upload + read the processing report

Upload the `.xlsm` on the same page. Amazon processes asynchronously;
**Check Upload Status** shows the result and a downloadable **processing
report**. Save it to `/tmp/<slug>/` and parse it:

```bash
$PY $S/listing_bulk.py parse-feedback /tmp/<slug>/processing-report.xlsm
```

It prints per-SKU `[Error|Warning] sku=… code=…: message` and a summary
count (exit code 1 if any error). Fix the flagged rows in the spec and
re-fill / re-upload. Iterate until zero errors, then verify the family
in the catalogue (parent with its children, prices, images).

### First-upload gotchas (verified on a socks template)

- **`recommended_browse_nodes`** is a **gated set** per category — a
  guessed id is rejected. Pick one from the template's Valid Values /
  `Browse data` sheet.
- **`external_product_id` (GTIN/UPC/EAN)** is required per child. An
  own-brand product with no barcode needs a **GTIN exemption** (applied
  once per brand+category in Seller Central) before the create will pass.
- Many **battery / lithium / hazmat** fields are marked Required but are
  only *conditionally* required — leave them blank for a non-battery
  product (set `batteries_required: No`, `are_batteries_included: No`).
