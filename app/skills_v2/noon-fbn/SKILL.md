---
name: noon-fbn
description: "Noon Fulfilled-by-noon (FBN) — ASN creation flow, ASN status enum, inventory export, barcode print, and the FBN Reports page for PER-SKU storage / RTV-removal fee reports. Load when scheduling a shipment, managing FBN inventory, creating an ASN, exporting warehouse stock, or pulling per-SKU storage / return fees."
requires: [noon-shared]
---

# Noon — FBN (Fulfilled by noon)

Load the `noon-shared` skill with the Skill tool first; it covers login,
page structure and common modals.

ASN (Advance Shipping Notice) creation, inventory management, barcode
printing and per-SKU fee reports for noon's fulfilment service.

## Done when

- **ASN:** it has an ASN number and appears on My ASN & Storage
  (`fbn-inbound.noon.partners/en-{cc}`) with a valid status, and its
  products and quantities match the request.
- **Inventory export:** the CSV exists with rows and the warehouse/stock
  columns.
- **Per-SKU fees:** all four Finance reports (Monthly Storage, Long Term
  Storage, Non Saleable Storage, RTV Removal) exist for every requested
  country × service month, each opened and checked against its filename
  (`country_code`, `service_month`), and `scripts/check_fee_rows.py` has
  classified any empty ones. An empty monthly-storage file means the
  month is not published yet — report it as pending, not as a figure, and
  don't regenerate it (a retry returns the same empty file).
- **Reconciliation:** `references/fee-reports.md` § 4 closes — the four
  reports for service month M−1 × (1 + VAT) equal month M's Transaction
  View `balance_transfer`.

## 1. Overview

**URL**: `https://fbn-inbound.noon.partners/en-{cc}?project=PRJ{project_id}`

FBN's inbound pages (My ASN & Storage, Create ASN) live on their own
host, `fbn-inbound.noon.partners`; the rest of FBN (inventory,
returns, fees, reports) stays on `fbn.noon.partners`. Top section
shows **Inbound performance tier** with the 90-day delivery score,
and the page carries **Print barcodes** and **Create ASN** buttons.

## 2. Tabs on My ASN & Storage

| Tab | Purpose |
|-----|---------|
| Shipments (ASN) | List of all ASNs |
| Storage | Warehouse storage summary |
| Requests | Special requests |
| Product dimensions | SKU dimension overrides |
| Manifestation | Per-unit serial / manifestation records |

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
- Status dropdown
- Shipment-type chips: `All shipments` / `Auto-generated` /
  `Drop-off` / `Pickup`

## 5. Create ASN — Full Flow

**URL**: `https://fbn-inbound.noon.partners/en-{cc}/asn/create?project=PRJ{project_id}`

Button entry points:
- "Create ASN" link in left nav
- "Create ASN" button on My ASN & Storage
- "Add FBN Stock" button on product edit page (the `noon-listing` skill § 3.3)

A **4-step wizard** (stepper at the top):
**1 Logistics Mode → 2 Select SKUs → 3 Review ASN Creation →
4 Seal & Schedule**.

### Step 1 — Logistics Mode

Two options:
1. **Self Delivery** — you or your logistics partner deliver
   directly to the assigned FBN warehouse ("Existing flow", no
   pickup fee, supports all storage types)
2. **noon Pickup Service** — noon Logistics collects from your
   warehouse and delivers to FBN (door-to-door, pickup scheduling;
   noon checks warehouse serviceability first; some size
   classifications / storage types are not eligible)

### Step 2 — Select SKUs

Choose SKUs from your catalog or upload a CSV; set quantities per
SKU. Continue advances to Review, then Seal & Schedule.

### Quota commitment — read before Agree

On the way to sealing, noon asks you to acknowledge a **penalty
warning** (delivery commitment; sellers who deliver as committed are not
affected). **Agree & Proceed reserves inbound quota** against the
store's allocation, and a missed commitment can be penalised, so continue
only when the user intends to ship this shipment. To explore without
committing, **Cancel** or step back.

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

The report list on the page also carries other report types beside
these four — **RTV Report - Shipments / RTV Report - Requests**
(the 退仓 record sources) and **FBN Received Report /
FBN Inventory Detail Report** — each row with its parameters
(Country, date window) and a per-row **Download**.

Four things that will bite you, each covered in the reference:

1. **No country field in the modal** — country comes from the
   `en-{cc}` URL segment. The report *list* is project-wide, so you
   only switch URL to generate, not to download.
2. **Downloads are named by report type only** (no country, no month) —
   rename each file as it arrives (`monthly_storage_<cc>_<YYYY-MM>.csv`,
   lowercase country code) or SA/AE overwrite each other.
3. **`charged_amount` is ex-VAT and the settlement lags by one month** —
   month M's settlement is service month M−1, grossed up by VAT
   (SA 15% / AE 5%).
4. **RTV Removal has no `sku`** — join `barcode` → the storage reports'
   `pbarcode` → `sku`. And FBN/ads SKUs are noon-internal
   (`Z…Z-<n>`), NOT the seller codes in the Transaction View's
   `Partner SKUs`; that export's `SKUs` column is the bridge.

Read `references/fee-reports.md` before generating or downloading
anything here — it has the generation flow, the download/rename pattern,
the file layout a monthly report run is checked against, the
reconciliation formula and the join keys.

## Tips

- **"Agree & Proceed" on an ASN commits inbound quota** — only when the
  user intends to ship.
- **A country can be missing a fee report entirely.** Reconcile (see
  the reference § 4); if the sum doesn't close, generate the missing
  report rather than assuming rounding.

## See also

- `noon-fbn/references/fee-reports.md` — per-SKU fee reports in full
- `noon-shared` — login, page structure (prerequisite)
- `noon-listing` — Add FBN Stock from listing edit page
- `noon-exports` — Transaction View (the settlement + the SKU bridge)
- `noon-ads` — per-SKU ad spend (same keyspace trap)
