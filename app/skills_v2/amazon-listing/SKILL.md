---
name: amazon-listing
description: "Amazon listing CRUD via the category flat-file (Add Products via Upload). Create a variation family (parent + colour/size children), update attributes, change parent-child relationships, and delete SKUs — all in one template round trip. Also covers the end-to-end sourcing flow: a supplier link (e.g. 1688) → extract product data → local GPU-free OCR of detail images → generate title / bullet points / description → bilingual review with the user → propose the parent-child structure → fill the template → upload → read the processing report. Load this BEFORE any browser-use action on sellercentral.amazon.<tld>/listing/upload or when the task is to create / edit / delete a listing from a product link."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
review:
  criteria: |
    - The listing is live on the TARGET marketplace the user asked for
      (the exact country / `sellercentral.amazon.<tld>`), confirmed on
      THAT marketplace's own Manage Inventory — NOT merely "batch
      submitted", and NOT live only on a different marketplace. Amazon
      groups marketplaces two ways and you must not conflate them: a
      UNIFIED regional account whose sibling marketplaces share ONE
      catalog + an ACCOUNT-LEVEL bulk feed (the same batch id then shows
      on every sibling's listing/status page, so a batch row on one is NOT
      proof the SKU is live on another, and the offer can land on the
      account's home marketplace only); versus a SEPARATE account (a
      different region, or a legacy single-marketplace login) that is a
      different catalog entirely, created and confirmed independently,
      often with its own ASIN. Either way: if the TARGET marketplace's own
      inventory does not show the SKU live, it is a GAP even when the
      upload "succeeded" — a listing on the wrong marketplace, or only a
      batch id, is not done.
    - Every attempted SKU is ACTUALLY LIVE, not just "uploaded": on
      Manage Inventory the parent shows Variations(N) and each child has
      a real ASIN (not "-"), with title / bullets / images matching the
      request. The LATEST processing report for EVERY batch parsed
      (`parse-feedback`) to zero errors of ANY severity EXCEPT a
      missing-image 18320. This includes non-fatal "SUCCESS (OTHER) /
      Action required" errors (e.g. 100476 Item Highlight): the SKU is in
      inventory yet the error is unresolved -- that is NOT done. Presence
      in inventory alone never satisfies this.
    - For a delete, the SKU no longer appears in Manage Inventory.
    - No partial family (parent live but a child missing) is called done.
  evidence:
    - "*REPORT*.xlsm"
    - "*.xlsm"
    - "LISTING_*.md"
  verify_by: |
    Open Manage Inventory ON THE TARGET MARKETPLACE
    (`sellercentral.amazon.<target-tld>/skucentral?mSku=<sku>`, no
    &condition=New) for each attempted SKU and confirm it exists LIVE on
    that marketplace with the intended content and a real ASIN, and that
    its offer/price/stock show on that marketplace's Pricing view. Do NOT
    accept a batch-status row on some marketplace's listing/status page as
    proof — the batch id is account-level and appears on every
    marketplace. If the target marketplace's inventory is empty but
    another marketplace's shows the SKU, the offer landed on the wrong
    marketplace: that is a GAP. Download the LATEST processing report for
    EVERY batch you uploaded and `parse-feedback` it -- confirm zero
    errors except 18320. Do NOT accept a batch shown "Action required /
    SUCCESS (OTHER)" (e.g. 100476) as done. For a delete, confirm the SKU
    is gone.
---

# Amazon — Listing CRUD (flat-file upload)

> **PREREQUISITE:** read `../amazon-shared/SKILL.md` for the Ziniao
> login challenge-loop (password / OTP / hosted-passkey), marketplace
> TLDs, version-aware navigation (New Seller Central vs classic;
> navigate by direct URL), and the capture rule (live data →
> `/tmp/<task>/`, never `knowledge/`).

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

> **Before you finish — verify it's LIVE, not just uploaded.** A 0-error
> processing report is necessary but NOT sufficient; the listing is only
> done when Manage Inventory shows it live with a real ASIN (per the
> `review:` block above). Run the DoD review loop
> (`../amazon-shared/references/dod-review-loop.md`) with this skill's
> `review.criteria` / `review.verify_by` and converge to `Status: ok`
> before `set_task_result`.

