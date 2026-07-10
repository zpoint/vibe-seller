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
"Sock"), choose the language, then **select the store's own single
marketplace only** (uncheck the others — the picker defaults to several,
and multi-marketplace disables Listing Preferences), then **Generate
Spreadsheet**. The file lands in `~/.vibe-seller/downloads/<slug>/`
(a macro-enabled `.xlsm`).

> **Getting TO the product-type search (current Beta flow — don't
> reverse-engineer it):** Spreadsheet → **Download Blank Template** lands
> on `/product-search/bulk/generate` showing **template cards** (e.g.
> "List products that are not currently in Amazon's catalog"). The card
> body text is NOT the button — the clickable control is a **`kat-button`
> in the card's FOOTER**. Find it via the DOM, not a screenshot:
> `js` for `kat-card kat-button` → `getBoundingClientRect` → `click_at_xy`
> (see browser-harness "Locate & click without vision"). That navigates to
> `/product-search/bulk/generate/add-product`, the product-type search page.
>
> **Product-Type search gotcha:** the search box is a locked `kat-input` —
> setting its value via `js(...)` alone does **not** open the candidate
> list. Locate and `click_at_xy` the **search icon** (`kat-icon
> name=search`) at the right of the input row to trigger the suggestions,
> then `click_at_xy` the **Select** button on the matched product type.

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
                  "item_name": "ACME ... (White)", "target_gender": "Male",
                  "age_range_description": "Adult",
                  "recommended_browse_nodes": "<from Browse data>",
                  "fulfillment_availability#1.fulfillment_channel_code": "DEFAULT",
                  "fulfillment_availability#1.quantity": "100",
                  "purchasable_offer[marketplace_id=<OWN_MKT>]#1.our_price#1.schedule#1.value_with_tax": "29.00" } }
  ]
}
```

> Fill only the store's **own** marketplace offer block (one
> `purchasable_offer[marketplace_id=…]`), and omit `main_image_url` when
> the only image you have is a hotlinked supplier URL (add images later
> via the image flow). Give each child its category's full required set,
> not just the differentiator (see the Parent-vs-Child note below).

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

> **Children are NOT minimal — fill each child's full required set.**
> On a real socks create, minimal children (only `parent_sku` + colour +
> offer) were rejected: Amazon required `item_name`, `target_gender`,
> `age_range_description`, `apparel_size_system`, and the compound
> `apparel_body_type` / `apparel_height_type` **on every child row**. So
> put the shared required attributes on the children too, not just the
> parent. `fill`'s "missing required field(s)" warnings on child rows
> are worth heeding, but the authoritative list is what the **processing
> report** flags per SKU — read it and add exactly those fields.
> `external_product_id` is the exception: a **GTIN-exempt** brand leaves
> it blank (set `brand_name`); the feed may still print an `8560` on
> children, yet the ASINs create under the exemption — verify in
> inventory, not the feed count.

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

## 4. Upload the `.txt` + read the processing report

**Upload the tab-delimited `.txt` that `fill` wrote, NOT the `.xlsm`.**
An openpyxl-saved `.xlsm` is rejected with a **90502 FATAL** ("the file
does not contain a worksheet with a template type that is supported for
Excel upload") — the re-save alters the macro-workbook structure Amazon
validates, and its own remedy is to upload a tab-delimited text file.
The `.txt` reaches real content validation, which is what you want.

On the upload page the file input is a `kat-file-upload` widget. Its
`shadowRoot input#kat-file-attachment` is an **inert placeholder** — a
`DOM.setFileInputFiles` on it returns success but leaves `files.length`
at **0** (verified: objectId, `backendNodeId`, and `getDocument(pierce)`
all no-op), so DON'T set that node and DON'T click "Browse"/`file://`.

The widget creates its **real** input only when the "Upload file" button
is clicked with a **trusted** gesture, and hands it to you via
`Page.fileChooserOpened`. Intercept the chooser, trusted-click the button,
set the file on that `backendNodeId` (`cdp()` params are keyword args):

