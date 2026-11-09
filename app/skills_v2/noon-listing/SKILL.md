---
name: noon-listing
description: "Noon listing operations — create SKUs (NIS spreadsheet import or the 3-step click wizard) and edit listings (Offer / Content / Sizes / Groups tabs: price, stock, barcode, content, visibility), plus deactivation. Load when creating, editing, pricing, restocking, deactivating, or updating content on a noon SKU."
requires: [noon-shared]
---

# Noon — Listing Operations

Load the `noon-shared` skill with the Skill tool first; it covers login,
page structure, the country switcher, modals and button-click patterns.

When writing or rewriting copy (title, bullets, description, EN and AR),
load the `amazon-listing` skill and follow its
`references/listing-copy.md`: fact sheet from every detail tile, the real
category, a competitor check, then review.

## Done when

- **Create:** the SKU exists on noon, not just "submitted". The click
  wizard reached `/catalog/{noon_sku}/p`, and the Offer tab shows the
  price / stock / barcode entered, still there after a reload.
- **NIS import:** the import's Report File is non-empty and every row has
  a `catalog_sku`. `Status: Complete` and the processed-rows counter are
  not evidence; an import that created nothing shows both.
- **Bilingual:** every field the request gave in English has its Arabic
  half on the live Content tab (title, long description, each bullet),
  not a stub a fraction of the English length.
- **Edit:** the reloaded page shows the new values. A toast alone is not
  proof.

## Choosing a create path

Prefer the **file-based NIS import** (§ 1). It sets every field through a
spreadsheet, so it never touches the Ant-Design dropdowns (Warranty,
Department, Gender, Size, content selects) that the click wizard can open
only with trusted mouse clicks — the recurring obstacle on that path. Use
the **click wizard** (§ 2) for a one-off when the import is impractical,
and the edit tabs (§ 3) for changes after creation.

## 1. Create — NIS import (preferred)

Full procedure: `references/nis-import.md`. The points that decide
whether an import actually creates anything:

- Imports → Add Import → **Content / NIS Create/Update** → Specific
  category (+ marketplace) → Download the English + Arabic template.
- Each product is a **parent row plus at least one child row** sharing a
  `parent_group_key`; a lone parent creates nothing. One child per size.
  Price/stock imports key on the child `seller_sku`.