## Work it like a human: upload → read the report → fix → repeat

The template, its required fields, valid values, and even the upload
mechanics **change per product type and over time**. Do not follow a
fixed recipe from memory. Run the loop a human runs:

1. **Download a FRESH template** for the exact product type — Amazon's
   own error messages say "download the latest template". Never reuse a
   stale one.
2. **`inspect` it.** The field set / required fields / valid values for
   THIS category are the ground truth, not this doc.
3. **Fill, upload, then download the processing report** and run
   `parse-feedback REPORT.xlsm`. It reads the summary tables **and the
   per-cell comments (批注) on the report's `Template` tab** — where
   Amazon writes the precise, field-level verdict per SKU — and prints
   `sku=… field=… : MESSAGE`.
4. **For each ERROR line, fix exactly the field it names** — set it to a
   value from the template's own valid set (`inspect --field NAME`). Do
   not reinterpret or theorise a root cause; act on the report's words.
   (If a WARNING names a key defining attribute like material/pattern,
   fix it too — that's often what unblocks new-ASIN creation.)
5. **Re-upload and repeat until only expected noise remains** (see the
   main-image rule below).
6. **Final verify — the two sources of truth, not the feed count:** the
   **downloaded report** shows 0 blocking errors, AND the **Manage
   Inventory page** shows the family (parent's "Variations (N)", each
   child a real ASIN — not `-` — with title / description / bullets).

The fixes listed below are **examples this loop surfaced on real
templates** — priors that speed up diagnosis, not a checklist that
replaces reading the actual report.

### Verification: trust inventory, not the feed count

The report's "records processed / 0 errors" means the **feed was
accepted**, not that a live listing exists. A record *with* errors can
still create an incomplete stub; a clean feed can leave a suppressed
listing. **Always confirm on Manage Inventory** (or
`skucentral?mSku=<sku>` **without** `&condition=New` — that param
false-negates incomplete listings). Confirm the SKU has an ASIN, and for
a family that the parent shows **"Variations (N)"**.

> **"Missing Information / ASIN -" is usually NOT a failure — don't
> thrash.** Two benign causes, and re-uploading fixes neither:
> 1. **ASINs mint asynchronously.** A just-submitted family can show
>    `ASIN -` / a "Complete drafts → Submitted: Provide missing
>    information" entry for **10–30 minutes** while Amazon mints the
>    ASINs. Re-check Manage Inventory later — the parent flips to
>    `Variations (N)` with real child ASINs on its own. Do NOT re-upload
>    (that just spawns duplicate batches).
> 2. **Only the main image is missing.** We intentionally don't upload
>    images, so a no-image product parks in "provide missing information"
>    for the image alone. **That is an acceptable DONE state** — the
>    seller adds the image later. The listing is **finished** once Manage
>    Inventory shows it with the variation relationship (`Variations (N)`),
>    real child ASINs, and correct **title / price / bullets**; a blank
>    image does not block "done". Only treat it as unfinished if the
>    processing report names a **non-image** blocking error.

> **Do NOT re-upload just because Check Upload Status shows "N/A".** After
> a submit, the batch's "SKUs successful / N/A" column stays `N/A` for
> minutes (and CREATE/DELETE feeds can sit at N/A a long time) — that is
> **normal, not a failure**, and the widget's shadow-root text may even
> read "File not uploaded" on a submit that *did* go through. Re-uploading
> on N/A just creates duplicate batches and wastes the run. Once you have
> a batch reference_id, the upload was accepted: **go straight to Manage
> Inventory** (search your SKU prefix) to verify the family is live —
> that is the source of truth, not the feed status. Only re-upload if the
> **downloaded processing report** names a real per-SKU error to fix.

### Priors that recur across categories

- **Upload a tab-delimited `.txt`, not the `.xlsm`.** `fill` writes the
  `.txt` next to the `.xlsm` for you — upload that. An openpyxl-saved
  `.xlsm` triggers a **90502 FATAL** ("worksheet template type not
  supported for Excel upload").
- **`fill --out` into the store downloads dir**
  (`~/.vibe-seller/downloads/<slug>/`), not `/tmp` — the browser must read
  the file to attach it (see `browser-harness` § "Uploading a file").
- **Submit is TWO clicks; the "network error" banner is a red herring.**
  On the unified upload page the 1st **Submit products** only fires
  `introspect-feed` (file-type detection → "Automatically detected"
  banner); a 2nd click actually posts the feed (URL gains
  `reference_id=`). The red "Sorry! There's a network error" toast shows
  even when introspect returned 200 — do NOT re-upload on it. See
  `references/template-round-trip.md` § 4.
- **"1/N — parent created, children failed" is a CONTENT rejection, not a
  browser bug.** The file uploaded fine (it reached validation); download
  the Processing Summary and `parse-feedback` it for the per-child reason,
  then fix + re-upload the children. Never thrash on the upload widget.
- **Children are NOT minimal.** Each child needs the full required set
  its category asks for (e.g. `item_name`, `target_gender`,
  `age_range_description`, and any compound-attribute sub-fields), plus
  its differentiator + offer — not just `parent_sku` + colour.
- **"Required" is a guide, not an absolute — fill what you can, defer what
  you can't.** For each required-field error the report names, supply a
  sensible value: pick from the template's valid set (material, weave,
  size type, package dimension/weight units), set `list_price` = your
  price, `model_name` = the SKU/title. For a **new ASIN** with no GTIN, set
  the product-id **type** to `GTIN Exempt` (unified) / leave the id blank
  + set brand (legacy) if the brand is exempt. A few are genuinely
  deferrable — a real **main image** you don't have (18320) is added later
  and does NOT block creation. Don't stall the whole family on one
  attribute you can't provide; create with what you have and let the
  seller finish image/GTIN afterwards.
- **Enum case is exact** (`UAE/KSA`, not `uae/ksa`). `fill` canonicalises
  a value to the template's own casing when the field has a valid set.
- **Compound attributes come as a set** — e.g. Apparel Size needs
  `apparel_size_class` + `apparel_size_system` + `apparel_body_type` +
  `apparel_height_type` together; a partial set errors (99001/99022).
- **Don't fight the marketplace checkboxes in the generator** — just make
  sure your **target** marketplace is ticked and Generate; do NOT try to
  uncheck the others. A bundled multi-marketplace template is fine (`fill`
  routes offer + quantity to your target's block); the store `kat-checkbox`
  toggles are unreliable and unchecking buys nothing but a stuck run.
- **Main image is not required by default** — we do **not** upload
  images from here (the seller adds them separately). So a `18320`
  ("main image is missing") error is *expected noise*, not a blocker;
  don't chase it, and don't hotlink a supplier CDN URL into
  `main_image_url` (Amazon can't fetch a referer-protected 1688/alibaba
  URL anyway). "Done" = every error resolved **except** the image one —
  and that means **`parse-feedback` the report, not eyeball inventory**. A
  record can post as **SUCCESS (OTHER)** ("Action required", 0 successful):
  it *appears* in inventory but carries an unresolved, fixable error —
  e.g. **100476** ("Provide an Item Name ≤75 chars to use Item
  Highlights") when `title_differentiation` (Item Highlight) was filled on
  a long-title item. That is **NOT done**: fix the exact field the report
  names (Item Highlight is optional — clear it; the colour belongs in
  `color_name`) and re-upload. Only 18320 is a legit deferral.
- **When the image is the only remaining error, report it as
  image-deferred, not "live".** A SUCCESS (OTHER) whose sole error is the
  missing main image ("submit a compliant image to lift the suppression")
  means the SKU is created + priced + stocked but **search-suppressed** —
  not buyable or discoverable until the seller adds an image. That is the
  accepted deferred done-state; report it as such (e.g. "N children
  created, linked, priced, stocked — suppressed pending main image, seller
  adds it to go live") rather than "live" or "done". The Manage Inventory
  row reads "Search suppressed" / "No image available" and the upload feed
  reads 0/N successful — expected for this state, not a failure to
  re-upload over.
- **A buyable child that `8560`s ("doesn't match any ASINs … include
  standard_product_id")** — Amazon is refusing to *mint a new ASIN* for
  it. Two cases, decided by whether that child's ASIN already exists:
  - **ASIN already exists** (you're re-submitting, or a prior create left
    a catalog ASIN — note a `delete` removes your SKU/offer but **not**
    the catalog ASIN): don't try to create — **match** it. Set
    `operation: update`, `external_product_id` = the existing ASIN,
    `external_product_id_type: asin`. This is the reliable fix and what
    resolves a variation child that won't join its family.
  - **Genuinely new ASIN, GTIN-exempt brand** (leave `external_product_id`
    blank): the exemption alone is not enough — the report also warns
    which **key defining attributes are missing** (e.g. `material_type`,
    `pattern_name`); fill exactly those from the template's valid values
    so the ASIN can be minted.
  Either way, set `update_delete` on **every** row including children —
  never leave a child's operation blank.
- **Offer/price is per-marketplace — set `our_price` + a top-level
  `marketplace`, don't hand-pick the column.** A multi-marketplace
  template has one `purchasable_offer[marketplace_id=<MKT>]` block per
  marketplace and marks the account's *home* marketplace's block Required
  — so hand-picking a column silently puts the price in the wrong
  marketplace, creating an ASIN with **no live offer** ("Missing offer",
  never live) even though the feed says success. Instead give the spec a
  top-level `"marketplace": "<CC>"` (the country you're listing on) and a
  bare `"our_price"` **and bare `"quantity"`** on each child; `fill` routes
  BOTH to that marketplace's block. **Stock is per-marketplace too, and it
  is NOT bracketed like the price** — each `fulfillment_availability#N`
  group is tied by *position* to one marketplace's offer block, so `#1` is
  a *different* marketplace than you may think. Never hand-pick a
  `fulfillment_availability#N.quantity` column; use the bare `quantity` and
  let `fill` pick the group adjacent to the target offer (it also
  normalises a wrong-index `fulfillment_availability#k.*` to the right
  one). Putting stock on the wrong group = an offer with no stock = never
  live. **Verify it in THAT marketplace's Pricing view** — the feed "N/N
  successful" count does not reflect price, and quantity can apply while
  price shows `--` if you set a different marketplace's column.
- **A multi-marketplace account's template bundles every marketplace's
  offer columns** (e.g. a Europe account yields both SA + AE columns even
  when you select one) — a truly single-country template may not be
  downloadable there. "Clean single-country" then means: fill only the
  intended marketplace's offer block, leave the others blank.

## Relisting the same product on another marketplace (share the ASIN)

When a product already lives on one marketplace (say SA) and the user
wants it "the same" on another (say AE), the default intent is the
**same ASIN on both**. Amazon pools ratings/reviews by ASIN, so minting
a *new* ASIN forks the reviews and restarts the new marketplace at zero
stars — for the same physical product that is almost never what's
wanted. Only create a new ASIN when the user explicitly asks for a
separate listing, or when the account's marketplaces are on genuinely
separate catalogs (see below).

Make the match **proactively** — don't submit a blind create and wait
for an `8560` to fix reactively:

1. **Get the source ASINs.** Map every SKU (parent + each child) to the
   ASIN it already has on the source marketplace — the All-Listings
   report is account-level (byte-identical across a unified account's
   marketplace subdomains), or read them off Manage Inventory. Reuse the
   **same SKUs** on the target marketplace; same SKU keeps it idempotent.
2. **Switch to the target marketplace and download ITS template** — use
   Amazon's own marketplace switcher, don't hand-edit URLs on a gated
   store, and `inspect` the fresh template. Offer/stock columns are
   per-marketplace (see the offer prior above).
3. **Pin the ASIN on every row** so Amazon *matches* instead of minting:
   `external_product_id` = that row's existing ASIN (e.g. `B0EXAMPLE1`),
   `external_product_id_type: asin`. The catalog content already exists
   under the ASIN — you are only adding this marketplace's **offer**, so
   set it via the top-level `"marketplace": "<CC>"` + bare `our_price` +
   `quantity` + `fulfillment_channel_code` (offer prior), not by
   re-describing the product.
   - **Use `operation: partialupdate` to add the offer to a matched
     ASIN.** The catalog exists under the ASIN, so you merge in only this
     marketplace's offer. `update` (Create or Replace) instead demands
     re-supplying EVERY required catalog field (item_name, description,
     bullet_point, fabric_type, country_of_origin, …) and rejects the row
     for any that's missing — i.e. re-describing the product, which the
     ASIN match exists to avoid. Reserve `update` for when you truly mean
     to (re)write the full catalog content; use `create` only for a
     genuinely new ASIN.
   - Supply the **complete offer** — `our_price` + `quantity` +
     `fulfillment_channel_code` — on each child. A fulfillment group needs
     a channel code together with its quantity, or Amazon rejects the
     offer ("does not have enough values"). The code is marketplace-
     specific (read the target template's valid values); `fill` routes a
     bare `fulfillment_channel_code` to the target group and warns if a
     group has a quantity but no code.
4. **Verify** the target ASINs equal the source ones (same ASIN ⇒ shared
   reviews) and that the offer is live in the TARGET marketplace's
   Pricing view — the feed's "N/N successful" does not prove either.

**Unified vs separate catalogs.** A unified pan-regional account (e.g.
MENA: SA / AE / EG) shares one catalog, so a matched ASIN carries its
reviews across all of them — reusing the same SKU often auto-shares it,
but pinning `external_product_id` makes it deterministic instead of
hoping. If the marketplaces sit on separate catalogs the ASIN may not be
shareable at all: when Amazon still `8560`s *after* a correct match,
that region cannot share the catalog — report that (a new ASIN is
unavoidable), don't silently fork the reviews.

## The two scripts

```bash
S=<skills>/amazon-listing/scripts
PY=<project-venv>/bin/python3     # needs openpyxl + rapidocr-onnxruntime
```

- **`listing_bulk.py`** — deterministic template writer. It keys every
  field by its **field API name** (the row carrying the SKU column),
  which is identical in every console language, so it is locale-robust
  the same way `amazon-ads/ads_bulk.py` is. It **auto-detects both
  template dialects** — legacy `fptcustom` (`item_sku`, `update_delete`,
  header row 3) and the current unified NGS "Beta Product Spreadsheet"
  (`contribution_sku#1.value`, `::record_action`, marketplace-scoped
  parentage/offer, header row 5) — and resolves the friendly spec keys to
  each dialect's columns, so the SAME spec drives either. **Always drive
  the upload file through `fill` — never hand-roll it**: a hand-rolled
  file skips `fill`'s guard that clears the unified template's prefilled
  example/instruction rows, and uploading those as SKUs creates only the
  parent — the children fail. See
  `references/template-round-trip.md` § 0.
  - `inspect TEMPLATE.xlsm [--field NAME]` — dump the dialect, the field
    set, which fields are Required, the accepted enum tokens, and the
    resolved friendly roles (which column each of sku / operation /
    parentage / parent_sku / variation_theme / brand / offer maps to for
    THIS template). **Run this first on every fresh template** — the
    column set, dialect, and valid values differ per product type.
  - `fill TEMPLATE.xlsm --spec SPEC.json --out OUT.xlsm` — write
    parent/child rows, set the operation column per row, validate enums
    and required fields against the template's own metadata sheets, and
    preserve the workbook (macros, signature row) verbatim.
  - `parse-feedback REPORT` — extract Amazon's verdict: the summary
    tables **and the per-cell comments (批注) on the report's `Template`
    tab**, emitted as `sku=… field=… : MESSAGE`. The 批注 are the
    precise, field-level fixes — the engine of the self-correct loop.
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

Rule of thumb: **no ASIN yet → create; the ASIN already exists → update
and match it** (put the ASIN in `external_product_id` with
`external_product_id_type: asin`). The operation column is authoritative,
so set it **explicitly on every row, children included** — a blank child
operation is a common cause of a child failing to join its family.

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
6. **Upload the `.txt` and run the self-correct loop** (see "Work it
   like a human" above): `parse-feedback` the report, fix exactly the
   field each 批注 names, re-upload, repeat, and **verify on Manage
   Inventory + the Pricing view** — not the feed count. Stop when the
   only remaining error is the image (`18320`).

## Sourcing login

The supplier site (1688) needs a login for some bulk specs, but the
core product data is reachable without it. When a login **is** needed,
the login is a QR scan — **ask the user to scan it** (do not attempt to
authenticate on their behalf). Say which QR and wait.
