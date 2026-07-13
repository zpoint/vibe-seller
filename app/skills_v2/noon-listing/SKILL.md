---
name: noon-listing
description: "Noon listing operations — create SKU (3-step wizard) and edit listings (Offer / Content / Sizes / Groups tabs). Load when creating, editing, pricing, restocking, or updating content on a noon SKU."
requires: [noon-shared]
review:
  criteria: |
    - The SKU is ACTUALLY created and live on noon, not just "submitted":
      the create flow reached the success redirect to
      /catalog/{noon_sku}/p, and the Offer tab shows the base price /
      sale price / stock / barcode as entered (committed on the live
      page). Content/Sizes match the request.
    - An edit is done only when the live page reflects it (green success
      + updated values), not on a toast alone.
  verify_by: |
    Open the created SKU's catalog page and its Offer tab; confirm price
    / stock / barcode match what was entered. For an edit, reload the tab
    and confirm the new values persisted. A feed/toast without the live
    page reflecting it is a gap.
---

# Noon — Listing Operations

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, modals, and button-click patterns.

Covers SKU creation and the post-creation edit flow (price, stock,
barcode, content, visibility status).

## 1. Create Listing (SKU)

**URL**: `https://noon-catalog.noon.partners/en/catalog/create?project=PRJ{project_id}`

3-step wizard: **Category → Brand → Identity**.

> **MINIMAL PATH to a valid listing — do exactly these, in order, and
> STOP:**
> 1. Wizard create (Category → Brand → Identity), `fill_input` the SKU.
> 2. **Offer tab** → `fill_input` **Base Price**, then **set Warranty**
>    (MANDATORY — see below), then **Save Changes** (green modal; price
>    persists across reload).
> 3. **Content** on `/d?code=…&tab=content` → `fill_input` **Product
>    Title** + set **Department** → **Save Changes** → "sent for QC"
>    (async — done; do NOT re-fill).
> 4. **Image** (mandatory ≥1): upload one, or leave as the seller's item.
> 5. **Seller Status → ON**.
>
> **Warranty is MANDATORY — but trivial: select "No Warranty".** The
> Offer save FAILS with `Save failed — Warranty / No Offer Created`
> unless the Warranty **type** is set. Do NOT try to configure a real
> warranty (service center + 1–60mo duration — that path IS an
> anti-automation rathole). Just open the Warranty **type** select and
> pick **"No Warranty"** (options: No Warranty / Seller Warranty /
> Manufacturer Warranty) — no service center or duration needed, and the
> offer saves. Open it with a trusted `click_at_xy` on the select's
> centre (it's below the fold — grow the viewport via
> `Emulation.setDeviceMetricsOverride` first), then click the
> **"No Warranty"** option in the `.ant-select-dropdown`. Verified live:
> price + No-Warranty → Save → offer created, persists across reload.
>
> **SKIP the other OPTIONAL fields — do NOT fight their dropdowns.**
> Gender, Size Unit, Feature Bullets, Long Description, Material, Colour
> and the other detailed-content attributes are **optional** — the
> listing saves and goes live without them, and their Ant-Design selects
> resist programmatic opening (**trying to set them is a rathole that
> burns the whole run** — a live run stalled dozens of steps on the
> Gender select). Fill only what steps 2–5 require; if a seller
> explicitly asked for an optional attribute and its dropdown won't open,
> note it as a manual follow-up rather than looping.

**Runnable Offer-tab snippet (price + No Warranty) — copy verbatim.** The
below-fold Warranty select can't be found by an un-scrolled DOM query;
this grows the viewport, trusted-clicks the Warranty card's select, and
picks "No Warranty". It also clears the price field first (`fill_input`
*appends* if a value is already present → `59.59.9`). Verified live:

