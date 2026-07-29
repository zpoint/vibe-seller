---
name: noon-fbn
description: "Noon Fulfilled-by-noon (FBN) — ASN creation flow, ASN status enum, inventory export, barcode print, and the FBN Reports page for PER-SKU storage / RTV-removal fee reports. Load when scheduling a shipment, managing FBN inventory, creating an ASN, exporting warehouse stock, or pulling per-SKU storage / return fees."
requires: [noon-shared]
review:
  criteria: |
    - The ASN was ACTUALLY created: it has an ASN number and appears in
      My ASN & Storage with a valid status; its product list and
      quantities match the request. A "submitted" claim without the ASN
      showing in the list is not done.
    - If an inventory export was requested, the CSV exists and is
      non-empty with the expected columns.
    - If per-SKU FBN fees were requested: ALL FOUR Finance reports
      (Monthly Storage, Long Term Storage, Non Saleable Storage, RTV
      Removal) exist for EVERY requested country x service month, each
      as its own file whose name states the country and month. A report
      that was missing on the page and never generated is a gap, not
      "not applicable" — the only acceptable empty report is one that
      downloads with a header row and genuinely zero data rows.
    - The reconciliation in references/fee-reports.md § 4 was actually
      computed and closes: sum(charged_amount) over the four reports for
      service month M-1, x (1+VAT), equals month M's Transaction View
      balance_transfer. An unreconciled pull is not done.
  verify_by: |
    Open the FBN ASN page (fbn.noon.partners/.../asn); confirm the new
    ASN number + status + shipment contents match. If inventory export
    was asked, open the CSV and confirm non-zero rows + warehouse/stock
    columns. For per-SKU fees: list the output dir and confirm one file
    per (report type x country), each with a sku (or barcode) column and
    a charged_amount column; then print the § 4 reconciliation showing
    the detail total, the VAT gross-up, and the settlement it matches.
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

## 8. FBN Reports — per-SKU storage & RTV removal fees

**URL**: `https://fbn.noon.partners/en-{cc}/fbnreports?project=PRJ{project_id}`

The Transaction View export gives the month's FBN storage as **one
lumped `balance_transfer` row with no SKU attribution**. This page is
where the per-SKU breakdown lives — four Finance reports that together
reconcile exactly to that settlement:

| Report | Key | Gives you |
|---|---|---|
| Monthly Storage Charge | `sku` | per-SKU monthly storage fee + average stock |
| Long Term Storage Charge | `sku` | per-SKU long-term (aged 6m/12m) storage fee |
| Non Saleable Storage Charge | `sku` | per-SKU non-saleable ageing storage fee |
| RTV Removal Charge | `barcode` | per-barcode removal/return fee + `shipped_qty` |

Four things that will bite you, each covered in the reference:

1. **No country field in the modal** — country comes from the
   `en-{cc}` URL segment. The report *list* is project-wide, so you
   only switch URL to generate, not to download.
2. **Downloads are named by report type only** (no country, no month) —
   rename each file immediately or SA/AE overwrite each other.
3. **`charged_amount` is ex-VAT and the settlement lags by one month** —
   month M's settlement is service month M−1, grossed up by VAT
   (SA 15% / AE 5%).
4. **RTV Removal has no `sku`** — join `barcode` → the storage reports'
   `pbarcode` → `sku`. And FBN/ads SKUs are noon-internal
   (`Z…Z-<n>`), NOT the seller codes in the Transaction View's
   `Partner SKUs`; that export's `SKUs` column is the bridge.

> **Read `references/fee-reports.md`** before generating or downloading
> anything here — it has the generation flow, the download/rename
> pattern, the reconciliation formula, and the join keys.

## Tips

- **Don't "Agree & Proceed" ASN unless committing** — it reserves
  quota that counts against your allocation.
- **A country can be missing a fee report entirely.** Reconcile (see
  the reference § 4); if the sum doesn't close, generate the missing
  report rather than assuming rounding.

## See also

- `noon-fbn/references/fee-reports.md` — per-SKU fee reports in full
- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — Add FBN Stock from listing edit page
- `noon-exports` — Transaction View (the settlement + the SKU bridge)
- `noon-ads` — per-SKU ad spend (same keyspace trap)