- **Leave fields blank unless the task gave the value.** A value set in
  error is hard to undo on noon (a blank cell in a later update means "no
  change"), so ask the user rather than inventing one.
- Delete the template's sample row, fill both halves of each bilingual
  pair, then run `validate_nis.py` before uploading.
- Evidence of creation is the import's **Report File** with a
  `catalog_sku` per row. After import, enter the Arabic seller size on
  the Sizes tab — NIS has no column for it.

## 2. Create — click wizard (fallback)

Full procedure and snippets: `references/click-wizard.md`. In short:
Category → Brand → Identity → **Create**, then Offer tab (base price +
Warranty "No Warranty" → Save → Submit), Content tab (title EN + AR,
**Gender**), at least one real product image, Seller Status on. Gender
and Warranty are required; the other content attributes are optional.

## 3. Edit a listing

**URL**: `https://noon-catalog.noon.partners/en/catalog/{sku}/d?code={code}&offerTab=noon&project=PRJ{project_id}`

Get `{code}` from the My Catalog row's product link. Click tabs and the
noon / supermall / global sub-tabs by their visible text — the tab ids are
generated per render.

| Tab | Purpose |
|-----|---------|
| Offer | Price, stock, barcode, warranty, offer note |
| Content | Title, description, images, attributes |
| Sizes | Size variants of one parent |
| Groups | Product groupings |
| Product Insights | Performance insights |

Switching tabs with unsaved edits opens "Do you want to save the changes
you made to this page?" — choose **Save Changes**; Discard loses the
input.

Every save opens a **Submit** confirmation. Click it, then reload and
re-read the field; that reload is the proof.

### 3.1 Offer — price

Inputs are identified by `name=`:

| Field | name | Required |
|-------|------|----------|
| Pricing Method | (dropdown) | Yes — default "Manual" |
| Base Price | `new_price` | Yes |
| Seller Price Minimum | `new_price_min` | Optional |
| Seller Price Maximum | `new_price_max` | Optional |
| Sale Price | `new_sale_price` | Optional (inside the expandable Sale Price section) |
| Sale Duration | (date-range control) | Optional, inside the Sale section |

```bash
browser-use <<'PY'
fill_input("input[name=new_price]",     "99.00")
fill_input("input[name=new_price_min]", "49.00")
fill_input("input[name=new_price_max]", "99.00")
fill_input("input[name=new_sale_price]", "79.00")   # after expanding Sale Price
PY
```

Then the blue **Save Changes** (top right); "Your changes have been
saved" with a green check confirms it.

### 3.2 Offer — barcode

The barcode noon wants is the **Amazon FNSKU**, not the ASIN. The FNSKU
is the code on the FBA label, shape `X00` + 7 characters (e.g.
`X00EXAMPL1`); an ASIN (`B0EXAMPLE1`) has the same length and alphabet
but is a different identifier, and warehouse scans of the physical unit
never match it. Read the value from an FNSKU source:

| Source | How |
|---|---|
| Manage FBA Inventory report (all SKUs at once) | `sellercentral.amazon.{tld}/reportcentral/FBA_MYI_UNSUPPRESSED_INVENTORY/1` → download the newest row; TSV columns `sku`, `fnsku`, `asin`. Regenerated daily. |
| Amazon Fulfilled Inventory report | `/reportcentral/AFNInventoryReport/1` — same `fnsku` column, only SKUs with stock |
| FBA Inventory UI | per-SKU `FNSKU` column |

The noon partner SKU and the Amazon seller SKU are usually the same
string for a store that lists the same catalogue on both; confirm on one
live noon SKU that already has a barcode before relying on it.

**Amazon SA and AE issue different FNSKUs for the same SKU, and noon's
barcode field is common across marketplaces**, so add every FNSKU for the
SKU as its own chip — one per Amazon marketplace the store sells on. Pull
the second marketplace's report by switching account in Amazon's
marketplace switcher (see the `amazon-shared` skill), not by browsing to
the other country's domain. If the switcher lacks a marketplace, fill
what you can and report the gap.

The card is `div#barcode-card`; its input has no `name` or `placeholder`,
so scope to the card and tag the input:

```bash
browser-use <<'PY'
import time
cdp("Emulation.setDeviceMetricsOverride", width=1920, height=1600,
    deviceScaleFactor=1, mobile=False)   # the card sits below the fold
js("document.getElementById('barcode-card').scrollIntoView({block:'center'})")

def add_one(code):
    js("document.getElementById('barcode-card').querySelector('input').setAttribute('data-bc','1')")
    fill_input('#barcode-card input[data-bc="1"]', code)   # enables the button
    box = js("""
      var c=document.getElementById('barcode-card');
      var b=[].slice.call(c.querySelectorAll('button'))
              .find(function(x){return /add barcode/i.test(x.innerText||'');});
      var r=b.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    """)
    click_at_xy(box["x"], box["y"])
    for _ in range(20):          # wait for the chip before anything else
        chips = js("var c=document.getElementById('barcode-card');"
                   "return c ? [].slice.call(c.querySelectorAll('.ant-tag-blue'))"
                   ".map(function(e){return e.innerText.trim();}) : null;")
        if chips and code in chips:
            return chips
        time.sleep(3)
    raise SystemExit("barcode %s never committed" % code)

add_one("X00EXAMPL1")      # SA FNSKU
add_one("X00EXAMPL2")      # AE FNSKU, only after the first chip shows
cdp("Emulation.clearDeviceMetricsOverride")
PY
```

**Add Barcode saves on its own and re-renders the card** — there is no
Save Changes step. Two consequences that both look like "the barcode
didn't stick": reading straight after the click returns an empty card
(mid-render, not a failure — poll for the chip), and a second add sent
while the first save is in flight is silently dropped. Confirm chip *n*
before typing chip *n+1*. Chips are `.ant-tag-blue`; the "Common across
marketplaces" label is a plain `.ant-tag`. Reload and confirm the chips
are still there.

### 3.3 Offer — stock

- **FBN Warehouses** → **Add FBN Stock** starts an ASN (the `noon-fbn`
  skill).
- **FBP Warehouses** → "Create a warehouse" for self-fulfilment.

With stock configured, the FBN section shows the warehouse name, stock
type ("Regular"), Last Stock Update, Stock Transferred, Stock Reserved and
Net stock.

### 3.4 Offer — warranty and offer note

- **Warranty**: type select; "No Warranty" is enough for the offer to
  save (see `references/click-wizard.md` for opening it).
- **Offer Note**: free text, 353-character limit.

### 3.5 Content tab

Edit on the `/d` page with `&tab=content`
(`…/catalog/{noon_sku}/d?code={code}&tab=content&project=PRJ{id}`); the
read-only `/p` view loads no editable fields. Left sub-nav: **Basic
Content** / **Detailed Content**.

**Required for content to pass:** Product Title, Gender (which sets
Department) and at least one product image. Unlike Amazon, noon will not
pass content without an image; if the seller has supplied none, report
"Missing Image" as the remaining item.

**Content save is asynchronous.** After Save Changes, a green modal says
the content is sent for Quality Check. The Content Check Status, "N
Issues" and "0/7 Attributes" indicators update only after QC finishes,
minutes later. Save once, trust the modal, re-check later — re-filling
because the counters still show issues just repeats saves that already
landed.

Basic Content (each with English + local-language variant):

| Field | Required | Notes |
|-------|----------|-------|
| Product Title | Yes | maxlength 1000 |
| Product Fulltype | Auto-filled | From category |
| Brand | Yes | Pre-filled from create |
| Gender | Yes | Dropdown; sets Department |
| Long Description | Optional | Rich text |
| Size Unit | Optional | Dropdown |
| Feature Bullet (1–5) | Optional | Rich text |
| GTIN | Optional | |

Detailed Content: Colour Name (EN + local), Fabric Care Instructions, HS
Code, Material Composition, Model Height / Name / Number, MSRP (per
marketplace), Size / Year / MPN, What's In The Box, Shipping
dimensions/weight — all optional.

**Filling.** These are React-controlled inputs: Latin text with
`fill_input`, Arabic with `type_text` after focusing (see
`references/click-wizard.md`). On these inputs `fill_input`'s built-in
select-all does not always clear existing text, so an edit of live copy
can come out as `<old><new>`. Clear the field first, fill, and read it
back before saving:

```bash
browser-use <<'PY'
js("""var e=document.querySelectorAll("input[placeholder='Product Title *']")[0];
Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'');
e.dispatchEvent(new Event('input',{bubbles:true}));""")   # [1] is the Arabic box
fill_input("input[placeholder='Product Title *']", "USB-C Charger 65W, 3-Port")
print(js("return document.querySelectorAll(\"input[placeholder='Product Title *']\")[0].value"))
PY
```

The native setter is fine for clearing; use `fill_input` for the value
itself. Leave `®` and `™` out of every field — noon drops the whole save
without an error (Submit closes and the reload shows the old copy).

### 3.6 Sizes tab

The click equivalent of NIS parent + child rows. On the parent SKU's
Sizes tab: "Create size variants for this SKU" → **Add Size**, one row per
size with Partner SKU / Seller Size (EN) / Seller Size (AR) / Display Size
/ noon Size. Each size becomes a child noon-SKU (`<parent>-1`, `-2`, …).
Use the NIS import for many sizes at once and this tab to add or adjust a
size on an existing parent, including the Arabic seller size that NIS
cannot set.

### 3.7 Visibility

Top of the page: **Seller Status** toggle (whether the offer is live),
**Live Status** badge ("Offer Created", "Unavailable"), **Buy Box Won**.

## 4. Deactivate a listing

noon has no hard delete. **Deactivate** pulls the SKU from sale across
noon, supermall and Global, and **Activate SKUs** reverses it.

1. **My Catalog** → type the partner SKU in "Search for SKU here…" and
   press **Enter** (typing alone does not filter).
2. Check the result before ticking anything. The search is prefix/fuzzy,
   so "Total 1 items" can be a different SKU (searching `WIDGET-010` can
   return `WIDGET-011-OS`). Tick the row only when its `PSKU:` matches
   your target exactly; if no row matches, the SKU is not listed. Do not
   use the header checkbox — it selects every SKU in the catalogue,
   including live sellers.
3. Click **Deactivate SKUs** and confirm the modal ("Deactivate this
   SKU? … across noon, supermall, and Global").

## See also

- `noon-shared` — login, page structure, country switcher, modals
- `noon-fbn` — FBN stock via ASN (the Add FBN Stock button)
- `noon-ads` — promote a listing via Ad Manager