```bash
browser-use <<'PY'
import time, json
# 1) price — clear first (fill_input appends to an existing value), then fill
js("var i=document.querySelector('input[name=new_price]'); if(i){i.value='';}")
fill_input('input[name="new_price"]', '59.90')
# 2) Warranty = No Warranty (MANDATORY). Grow viewport (it's below fold),
#    trusted-click the Warranty card's ant-select, option-click No Warranty.
cdp("Emulation.setDeviceMetricsOverride", width=1500, height=2200, deviceScaleFactor=1, mobile=False)
time.sleep(2)
info = js("""(function(){
  var hdr=Array.from(document.querySelectorAll('*')).find(e=>e.textContent.trim()==='Warranty'&&e.children.length<3);
  var card=hdr.closest('div'); for(var i=0;i<5&&card;i++){if(card.querySelector('.ant-select'))break;card=card.parentElement;}
  var sel=card.querySelector('.ant-select'); sel.scrollIntoView({block:'center'});
  var r=sel.getBoundingClientRect(); return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
})()""")
p=json.loads(info); click_at_xy(p['x'], p['y']); time.sleep(2)
js("""(function(){var dd=document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  var o=Array.from(dd.querySelectorAll('.ant-select-item')).find(x=>x.textContent.trim()==='No Warranty'); if(o)o.click();})()""")
time.sleep(1); cdp("Emulation.clearDeviceMetricsOverride")
# 3) Save Changes, then confirm Submit
js("(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>/save changes/i.test(x.textContent)&&x.textContent.length<20); if(b)b.click();})()")
time.sleep(3)
js("(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>/^submit$/i.test(x.textContent.trim())); if(b)b.click();})()")
time.sleep(3)
print(js("var t=document.body.innerText; ('Save failed: '+/Save failed/i.test(t)+' | saved: '+/have been saved/i.test(t))"))
PY
```

> **Use the 3-step wizard to create your OWN new product. Do NOT use the
> "paste a noon PDP URL / copy SKU link" shortcut to create a brand-new
> listing.** That shortcut clones an *existing* catalog item and links
> your SKU to a parent you don't own — the product is then permanently
> un-saveable: every Offer save fails with a red
> `Invalid sku_parents: {...}` toast and price/content never persist.
> The wizard below mints a clean standalone parent that saves normally.
>
> **Input method (critical):** fill every field with **`fill_input`**
> (it fires the real key + input/change events React needs). Do NOT use
> `type_text`, `Input.insertText`, or a `nativeSetter` — those set the
> DOM value only; React never ingests it, so "Final Price" stays `-` and
> the save drops the field. Verify a save by **reloading the page** and
> re-reading the value (a green "changes saved" toast alone is not proof;
> a hidden `pricing-errors.undefined` string in the DOM is NOT a real
> error — trust the reloaded value / a screenshot, not a DOM grep).

### Step 1 — Category

Hierarchical tree (e.g. Electronics > Accessories > Cables). Click
down to a leaf; it shows "Selected" badge.

The "Next" button is often **off-screen** at bottom-right. A JS
click works even off-screen:
```bash
browser-use <<'PY'
js("document.querySelectorAll('button')[0].click()")
PY
```

### Step 2 — Brand

Searchable dropdown:
```bash
browser-use <<'PY'
fill_input("input[placeholder*='Brand']", "<brand-name>")   # brand search box
wait_for_load()
print(page_info())      # find the matching dropdown option
# select the option (match by text — the list renders below the input):
js("Array.from(document.querySelectorAll('.ant-select-item-option')).find(o=>/<brand-name>/i.test(o.textContent))?.click()")
js("document.querySelectorAll('button')[0].click()")   # Next
PY
```

Checkbox "This product does not have a brand name" is available for
unbranded products.

### Step 3 — Identity

Enter Partner SKU (your internal code) or click "Generate Partner SKU".

**Important**: "Generate Partner SKU" auto-fills a format like
`PSKU_{project}_{digits}_X`. To use your own SKU, `fill_input` the SKU
box (it has no stable `name=`, so target the visible text input):
```bash
browser-use <<'PY'
fill_input("input[type=text]", "SKU-100234")   # partner SKU box (clears + types via real key events)
# Click "Create" (NOT Next — this is the final step)
js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Create')?.click()")
PY
```
Success = redirect to `/en/catalog/{noon_sku}/p?...` (a fresh noon SKU is
minted). Verified live: this wizard product saves price/content normally.

On success, redirected to:
```
/en/catalog/{noon_sku}/p?code={code}&project=PRJ{project_id}
```

## 2. Edit Listing — After Creation

