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
      "not applicable".
    - Empty reports were CLASSIFIED, not waved through. Run
      scripts/check_fee_rows.py over the output folder. A header-only
      rtv_removal file is fine (removals are events; a month can have
      none). A header-only monthly/longterm/nonsaleable storage file is
      NOT fine — storage accrues against held stock, so zero rows means
      the service month is not published yet. Report those as pending
      for the month they belong to; never present one as a delivered
      figure, and never retry them (a second generation returns a
      byte-identical empty file).
    - Every non-empty storage report was OPENED and its own
      `country_code` / `service_month` columns match the country and
      month in its filename. The filename is a manual rename applied
      after the download, so it proves nothing on its own — a
      mis-clicked row yields a perfectly named file full of the wrong
      country's money. Two files for the same report type and month must
      not be byte-identical, and the two ads xlsx must not have equal
      `Spends` totals.
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
- "Add FBN Stock" button on product edit page (see `noon-listing § 2.3`)

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
warning** (delivery commitment; sellers who consistently deliver as
committed are not affected). **Proceeding past it reserves inbound
quota** — noon counts this against your allocation. Only continue
if you actually intend to ship. For exploration without committing,
**Cancel** / step back.

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
