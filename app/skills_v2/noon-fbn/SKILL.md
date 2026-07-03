---
name: noon-fbn
description: "Noon Fulfilled-by-noon (FBN) — ASN creation flow, ASN status enum, inventory export, barcode print. Load when scheduling a shipment, managing FBN inventory, creating an ASN, or exporting warehouse stock."
requires: [noon-shared]
---

# Noon — FBN (Fulfilled by noon)

> **PREREQUISITE:** Read `../noon-shared/SKILL.md` for login, page
> structure, and common modals.

ASN (Advance Shipping Notice) creation, inventory management, and
barcode printing for noon's fulfillment service.

## 1. Overview

**URL**: `https://fbn.noon.partners/en-{cc}/asn?project=PRJ{project_id}`

Top section shows **Inbound Performance Tier** (Launchpad, etc.)
with 90-day delivery score. Best practices: "Avoid creating ASNs
with no intention to ship", "Ship ASNs on time", "Ensure delivered
quantities match what you scheduled."

## 2. Tabs on My ASN & Storage

| Tab | Purpose |
|-----|---------|
| Shipments (ASN) | List of all ASNs |
| Storage | Warehouse storage summary |
| Requests | Special requests |
| Product Dimensions | SKU dimension overrides |
| Serialization Service | Per-unit serial numbers |

## 3. ASN Status Values (verified)

From the Status filter dropdown on the Shipments tab:

| Status | Meaning |
|--------|---------|
| Created | ASN drafted, not submitted |
| Cancelled | ASN cancelled |
| Pending | Awaiting noon review |
| Sealed | Sealed for shipment |
| Scheduled | Delivery scheduled |
| Handed Over | Handed to courier/noon |
| Receiving | Being received at warehouse |
| Putaway In Progress | Being stocked |
| GRN Completed | Goods Receipt Note completed (fully stocked) |

## 4. Filters

- Search by ASN #
- Status multi-select dropdown
- Auto Generated / Manual / 70 Active checkbox toggles

## 5. Create ASN — Full Flow

**URL**: `https://fbn.noon.partners/en-{cc}/asn/createasn?project=PRJ{project_id}`

Button entry points:
- "Create ASN" link in left nav
- "Create ASN" button at top-right of Shipments tab
- "Add FBN Stock" button on product edit page (see `noon-listing § 2.3`)

### Step 1 — Product Selection Method

Two options:
1. **Choose from Your Catalog** — searchable product list
2. **Upload CSV File** — download template, fill, upload

### Step 2 — Select Products (if "From Catalog")

Table columns: Product Name, SKU, Quantity (editable), Volume,
Storage Type, Size Classification. Click a row checkbox to select,
then adjust quantity. Bottom bar shows "N items selected" with:
- **Add Serialization** button
- **Continue** button

```bash
browser-use <<'PY'
# select the product row (qty=1 default) — match its checkbox by row text
js("Array.from(document.querySelectorAll('tr')).find(r=>/SKU-100234/.test(r.textContent))?.querySelector('input[type=checkbox]')?.click()")
js("Array.from(document.querySelectorAll('button')).find(b=>/continue/i.test(b.textContent))?.click()")
PY
```

URL advances to `?type=catalog&step=1`.

### Step 3 — Penalty Warning Modal

Before quota reservation, a modal appears:
> **Penalty Warning**
> You selected these items for delivery:
> Products Selected: N
> Selected Quantity: N
> Sellers who consistently deliver as committed are not affected.
> [Cancel] [Agree & Proceed]

**Clicking "Agree & Proceed" reserves quota** — noon counts this
against your inbound quota allocation. Only click if you actually
intend to ship.

For exploration without committing, click **Cancel**.

```bash
# exit without reserving:
browser-use <<'PY'
js("Array.from(document.querySelectorAll('button')).find(b=>/cancel/i.test(b.textContent))?.click()")
PY
# OR reserve quota & continue:
browser-use <<'PY'
js("Array.from(document.querySelectorAll('button')).find(b=>/agree.*proceed/i.test(b.textContent))?.click()")
PY
```

### Remaining Steps (after Agree)

- Shipping details (warehouse destination, carrier)
- Box & pallet details
- Appointment scheduling
- Review & submit → generates ASN # (format: `ASN-B-{digits}N` or similar)

## 6. My Inventory Export

**URL**: `https://fbn.noon.partners/en-{cc}/inventory?project=PRJ{project_id}`

Summary cards: Total Stock, Saleable Stock (count + %), Non-Saleable.

Tabs: All Warehouses, Saleable, Non-Saleable.

Export button triggers direct CSV download (no modal).

```bash
browser-use <<'PY'
new_tab("https://fbn.noon.partners/en-<cc>/inventory?project=PRJ{project_id}")
wait_for_load()
print(page_info())
# Export triggers a direct CSV download (no modal) — click by text:
js("Array.from(document.querySelectorAll('button')).find(b=>/export/i.test(b.textContent))?.click()")
PY
```

Warehouse codes follow a `<CITY-ABBR><NN><OPTIONAL-SUFFIX>` shape
(e.g. a 3-letter city code like `XYZ##S`). Read the exact code from
the page; don't hardcode.

## 7. Print Barcodes

The My ASN & Storage page has a **Print Barcodes** button at the top
to generate printable barcodes for FBN shipments.

## Tips

- **Don't "Agree & Proceed" ASN unless committing** — it reserves
  quota that counts against your allocation.

## See also

- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — Add FBN Stock from listing edit page
