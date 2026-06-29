---
name: noon-listing
description: "Noon listing operations — create SKU (3-step wizard) and edit listings (Offer / Content / Sizes / Groups tabs). Load when creating, editing, pricing, restocking, or updating content on a noon SKU."
requires: [noon-shared]
---

# Noon — Listing Operations

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, modals, and button-click patterns.

Covers SKU creation and the post-creation edit flow (price, stock,
barcode, content, visibility status).

## 1. Create Listing (SKU)

**URL**: `https://noon-catalog.noon.partners/en/catalog/create?project=PRJ{project_id}`

3-step wizard: **Category → Brand → Identity**.

### Step 1 — Category

Hierarchical tree (e.g. Electronics > Accessories > Cables). Click
down to a leaf; it shows "Selected" badge.

The "Next" button is often **off-screen** at bottom-right. Use JS:
```bash
browser-use eval "document.querySelectorAll('button')[0].click()"
```

### Step 2 — Brand

Searchable dropdown:
```bash
browser-use input <brand-input> "<brand-name>"
browser-use state                    # find dropdown option
browser-use click <brand-option>     # select — value is now set
browser-use eval "document.querySelectorAll('button')[0].click()"  # Next
```

Checkbox "This product does not have a brand name" is available for
unbranded products.

### Step 3 — Identity

Enter Partner SKU (your internal code) or click "Generate Partner SKU".

**Important**: "Generate Partner SKU" auto-fills a format like
`PSKU_{project}_{digits}_X`. Clear it first if you want your own SKU:
```bash
browser-use click <sku-input>
browser-use keys "Control+a"
browser-use type "MY-SKU-001"
# Click "Create" (NOT Next — this is the final step)
browser-use eval "document.querySelectorAll('button')[1].click()"
```

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
browser-use state                                 # find inputs
browser-use input <base-price>  "<base>"      # e.g. 99.00
browser-use input <price-min>   "<floor>"     # e.g. 49.00
browser-use input <price-max>   "<ceiling>"   # e.g. 99.00
# Expand Sale Price section if needed, then:
browser-use input <sale-price>  "<sale>"      # e.g. 79.00
```

After filling prices, click the blue **Save Changes** button at top-right.
The modal "Your changes have been saved" with a green check confirms success.

### 2.2 Offer Tab — Barcode

Labeled "Common across marketplaces". Multiple barcodes can be added.

```bash
browser-use state                        # find "Enter Barcode" input
browser-use input <barcode-input> "TEST1234567890"
# "Add Barcode" button becomes enabled
browser-use click <add-barcode-btn>
# Barcode appears as a blue chip; input clears
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

#### Filling content via JS (bypasses shadow DOM issues)

Some inputs are inside shadow DOM. If `browser-use input` doesn't
trigger the React onChange, use the native setter pattern:

```bash
browser-use eval "
var nsetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
var inputs = document.querySelectorAll('input[placeholder=\"Product Title *\"]');
inputs.forEach(function(inp, i) {
  nsetter.call(inp, i === 0 ? 'English title' : 'local-language title');
  inp.dispatchEvent(new Event('input', {bubbles: true}));
});
"
```

Always click **Save Changes** after editing. Validate with the green
"Your changes have been saved" toast.

### 2.6 Product Visibility Status

Top of the page shows:
- **Seller Status**: toggle (on/off) — controls whether the offer is live
- **Live Status**: badge (e.g. "Offer Created", "Unavailable")
- **Buy Box Won**: ACTIVE badge

## See also

- `noon-shared` — login, page structure, modals (prerequisite)
- `noon-fbn` — adding FBN stock to a listing (Add FBN Stock button)
- `noon-ads` — promote a listing via Ad Manager