**URL**: `https://noon-catalog.noon.partners/en/catalog/{sku}/d?code={code}&offerTab=noon&project=PRJ{project_id}`

The product detail/edit page has 5 primary tabs:

| Tab | ID | Purpose |
|-----|----|----|
| Offer | `rc-tabs-0-tab-offer` | Price, stock, barcode, warranty, offer note |
| Content | `rc-tabs-0-tab-content` | Title, description, images, attributes |
| Sizes | `rc-tabs-0-tab-sizes` | Size matrix |
| Groups | `rc-tabs-0-tab-groups` | Product groupings |
| Product Insights | `rc-tabs-0-tab-product-insights` | Performance insights |

And country/market sub-tabs: `rc-tabs-1-tab-noon`, `rc-tabs-1-tab-supermall`, `rc-tabs-1-tab-global`.

### Unsaved Changes Warning

If you switch tabs with unsaved edits, noon shows a modal:
> "Do you want to save the changes you made to this page?"
> [Discard Changes] [Save Changes]

**Always save before navigating away** — discarding loses all input.

### 2.1 Offer Tab — Price

Inputs (all Ant Design shadow DOM, `name=` attribute identifies):

| Field | name | Required |
|-------|------|----------|
| Pricing Method | (dropdown) | Yes — default "Manual" |
| Base Price | `new_price` | Yes |
| Seller Price Minimum | `new_price_min` | Optional |
| Seller Price Maximum | `new_price_max` | Optional |
| Sale Price | `new_sale_price` | Optional (inside expandable Sale Price section) |
| Sale Duration | `sale_duration` | Optional (Start Date / End Date) |

```bash
browser-use <<'PY'
print(page_info())                          # confirm the inputs are present
fill_input("input[name=new_price]",     "99.00")   # base price
fill_input("input[name=new_price_min]", "49.00")   # floor
fill_input("input[name=new_price_max]", "99.00")   # ceiling
# Expand Sale Price section if needed, then:
fill_input("input[name=new_sale_price]", "79.00")  # sale price
PY
```

After filling prices, click the blue **Save Changes** button at top-right.
The modal "Your changes have been saved" with a green check confirms success.

### 2.2 Offer Tab — Barcode

Labeled "Common across marketplaces". Multiple barcodes can be added.

```bash
browser-use <<'PY'
fill_input("input[placeholder*='Barcode']", "TEST1234567890")   # "Enter Barcode" input
# "Add Barcode" button becomes enabled — click it by text:
js("Array.from(document.querySelectorAll('button')).find(b=>/add barcode/i.test(b.textContent))?.click()")
# Barcode appears as a blue chip; input clears
PY
```

Each added barcode shows as a removable chip (Amazon-ASIN-style
strings like `XNNNXXXNNN` are typical — 10 chars, digits + caps).

### 2.3 Offer Tab — Stock

Two sections:
- **FBN Warehouses**: "Add you products to our noon warehouses
  so we can deliver them for you." → **Add FBN Stock** button
  (creates ASN flow — see `noon-fbn` skill)
- **FBP Warehouses**: "Create a warehouse" (for self-fulfillment)

When stock is already configured, FBN section shows:
- Warehouse name (e.g. "Warehouse 1")
- Stock type badge ("Regular")
- Last Stock Update, Stock Transferred, Stock Reserved, Net stock

### 2.4 Offer Tab — Warranty & Offer Note

- **Warranty**: Select warranty duration dropdown ("No warranty" by default)
- **Offer Note**: Free-text textarea (0/353 char counter)

### 2.5 Content Tab