```bash
browser-use <<'PY'
import time
cdp('Page.enable'); cdp('Page.setInterceptFileChooserDialog', enabled=True)
drain_events()
box = js("""var b=document.querySelector('kat-file-upload').shadowRoot.querySelector('#select-file');
            var r=b.getBoundingClientRect();
            return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};""")
click_at_xy(box['x'], box['y'])          # TRUSTED click (not JS .click())
bnid = None
for _ in range(8):
    time.sleep(0.5)
    for e in drain_events():
        if 'fileChooserOpened' in str(e.get('method','')): bnid = e['params']['backendNodeId']
    if bnid: break
cdp('DOM.setFileInputFiles', backendNodeId=bnid, files=['/tmp/<slug>/listing.txt'])
cdp('Page.setInterceptFileChooserDialog', enabled=False)
print('attached to', bnid)
PY
```

(Generic recipe + the plain-input Method 1: `browser-harness` SKILL.md §
"Uploading a file".) Amazon stages the file, showing the filename +
green **"File Type … (Automatically detected)"** and enabling **Submit
products**. Confirm via `capture_screenshot()` + Read (the shadow-root
text carries hidden "unsuccessful" strings even on success — don't trust
it). Then `click_at_xy` **Submit products** and `wait_for_load()`. Amazon
processes asynchronously; the batch row on **Check Upload Status** shows
`SKUs successful / submitted` and a **Download Processing Summary** link.
Save it and parse it:

```bash
$PY $S/listing_bulk.py parse-feedback /tmp/<slug>/processing-summary.xlsm
```

It prints per-SKU `[Error|Warning] sku=… code=…: message`. Fix the
flagged fields and re-upload.

> **The status page lists many past batches — download the report from
> the row whose filename matches THIS upload**, or you'll parse a stale
> report (confirm the report's `timestamp=` in row 1 is recent).

**Done = the family is in inventory, not "0 errors".** Verify on Manage
Inventory (or `skucentral?mSku=<sku>` **without** `&condition=New`):
each SKU has an ASIN and the parent shows **"Variations (N)"**. The feed
count under-reports — records with errors still create stubs, and later
re-uploads can complete them.

### First-upload gotchas (verified on a socks template — priors, not a checklist)

- **`recommended_browse_nodes`** is a **gated set** per category — a
  guessed id is rejected. Pick one from the template's `Browse data`
  sheet. Put it on **every** row (parent + children).
- **Enum case is exact** (`apparel_size_system` = `UAE/KSA`, not
  `uae/ksa`). `fill` canonicalises to the template's casing, but if you
  hand-edit, match the Valid Values sheet exactly.
- **Compound attributes arrive as a set.** Apparel Size needs
  `apparel_size_class` + `apparel_size_system` + `apparel_body_type` +
  `apparel_height_type` together — a partial set errors 99001/99022.
- **`8560` on a buyable child ("doesn't match any ASINs … include
  standard_product_id")** — Amazon won't mint a new ASIN. Decide by
  whether that child's ASIN already exists:
  - **Exists** (re-submit, or a prior create left a catalog ASIN — a
    `delete` removes the SKU/offer, **not** the catalog ASIN): **match**
    it — `operation: update`, `external_product_id` = the ASIN,
    `external_product_id_type: asin`. This is what lets a stuck variation
    child join its family (verified fix).
  - **Genuinely new, GTIN-exempt brand:** leave `external_product_id`
    blank + set `brand_name`, and fill the **key defining attributes the
    report names as missing** (e.g. `material_type`, `pattern_name`) so
    the ASIN can be minted. The exemption alone is not enough. Exemptions
    are per brand+category+**marketplace**.
  Set `update_delete` on every row, children included.
- **Offer/price is per-marketplace; verify in that marketplace's Pricing
  view.** Fill one offer block —
  `purchasable_offer[marketplace_id=<MKT>]#1.our_price#1.schedule#1.value_with_tax`
  (`our_price` is the only price column that matters) +
  `fulfillment_availability#1.quantity`. The feed count doesn't reflect
  price; quantity can apply while Pricing shows `--` if you set a
  different marketplace's column. On a bundled multi-marketplace
  template, fill only the intended marketplace's block.
- **`main_image_url`:** by default not uploaded here (seller adds images)
  — a `18320` image error is *expected*, not a blocker. Never hotlink a
  supplier CDN URL (1688/alibaba `cbu01.alicdn.com`, tmall) — Amazon
  can't fetch a referer-protected image, so the listing is suppressed.
- Many **battery / lithium / hazmat** fields are marked Required but are
  only *conditionally* required — leave them blank for a non-battery
  product (set `batteries_required: No`, `are_batteries_included: No`).
