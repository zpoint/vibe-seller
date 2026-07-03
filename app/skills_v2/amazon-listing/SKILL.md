---
name: amazon-listing
description: "Amazon listing CRUD via the category flat-file (Add Products via Upload). Create a variation family (parent + colour/size children), update attributes, change parent-child relationships, and delete SKUs — all in one template round trip. Also covers the end-to-end sourcing flow: a supplier link (e.g. 1688) → extract product data → local GPU-free OCR of detail images → generate title / bullet points / description → bilingual review with the user → propose the parent-child structure → fill the template → upload → read the processing report. Load this BEFORE any browser-use action on sellercentral.amazon.<tld>/listing/upload or when the task is to create / edit / delete a listing from a product link."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
---

# Amazon — Listing CRUD (flat-file upload)

> **PREREQUISITE:** read `../amazon-shared/SKILL.md` for login, Ziniao
> auto-fill / OTP, marketplace TLDs, hamburger navigation, and the
> capture rule (live data → `/tmp/<task>/`, never `knowledge/`).

Amazon's **Add Products via Upload** takes a category **flat-file
template** — a macro-enabled `.xlsm` whose `Template` sheet is a wide
table (one column per attribute). One upload creates or edits a whole
**variation family** (a Parent plus N colour/size Children) at once.
This is the batch equivalent of the per-SKU web wizard, and the default
for anything touching more than one variant.

Two references, load what the task needs:

- **`references/template-round-trip.md`** — the download → inspect →
  fill → upload → read-feedback loop, the operation column
  (create/update/partialupdate/delete), and the parent-child cluster.
  Load for **any** listing CRUD.
- **`references/1688-sourcing.md`** — turning a supplier link into a
  filled template: page extraction, local no-GPU OCR of detail images,
  AI-generated copy, the **bilingual review** step, and image handling.
  Load when the task starts from a **product link**.

## The two scripts

```bash
S=<skills>/amazon-listing/scripts
PY=<project-venv>/bin/python3     # needs openpyxl + rapidocr-onnxruntime
```

- **`listing_bulk.py`** — deterministic template writer. It keys every
  field by its **field API name** (the row that contains `item_sku`),
  which is identical in every console language, so it is locale-robust
  the same way `amazon-ads/ads_bulk.py` is.
  - `inspect TEMPLATE.xlsm [--field NAME]` — dump the field set, which
    fields are Required, the accepted enum tokens, and the variation
    cluster. **Run this first on every fresh template** — the column
    set and valid values differ per product type.
  - `fill TEMPLATE.xlsm --spec SPEC.json --out OUT.xlsm` — write
    parent/child rows, set the operation column per row, validate enums
    and required fields against the template's own metadata sheets, and
    preserve the workbook (macros, signature row) verbatim.
  - `parse-feedback REPORT` — summarise Amazon's processing report into
    per-SKU errors / warnings.
- **`ocr_1688.py`** — local, GPU-free OCR (rapidocr-onnxruntime) of the
  supplier's detail images, where the spec table / size chart live.

## Operation rules (the in-sheet `update_delete` column)

The operation is **chosen per row in the sheet**, not inferred:

| `operation` in spec | `update_delete` cell | Use when |
|---|---|---|
| `create` (default) | *blank* | new SKU. **No ASIN** → this is the default. |
| `update` | `Update` | full re-submit of an existing SKU's attributes. |
| `partialupdate` | `partialupdate` | change only the fields present; leave others as-is. |
| `delete` | `delete` | remove the SKU. Needs only `sku` + `operation`. |

Rule of thumb the user gave: **no ASIN filled → create; ASIN filled →
update** — but the sheet's operation column is authoritative, so always
set `operation` explicitly. For update/partialupdate by ASIN, put the
ASIN in `external_product_id` with `external_product_id_type: asin`.

## End-to-end flow (product link → live listing)

1. **Extract** the product from the supplier link (see
   `1688-sourcing.md`): page data + OCR of detail images.
2. **Generate** an Amazon title, 5 bullet points, and a long
   description from the extracted data.
3. **Bilingual review** — present the generated copy to the user in
   **both the user's language and the target marketplace language**,
   plus the **proposed parent-child structure** (which variation theme,
   which children). Wait for the user's confirmation / edits. This is
   the one genuinely interactive step; do not skip it.
4. **Download** the category template for the product type (into
   `~/.vibe-seller/downloads/<slug>/`), `inspect` it.
5. **Fill** a spec (parent + children) and produce the `.xlsm`.
6. **Upload** it and **read the processing report**; fix row-level
   errors from the report and re-upload. Common first-timers: an
   invalid `recommended_browse_nodes` (pick from the template's valid
   values) and a missing `external_product_id` (own-brand items need a
   real barcode or a **GTIN exemption**).

## Sourcing login

The supplier site (1688) needs a login for some bulk specs, but the
core product data is reachable without it. When a login **is** needed,
the login is a QR scan — **ask the user to scan it** (do not attempt to
authenticate on their behalf). Say which QR and wait.
