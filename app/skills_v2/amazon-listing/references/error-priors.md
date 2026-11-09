# Processing-report priors

Fixes the upload → report → fix loop keeps surfacing across categories.
They speed up diagnosis; they don't replace reading the actual report.

Contents:
- Upload mechanics
- Required fields and enums
- Images
- 8560: Amazon won't mint the ASIN
- Offer, stock and price are per-marketplace
- Reading the report

## Upload mechanics

- **Upload the tab-delimited `.txt`, not the `.xlsm`.** `fill` writes the
  `.txt` next to the `.xlsm`. An openpyxl-saved `.xlsm` triggers **90502
  FATAL** ("worksheet template type not supported for Excel upload").
- **`fill --out` into `~/.vibe-seller/downloads/<slug>/`.** The browser
  must read the file to attach it and cannot read `/tmp` (see the
  browser-harness skill, "Uploading a file").
- **Submit is two clicks.** On the unified upload page the first
  **Submit products** only fires `introspect-feed` (file-type detection →
  "Automatically detected" banner); the second posts the feed (the URL
  gains `reference_id=`). The red "Sorry! There's a network error" toast
  can appear even when introspect returned 200; it is not a reason to
  re-upload.
- **"1/N — parent created, children failed" is a content rejection.** The
  file reached validation. `parse-feedback` the report for the per-child
  reason, fix those fields and re-upload the children. A brand-new family
  often needs a second pass.
- **The generator's store checkboxes:** the target must be ticked (the
  template is region-stamped by it); other ticked stores are harmless,
  because `fill` routes the offer to the target's block. Leave them alone —
  `kat-checkbox` ignores JS clicks, and unticking a region parent clears
  all its children. `bh_download_template.py` ticks and reads back the
  target for you.

## Required fields and enums

- **Children are not minimal.** Each child needs the full required set its
  category asks for (e.g. `item_name`, `target_gender`,
  `age_range_description`, compound-attribute sub-fields), plus its
  differentiator and offer — not just `parent_sku` + colour.
- **"Required" is a guide.** For each required-field error, supply a
  sensible value from the template's valid set (material, weave, size
  type, package dimension/weight units); `list_price` = your price,
  `model_name` = the SKU or title. For a new ASIN with no GTIN, set the
  product-id type to `GTIN Exempt` (unified) or leave the id blank and set
  brand (legacy) if the brand is exempt. Don't stall a family on one
  attribute you can't provide.
- **"Conditionally Required" fields** are not flagged locally, and Amazon
  rejects them one feed at a time (30–60 minutes per round). That's why the
  accepted-spec library matters; `fill` names every field the accepted
  spec set that yours leaves empty.
- **Enum case is exact** (`UAE/KSA`, not `uae/ksa`). `fill` canonicalises
  to the template's casing when the field has a valid set.
- **Off-list is advice, not law.** Amazon rejected an off-list fulfilment
  code and a boolean written `False` (90244) yet accepted off-list `style`
  and `special_size_type`. Treat `inspect --field NAME` as advice and the
  accepted-spec library as the rule; `--allow-unlisted-enum FIELD`
  silences one field you have checked.
- **Compound attributes come as a set** — e.g. Apparel Size needs
  `apparel_size_class` + `apparel_size_system` + `apparel_body_type` +
  `apparel_height_type`; a partial set errors (99001/99022).
- **Set `update_delete` on every row, children included.** A blank child
  operation is a common cause of a child failing to join its family.

## Images

Images are not uploaded from here; the seller adds them. A **18320**
("main image is missing") is expected and does not block creation. Don't
hotlink a supplier CDN URL into `main_image_url` — Amazon can't fetch a
referer-protected 1688/alibaba URL. 18320 is the only accepted deferral;
see `verification.md` for how to report an image-deferred family.

## 8560: Amazon won't mint the ASIN

"doesn't match any ASINs … include standard_product_id" on a buyable
child. Two cases, decided by whether that child's ASIN already exists:

- **ASIN exists** (a re-submit, or an earlier create left a catalog ASIN —
  removing a SKU does not remove the catalog ASIN): match it. Keep
  `operation: create` with the row's `asin` pinned (`external_product_id`
  = the ASIN, `external_product_id_type: asin`). This also resolves a
  variation child that won't join its family.
- **Genuinely new, GTIN-exempt brand**: the exemption alone isn't enough.
  The report also warns which key defining attributes are missing (e.g.
  `material_type`, `pattern_name`); fill those from the template's valid
  values so the ASIN can be minted.

If Amazon still 8560s after a correct match, the marketplaces sit on
separate catalogs; report that a new ASIN is unavoidable rather than
silently forking the reviews.

## Offer, stock and price are per-marketplace

- A multi-marketplace template has one
  `purchasable_offer[marketplace_id=<MKT>]` block per marketplace and
  marks the home marketplace's block Required, so hand-picking a column
  can put the price on the wrong marketplace: an ASIN with no live offer
  ("Missing offer") while the feed says success. Give the spec a
  top-level `"marketplace": "<CC>"` and a bare `our_price` and bare
  `quantity` on each child; `fill` routes both to that block.
- Stock is not bracketed like the price: each `fulfillment_availability#N`
  group is tied by position to one marketplace's offer block. Use the bare
  `quantity` and let `fill` pick the right group (it also normalises a
  wrong-index `fulfillment_availability#k.*`). Stock on the wrong group is
  an offer with no stock.
- A fulfilment group needs a channel code with its quantity, or Amazon
  rejects the offer ("does not have enough values"). The code is
  marketplace-specific: use a value from the target template's valid set;
  another marketplace's value is "Invalid enumerated value … (<mkt>)".
- `DEFAULT` + a quantity is an FBM offer. For an FBA seller, set
  `quantity: 0`; `fill` refuses a merchant quantity above 0 unless the spec
  declares `"fulfillment": "FBM"`. See `fulfillment-channel.md`.
- A multi-marketplace account's template bundles every marketplace's offer
  columns even when you select one. Fill only the intended marketplace's
  block and leave the others blank.
- Verify price and stock in that marketplace's Pricing view; quantity can
  apply while price shows `--` if a different marketplace's column was set.

## Reading the report

`parse-feedback` reads the summary tables and the per-cell comments (批注)
on the report's `Template` tab and prints `sku=… field=… : MESSAGE`. Fix
exactly the field each error names, with a value from the template's own
valid set; don't theorise a root cause. If a WARNING names a key defining
attribute (material, pattern), fix it too — that often unblocks new-ASIN
creation.

Whether a row landed is read from its own `::submission_status`: "applied
without any errors" and "applied, but contain other error(s)" are both
live; only "not applied" is a failure, and `parse-feedback` flags those by
SKU. The summary's "SKUs successful" (and the status page's `N/M`) counts
only clean rows, so a family whose children were all applied with a
warning or missing image shows 1/4 — that is not "the rest failed".

The status page lists many past batches: use the report for this upload's
batch. `bh_fetch_report.py` returns a copy tagged `…__batch<id>`, and
`parse-feedback --batch-id` refuses a report tagged for another batch or
downloaded before the batch was uploaded.