> **Edit content on the `/d` detail page's Content tab, not `/p`.** The
> editable URL is
> `…/catalog/{noon_sku}/d?code={code}&tab=content&project=PRJ{id}` (get
> `{code}` from the My-Catalog row's product link — the read-only `/p`
> view loads no editable fields and is why earlier runs "couldn't fill"
> content). Fill every field with **`fill_input`** (see §1 warning).
>
> **Content save is ASYNC — do NOT re-fill on immediately-stale status.**
> After you fill the mandatory fields and click **Save Changes**, a green
> modal says *"Your changes have been saved. The content will now be sent
> for Quality Check (QC)… allow some time."* The **Content Check Status /
> "N Issues" / "0/7 Attributes" indicators do NOT update instantly** —
> they clear only after Noon's async QC (minutes). Re-filling because the
> count still shows issues is a thrash (a live run re-typed the title 68×
> for this exact reason — the saves *were* landing). Save ONCE, trust the
> "sent for QC" modal, move on, and re-check later. This is the same
> async-confirmation trap as Amazon's async-minting ASINs.
>
> **Mandatory content on noon = Product Title + Department + ≥1 Image.**
> Unlike Amazon (where a blank main image is an acceptable done-state),
> **noon requires at least one product image** for content to pass — a
> listing with title+price but no image keeps a "Missing Image" content
> issue. If the seller hasn't supplied an image, upload a placeholder via
> the Content tab's **Add Image** file-chooser, or surface "Missing
> Image" as the one remaining item for the seller.

Click `rc-tabs-0-tab-content`. Left sub-nav: **Basic Content** /
**Detailed Content**.

#### Mandatory Content section (top)

- **Product Image**: "You need to upload atleast 1 product image"
  with "Add Image" button (opens file picker)
- **English Content Status**: Shows attribute completion (e.g. "0/7 Attributes")
- **Local-language Content Status**: Same for the marketplace's local language

The page shows fine-grained status like "Product Title Missing" with
a direct "Add Product Title" button that scrolls to the field.

#### Basic Content fields (verified list, each has English + local language)

| Field | Required | Notes |
|-------|----------|-------|
| Product Title | Yes | maxlength 1000 |
| Product Fulltype | Auto-filled | From category (e.g. "Electronics Accessories Cables") |
| Brand | Yes | Pre-filled from create step |
| Gender | Optional | Dropdown |
| Long Description | Optional | Rich text editor |
| Size Unit | Optional | Dropdown |
| Feature Bullet (1–5) | Optional | Rich-text bullets |
| GTIN | Optional | Max 1000 chars |

#### Detailed Content fields

| Field | English | Local language |
|-------|---------|--------|
| Colour Name | Yes | Yes |
| Fabric Care Instructions | Optional | Optional |
| HS Code | Optional | Optional |
| Material Composition | Optional | Optional |
| Model Height / Name / Number | Optional | Optional |
| MSRP (per marketplace) | Optional | Optional |
| Size / Year / MPN | Optional | Optional |
| What's In The Box | Optional | Optional |
| Shipping Height/Length/Weight/Width/Depth | Optional | Optional |

#### Filling content — always `fill_input`, never `nativeSetter`

Content fields are the same React-controlled inputs as the Offer tab, so
fill each with **`fill_input`** (matches on placeholder / name). There
are English + local-language variants per field — fill both:

```bash
browser-use <<'PY'
# Title has an English box and a local-language box (same placeholder,
# two elements). fill_input targets one selector; use :nth-of-type or a
# per-element loop with real key events — NOT nativeSetter (React ignores
# a nativeSetter value, leaving the field blank on save/reload).
fill_input("input[placeholder='Product Title *']", "Women's Cotton Crew Socks (6-Pack)")
# for the second (local-language) box, click it then fill:
js("document.querySelectorAll(\"input[placeholder='Product Title *']\")[1]?.focus()")
type_text_note = "use fill_input on a unique selector; if two share a placeholder, focus the 2nd then fill_input its id/xpath"
PY
```

> A `nativeSetter` + `dispatchEvent('input')` sets the DOM value but does
> NOT enter React state — the field looks filled yet saves blank and is
> empty on reload. This was a real live failure. Use `fill_input`.

Always click **Save Changes** after editing, then **reload and re-read**
the field to confirm it persisted (the green toast alone is not proof).

### 2.6 Product Visibility Status

Top of the page shows:
- **Seller Status**: toggle (on/off) — controls whether the offer is live
- **Live Status**: badge (e.g. "Offer Created", "Unavailable")
- **Buy Box Won**: ACTIVE badge

## See also

- `noon-shared` — login, page structure, modals (prerequisite)
- `noon-fbn` — adding FBN stock to a listing (Add FBN Stock button)
- `noon-ads` — promote a listing via Ad Manager
