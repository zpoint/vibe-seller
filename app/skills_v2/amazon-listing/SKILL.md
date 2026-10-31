---
name: amazon-listing
description: "Amazon listing CRUD via the category flat-file (Add Products via Upload). Create a variation family (parent + colour/size children) and edit existing listings with partial updates (each row pins its ASIN) — all in one template round trip. Full update runs only when the user asked for one by name (`--ops update`); delete is DISABLED (refused by the scripts). Also covers the end-to-end sourcing flow: a supplier link (e.g. 1688) or uploaded detail images → read every detail tile into a fact sheet → name the true category → check competitors on the storefront → generate title / bullet points / description → review with the user → propose the parent-child structure → fill the template → upload → read the processing report. Load this BEFORE any browser-use action on sellercentral.amazon.<tld>/listing/upload or when the task is to create or edit a listing."
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
    - No partial family (parent live but a child missing) is called done.
    - A partialupdate of copy (title / bullets / description / keywords)
      is proved by two things together: the batch's processing report
      reads "Your changes were applied without any errors" for every SKU
      (`parse-feedback`, 0 errors), and Manage Inventory
      (`bh_listing_status.py`) shows the NEW title on every SKU. Bullets,
      description and keywords have no reader here -- Manage Inventory
      shows titles only, and the per-SKU edit page is opened from a menu
      with no stable URL (`/listing/edit?asin=` is "page not found") -- so
      do not go hunting for them; the report's per-SKU "applied" line is
      the evidence for those fields.
  evidence:
    - "*REPORT*.xlsm"
    - "*.xlsm"
    - "LISTING_*.md"
  verify_by: |
    Read Manage Inventory ON THE TARGET MARKETPLACE with
    `SKUS=<every attempted SKU, comma-separated>
    SC_HOST=sellercentral.amazon.<target-tld> browser-use <
    .claude/skills/amazon-listing/scripts/bh_listing_status.py` (run from
    the task workspace) and confirm each SKU is in `rows` with
    a real ASIN (the one intended, for a pinned row) and nothing
    attempted is in `missing`; it refuses unless the page's `ue_mid` is
    that marketplace. `skucentral?mSku=` is an UNRELIABLE verification
    surface -- it has rendered an empty body in some sessions (one run
    spent 19 calls on it) though it renders normally in others
    (re-checked 2026-10-31: SKU/ASIN/FNSKU header + Sales/Economics/
    Pricing/Inventory tabs all present). If you open it, first confirm
    it actually rendered (a `SKU:` header line in the body text);
    an empty body is this page's known failure mode, not a missing SKU.
    Then confirm the offer/price/stock on that marketplace's Pricing
    view.
    FIRST, on every page you verify from, read WHICH marketplace the
    page is actually displaying: the header account/marketplace
    switcher label (store name + country/flag next to Settings) is the
    ONLY truth — the URL subdomain is NOT (a `.ae` URL renders the
    sibling marketplace's inventory when the session's switcher is
    still on it; both a live agent run and a human reviewer have been
    fooled by this). Record the switcher-shown marketplace in your
    evidence; if it differs from the target, switch via the picker and
    re-load before trusting anything on the page. Do NOT
    accept a batch-status row on some marketplace's listing/status page as
    proof — the batch id is account-level and appears on every
    marketplace. If the target marketplace's inventory is empty but
    another marketplace's shows the SKU, the offer landed on the wrong
    marketplace: that is a GAP. Download the LATEST processing report for
    EVERY batch you uploaded and `parse-feedback` it -- confirm zero
    errors except 18320. Do NOT accept a batch shown "Action required /
    SUCCESS (OTHER)" (e.g. 100476) as done.
---

# Amazon — Listing CRUD (flat-file upload)

> **PREREQUISITE:** read `../amazon-shared/SKILL.md` for the Ziniao
> login challenge-loop (password / OTP / hosted-passkey), marketplace
> TLDs, version-aware navigation (New Seller Central vs classic;
> navigate by direct URL), and the capture rule (live data →
> `/tmp/<task>/`, never `knowledge/`). That rule is for CAPTURES only —
> do not make `/tmp` your working directory. The upload gate, the
> reviewer gate and the accepted-spec library all read the TASK
> WORKSPACE; the helpers and `parse-feedback` now write there whatever
> your `$PWD` or `MARKER_DIR` is, but a review file you write yourself
> still has to go where the gate names it (an absolute path).

Amazon's **Add Products via Upload** takes a category **flat-file
template** — a macro-enabled `.xlsm` whose `Template` sheet is a wide
table (one column per attribute). One upload creates or edits a whole
**variation family** (a Parent plus N colour/size Children) at once.
This is the batch equivalent of the per-SKU web wizard, and the default
for anything touching more than one variant.

Four references, load what the task needs:

- **`references/listing-copy.md`** — how to write or rewrite title /
  bullets / description / search terms: read every detail-image tile
  into a fact sheet, name the real category, check the competitors on
  the storefront, write only what the facts back, review before upload.
  Load **before writing any copy** (new listing, relist, or refresh),
  unless the task hands you confirmed final copy.
- **`references/template-round-trip.md`** — the download → inspect →
  fill → upload → read-feedback loop, the operation column
  (create/update/partialupdate/delete), and the parent-child cluster.
  Load for **any** listing CRUD.
- **`references/1688-sourcing.md`** — turning a supplier link into a
  filled template: page extraction, local no-GPU OCR of detail images,
  AI-generated copy, the **bilingual review** step, and image handling.
  Load when the task starts from a **product link**.
- **`references/fulfillment-channel.md`** — FBM (`DEFAULT`) vs FBA
  channel codes, why an FBA product shows "International shipping" once
  FBA stock runs out, and how to zero the merchant quantity and verify it.
  Load when setting `quantity` / `fulfillment_channel_code` for an **FBA**
  seller, or when a listing ships as merchant-fulfilled unexpectedly.

> **Before you finish — verify it's LIVE, not just uploaded.** A 0-error
> processing report is necessary but NOT sufficient; the listing is only
> done when Manage Inventory shows it live with a real ASIN (per the
> `review:` block above). Run the DoD review loop
> (`../amazon-shared/references/dod-review-loop.md`) with this skill's
> `review.criteria` / `review.verify_by` and converge to `Status: ok`
> before `set_task_result`.

## Start from the last spec Amazon ACCEPTED — never from scratch

Before writing a spec, look in `store-data/<slug>/listing-specs/` for
`<product_type>__<CC>.json`. It is the spec behind the last batch of this
category that **Amazon accepted** on that marketplace — saved
automatically by `parse-feedback --batch-id` at the moment the verdict
came back clean. Copy its `spec`, change only what this listing changes
(SKUs, ASIN pins, copy, price), and keep every other field as it is.

This is not a nicety. The fields Amazon enforces on a create are flagged
**"Conditionally Required"** in the template (53 in one
template), so nothing warns about them locally — Amazon rejects them one
feed at a time, 30–60 minutes per round. Two days running, the first
create of a known product failed on exactly the fields the previous
run's accepted spec already carried (style, special size, outer
material, list price, package-dimension units), because that spec had
died with its task. `fill` now names every field the accepted spec set
that yours leaves empty; treat that warning as the rejection you have
not waited for yet. No entry for your marketplace? Another
marketplace's entry for the same product type is the next-best
reference — the attribute set is the category's, not the storefront's.

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

> **First, prove WHICH marketplace you are looking at — every other
> reading is worthless without it.** A seller-central page renders the
> marketplace the SESSION is on, whatever the URL subdomain says, so
> "the `.ae` inventory page shows the family" is not evidence that AE
> has it. Read the id, not a label: `js("return ue_mid")` returns the
> live marketplace id (`A17E79C6D8DWNP` = SA, `A2VIGQ35RCS4UG` = AE …,
> the full table is `marketplace_ids.py`), identical in every console
> language. Do this on EVERY verification read, and quote the id in the
> result — a family reported live on the wrong storefront is worse than
> an unverified one, because it closes the loop on a lie. Observed
> live: an AE-stamped upload landed in SA's upload history, and both
> marketplaces were then "verified" off pages that were never the
> marketplace claimed.

The report's "records processed / 0 errors" means the **feed was
accepted**, not that a live listing exists. A record *with* errors can
still create an incomplete stub; a clean feed can leave a suppressed
listing. **Always confirm on Manage Inventory, with
`bh_listing_status.py`** (step 4 of the helper block below): it returns
each SKU's ASIN off the page's own `div[data-sku]` rows, proves the
marketplace by `ue_mid`, and names the SKUs that are not there. Confirm
every SKU has the ASIN you meant, and for a family that the parent row
(the one with `offer_cell: false`) carries the family's parent ASIN.
`skucentral?mSku=` is an unreliable verification surface: it has
rendered an empty body in some sessions (see the review block above for
the render check).

> **The inventory page itself (re-verified 2026-10-31).** URL is still
> `/myinventory/inventory`; the header now reads **"Manage All
> Inventory"** (NGS naming) and, on an NGS session, the classic page
> body renders under the `ngstrim` trim. Tab row: **All listings (N) /
> Complete drafts (N) / Activate listings (N) / Enhance listings (N) /
> Review listing changes**; quick filters + Listing status / Fulfilled
> by dropdowns + "All filters"; top-right buttons **"Add a variation"**
> and **"Add a product"**. Row actions live in a per-row kebab
> (`button.indicator` inside a `kat-dropdown-button` at the row's far
> right — **outside** the `div[data-sku]` element, so row-scoped
> queries miss it). Parent (family) rows offer Edit listing / Manage
> images / Copy listing / Analyze for FBA / Delete listing / Advertise
> listing; child rows add Add another condition / Change to Fulfilled
> by Merchant / Send or replenish inventory / Create removal order /
> Print item labels / Close listing / Change shipping template.
> Expanding a family ("Variations (N)") opens a "Related to N
> variations" panel whose child rows carry an inline **price input**
> plus "View reference prices" and a **`+N marketplace` chip** on
> listings that exist on more than one marketplace. Automation caveat:
> in testing the kebab menu's items did not respond to synthetic
> clicks (the `kat-dropdown-button` host won the hit-test over its own
> popup); the flat-file helpers and `bh_listing_status.py` are the
> reliable paths — don't burn a run on the menu.

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
>
> **A slow queue says NOTHING about your file, so do not "test" theories
> against it.** Latency is not evidence: Amazon accepted the file the
> moment it gave you a reference_id, and a CREATE feed submitted right
> after a DELETE on the same SKUs is the slowest case there is — 30+
> minutes at N/A is ordinary. While a batch is pending the only legal
> moves are **wait** and **check Manage Inventory**. Do not rewrite the
> spec, and above all **do not remove an ASIN pin to see whether the pin
> was the problem** — an unpinned create mints a new ASIN, so that
> "test" destroys the very thing you were waiting to restore, and the
> stall told you nothing about the pin either way. Observed live: an
> agent 35 minutes into a pinned rebuild started proposing exactly that.
> If you genuinely need to know why, wait for the processing report and
> read what Amazon says; `fill` will refuse to drop the pin for you.

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
    the catalog ASIN): don't try to mint — **match** it. Keep
    `operation: create` with `external_product_id` = the existing ASIN,
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

**New ASINs AND the same children on both marketplaces is not a
contradiction — it is a sequence.** When the user wants Amazon to mint
fresh ASINs for a family that must match across two marketplaces,
create on ONE marketplace first with `mint_new_asin`, wait for its
report, read the ASINs it minted with `bh_listing_status.py`, and only
then create on the second marketplace pinned to exactly those. Never
mint on both (two different ASINs), and never upload the second before
the first's new ASINs are read: on a unified account a second mint for
the same SKU re-points it account-wide. Re-pinning to the family's
OLD ASINs is a different answer to a different request -- one run did
that when told "let Amazon mint new ones", because it saw the two asks
as incompatible. If the request is ambiguous about new-vs-existing
ASINs, ask.

Make the match **proactively** — don't submit a blind create and wait
for an `8560` to fix reactively:

1. **Get the source ASINs — one helper call per marketplace.** Map every
   SKU (parent + each child) to the ASIN it has on the SOURCE
   marketplace, and see what the TARGET already holds, before you plan a
   create:
   `SKUS=<parent>,<child-1>,... SC_HOST=sellercentral.amazon.<tld>
   browser-use < $S/bh_listing_status.py` (run it once with the source
   tld, once with the target's). Manage Inventory's search view collapses
   a family to its parent row, and probing it by hand — clicks, internal
   endpoints, public product pages — cost one run ten minutes before it
   fell back on ASINs remembered from an earlier task. A public product
   page proves an ASIN exists, never which SKU is on it. Reuse the
   **same SKUs** on the target marketplace; same SKU keeps it
   idempotent.
2. **Do the WHOLE target flow on the target marketplace's own subdomain —
   a create/partialupdate is marketplace-scoped.** Deleting is not
   available at all (see Operation rules): to change ONE storefront's ASIN
   for a SKU there is no route here — stop and ask the user. A flat-file upload applies to the
   marketplace of the `sellercentral.amazon.<tld>` you're on, regardless
   of the offer columns in the file; a `.sa` upload lands on SA even when
   the account context shows the target. So for AE, run template download +
   upload + Check Upload Status all on `sellercentral.amazon.ae/...` (EG →
   `.eg`, etc.); the subdomain selects the marketplace for a unified
   account. **Confirm the header's account/marketplace switcher shows
   the target** (marketplace name / currency) before uploading — and
   before EVERY verification read too: what a seller-central page
   DISPLAYS follows the session's switcher, not the URL subdomain (a
   `.ae` inventory URL happily renders SA's inventory when the switcher
   is on SA — verifying "AE has it" off such a page is how a listing
   ends up confirmed on the wrong marketplace). Read the switcher label
   back; never infer the marketplace from the URL. The machine-readable
   form of that check is the page's own `ue_mid` global (the live
   marketplace id, identical in every console language).
   `bh_upload_flatfile` refuses to submit unless **three** things name
   the same marketplace: the one you meant (from `SC_HOST`'s domain, or
   `SC_MARKETPLACE=<id|CC>`), the file's `primaryMarketplaceId` stamp,
   and the live `ue_mid`. **Two of them agreeing is not enough** — an
   SA-stamped file uploaded on an SA session by an agent that believed
   it was doing AE satisfies a file-vs-session check and still lists on
   the wrong storefront (observed live, twice).
   Verify your reads the same way: `js("return ue_mid")` beats squinting
   at a localised label. If the subdomain instead lands on an
   account-picker ("Select an account"), switch there first — fast path
   (current layout): click the target account's `<button>` (not the inert
   label), then **Select account**, and if a click no-ops or the layout
   changed, screenshot and find the real control rather than uploading on
   the wrong marketplace. Then download the target's template and
   `inspect` it. Offer/stock columns are per-marketplace (see the offer
   prior above).
   - **The template is region-stamped — generate it with the TARGET store
     ticked.** "Download Blank Template" → "Download Product Spreadsheet"
     ("List products not currently in Amazon's catalog") → search the
     product type (type into the search with **trusted** keystrokes, not a
     JS value set) → Select it → in "stores where you want to create
     offers", **tick the TARGET marketplace and untick the others** (the
     default is the account's HOME marketplace, so a template "downloaded
     from AE" is still stamped SA unless you fix this) → Generate. Reusing
     another marketplace's file fails upload with
     `UPLOAD_AND_DOWNLOAD_MARKETPLACES_DIFFERENT`, even on the right
     subdomain.
   - **Browse-node / category ids are marketplace-scoped — a
     dual-marketplace template carries the PRIMARY marketplace's
     classifications only.** Ticking extra stores adds offer columns
     for them, but `recommended_browse_nodes` values and the embedded
     browse data still belong to the template's PRIMARY (the store the
     generator treats as first). To set browse nodes on marketplace X,
     generate the template with X as primary; otherwise leave
     `recommended_browse_nodes` blank and let Amazon classify from the
     product type. `fill` hard-fails a cross-primary browse-node spec.
   - **Staging the file uses the file-chooser intercept, not the visible
     input.** The `kat-file-upload` widget's `input#kat-file-attachment`
     is a decoy — `setFileInputFiles` on it silently no-ops ("File not
     uploaded"). Use the intercept + trusted-click recipe in
     `browser-harness` § "Uploading a file" (Method 2). "Staged: False" is
     this bug, not a rejected template.
3. **Pin the ASIN on every row** so Amazon *matches* instead of minting:
   `external_product_id` = that row's existing ASIN (e.g. `B0EXAMPLE1`),
   `external_product_id_type: asin`. The catalog content already exists
   under the ASIN — you are only adding this marketplace's **offer**, so
   set it via the top-level `"marketplace": "<CC>"` + bare `our_price` +
   `quantity` + `fulfillment_channel_code` (offer prior), not by
   re-describing the product.
   - **Dropping the pin is DESTRUCTIVE, never a debugging simplification.**
     A seller SKU is account-scoped: an unpinned `create` for a SKU that
     already sells on the source marketplace makes Amazon mint a fresh
     ASIN and **re-point the SKU to it account-wide**, orphaning the
     original — its reviews, ratings and rank go with it, and Amazon
     never re-issues a retired ASIN. The FNSKU follows the SKU, so FBA
     stock looks untouched while the ASIN silently changed underneath
     it, and the feed report calls the whole thing a clean create.
     Observed live: an AE relist submitted plain `create` rows for the
     SKUs already live on SA, and all three SA children changed ASIN.
     If a pinned upload fails, fix what the report names — never
     "simplify" by removing the pin. `fill` now hard-fails an
     undeclared mint (see § Operation rules).
   - **Operation depends on whether the ASIN exists in the TARGET
     catalog — check that FIRST** (open `amazon.<tld>/dp/<ASIN>`, or read
     the first upload's report):
     - **Shared catalog** (the ASIN resolves on the target marketplace):
       pin it + `operation: partialupdate` and add only this marketplace's
       offer — don't re-describe the product.
     - **Not in the target catalog yet** (the target `/dp/<ASIN>` is
       "Page Not Found", or the upload reports *"the offer cannot be
       added because the product is not in the catalogue … listing a
       product from one marketplace to another"*): an offer-only
       partialupdate cannot work — but this is NOT a first-time
       creation either. **CREATE on the target with the FULL product
       data AND each row's existing ASIN pinned** (`asin: <existing>`
       on the row): the product already has an ASIN on the source
       marketplace, and pinning it lets the target link the SAME ASIN
       so ratings/reviews pool internationally instead of forking.
       Mint a NEW ASIN (GTIN-exempt, product id left blank) only when
       the product exists on no marketplace yet, the user explicitly
       wants a separate listing, or a pinned create irrecoverably
       conflicts (fix what the report names first). Afterwards verify
       each CHILD's ASIN on the target equals the source's — children
       are the buyable entities whose reviews pool; if the parent
       container minted a new ASIN despite matched children, say so in
       the result rather than calling the families identical.
   - Supply the **complete offer** — `our_price` + `quantity` +
     `fulfillment_channel_code` — on each child. A fulfillment group needs
     a channel code together with its quantity, or Amazon rejects the
     offer ("does not have enough values"). The code is marketplace-
     specific — use a value from the TARGET template's own valid set;
     another marketplace's value (e.g. `fulfilment by merchant (default)`)
     is rejected as "Invalid enumerated value … (<mkt>)". `fill` routes a
     bare `fulfillment_channel_code` to the target group and warns if a
     group has a quantity but no code.
   - **`DEFAULT` + a quantity is an FBM offer.** For an FBA seller it goes
     live, and ships internationally on the merchant template, whenever
     FBA stock hits 0. Converting the SKU to FBA does not zero it. So an
     FBA product gets `quantity: 0`, and `fill` **refuses** a merchant
     quantity above 0 unless the spec declares `"fulfillment": "FBM"`.
     See `references/fulfillment-channel.md`.
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

## Deterministic browser helpers (use these FIRST)

Three env-parameterized harness scripts mechanize the finicky browser
steps — run them through the store wrapper from the task workspace, read
the single ``RESULT {json}`` line.

If a helper misbehaves, first **re-run it with its env knobs** — a
transient timing miss is usually just a too-short wait, not a reason to
throw the helper away and hand-drive the whole flow:
- `bh_upload_flatfile.py`: `UPLOAD_LOAD_WAIT` (default 15s, page settle)
  and `UPLOAD_INTROSPECT_WAIT` (default 12s, type-detection) — bump both
  on a slow/heavy account before concluding the upload "won't work".

> **Seller Central renders in whatever language the SESSION is set to** —
> one account shows English, Chinese or Arabic on the same URL, and the
> subdomain has nothing to do with it. So **never gate a decision on the
> wording of a control.** The helpers don't: readiness is the Submit
> button flipping `disabled` → enabled, and the report download is found
> by a real anchor's href or an `icon`-style attribute (Amazon's own API
> tokens, identical in every language) before any wording is considered.
> Hand-drive the same way — prefer a state change, an attribute, or an
> href over a label; if you must read a label, use it as an identity to
> compare against itself, not as a word to match. And never read "the
> page doesn't say the English thing" as "the page is broken": a helper's
> `ok=false` names its own reason, and a banner left over from an earlier
> page is not that reason. This cost a full run once — the upload helper
> matched English on a Chinese console, reported "not detected" on a page
> a human submits in one click, and the agent hand-drove onto the wrong
> page and uploaded to the wrong marketplace.

Fall back to exploring by hand when a helper reports ok=false for a
STRUCTURAL reason it names (widget genuinely absent, region-stamp
mismatch); prefer fixing the input (regenerate the template, fix the
spec) over hand-clicking, and use whatever tools you have — including
vision/screenshots if your model supports them — to find the real
control:

```bash
S=.claude/skills/amazon-listing/scripts
DL=~/.vibe-seller/downloads/<slug>

# 1. Generate + download ONE marketplace's template (region-stamped by
#    the ticked store — this does the ticking for you AND verifies it
#    took: kat-checkbox ignores JS clicks, so the states are read back
#    and returned as "stores"; ok=false means the target never got
#    ticked — do NOT generate/fill from that state). Writes
#    UPLOAD_PENDING.json to MARKER_DIR: from here the task cannot
#    finish until an uploaded batch has a parse-feedback verdict (or
#    you delete the marker because no upload happened after all):
SC_HOST=sellercentral.amazon.<tld> PRODUCT_TYPE=<keyword> \
STORE_LABEL=Amazon.<tld> DOWNLOADS_DIR=$DL MARKER_DIR="$PWD" \
browser-use < $S/bh_download_template.py
# Then confirm the stamp before filling: `listing_bulk.py inspect`
# prints "marketplaces:" — your target MUST be listed, and `fill`
# requires --marketplace <CC> and hard-fails on a wrong-region
# template instead of listing on the wrong storefront.

# 2. Stage + submit the filled .txt (file-chooser intercept + Submit +
#    batch id). Writes UPLOAD_BATCH_<id>.json to MARKER_DIR — the task
#    CANNOT finish until that batch has a clean parse-feedback verdict.
#    If this reports ok=false and you complete the upload BY HAND, the
#    gate still applies (UPLOAD_PENDING arms it): find the batch id on
#    the status page and parse-feedback it like any other batch:
UPLOAD_FILE=$DL/out.txt SC_HOST=sellercentral.amazon.<tld> \
MARKER_DIR="$PWD" browser-use < $S/bh_upload_flatfile.py

# 3. Fetch THAT batch's processing report, then verdict it. Parse the
#    `…__batch<id>` copy bh_fetch_report hands back: a report names no
#    batch and same-named reports overwrite each other, so parse-feedback
#    refuses one tagged for another batch or downloaded before the batch
#    was uploaded -- a wrong verdict once put six errors on a clean batch:
SC_HOST=sellercentral.amazon.<tld> BATCH_ID=<id> DOWNLOADS_DIR=$DL \
browser-use < $S/bh_fetch_report.py
python3 $S/listing_bulk.py parse-feedback <report> --batch-id <id>
# ^ run FROM the task workspace root: the verdict JSON is written to
#   the current directory, which is where the completion gate reads it.

# 4. Verify what Manage Inventory now says each SKU IS, on THAT
#    marketplace: exact SKU -> ASIN (+ which SKUs are missing), read from
#    the page's own rows, refused unless ue_mid is the one you meant.
#    One call for the whole family; don't hand-scrape inventory pages:
SKUS=<parent>,<child-1>,<child-2> SC_HOST=sellercentral.amazon.<tld> \
browser-use < $S/bh_listing_status.py
```

The helpers encode the mechanics (decoy input, region stamp, two-click
submit, shadow-DOM rows); your judgment stays with the spec: which
operation per row, which fields, and how to fix what the report names.

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
    preserve the workbook (macros, signature row) verbatim. A value
    outside a field's valid-value list is a **warning**, and every such
    value is named at once. The list is not what Amazon enforces: it
    rejected an off-list fulfilment code and a boolean written `False`
    (90244), yet accepted an off-list `style` and `special_size_type`
    without even a warning. A gate that made the list fatal once forced
    an extra upload to "fix" values Amazon had just accepted — so read
    the TARGET template's list (`inspect --field NAME`) as advice, and
    the accepted-spec library as the rule. `--allow-unlisted-enum FIELD`
    silences one field you have checked.
  - `parse-feedback REPORT` — extract Amazon's verdict: the summary
    tables **and the per-cell comments (批注) on the report's `Template`
    tab**, emitted as `sku=… field=… : MESSAGE`. The 批注 are the
    precise, field-level fixes — the engine of the self-correct loop.
    Whether a row LANDED is read from its own `::submission_status`:
    "applied without any errors" and "applied, but contain other
    error(s)" are both live; only "not applied" is a failure, and
    `parse-feedback` flags those by SKU. Do **not** read the summary's
    "SKUs successful" (or the status page's `N/M`) as "the rest failed" —
    Amazon counts only clean rows there, so a batch whose children were
    all applied with a warning or a missing image shows 1/4. An earlier
    version of this check made exactly that mistake and sent a reviewer
    after a failure that did not exist. A report whose only error is the
    missing main image prints DONE — do not re-upload for it.
- **`ocr_1688.py`** — local, GPU-free OCR (rapidocr-onnxruntime) of the
  supplier's detail images, where the spec table / size chart live.

## Operation rules (the in-sheet `update_delete` column)

The operation is **chosen per row in the sheet**, not inferred:

| `operation` in spec | `update_delete` cell | Use when |
|---|---|---|
| `create` (default) | *blank* | new SKU. Needs either a pinned `asin` **or** an explicit `"mint_new_asin": true` — see below. |
| `partialupdate` | `partialupdate` | edit an EXISTING SKU: change only the fields present; leave others as-is. **Must pin `asin`.** |
| `update` | `Update` / `Create or Replace (Full Update)` | **Opt-in only**: the user asked for a full update by name. Clears every attribute the row leaves blank. **Must pin `asin`.** |
| `delete` | — | **DISABLED.** See below. |

**`update` is refused** by `fill` unless the run passes `--ops update`,
and by `bh_upload_flatfile.py` unless `ALLOW_FULL_UPDATE=1` (it reads the
upload file's operation column, so a hand-written file cannot get past
it either). Pass them **only when the user's request says "update" /
"full update" in so many words** — e.g. re-listing SKUs the user deleted
by hand in Seller Central, under their old SKU and old ASIN. Never choose
it yourself to "refresh" copy: a full update row must then carry the
whole listing (product type, brand, title, bullets, description, item
type, parentage, variation theme, size / colour, offer fields), because
whatever it leaves blank is cleared.

**`delete` is refused** by both, with no opt-in. A delete on a unified
account removed the SKU from every marketplace (an AE-only delete of
a family's children took them off SA too), and a deleted SKU can never be
an FBA offer again — the family had to be rebuilt under new SKUs. If a
task seems to need one, stop and tell the user; do not work around it.

**Every `partialupdate` / `update` row pins the ASIN it edits** (`"asin":
"B0..."` on the row; a parent row pins the parent ASIN) — `fill` refuses
one that does not. A copy-only partial update that carried no product id
came back clean, and within the hour Amazon had moved every variant SKU
of that family to NEW ASINs on one marketplace (same SKU, same FNSKU, old
ASINs gone from the storefront); the sibling marketplace kept its ASINs.
With no product id Amazon re-runs catalog matching on the new attributes.
Read each SKU's ASIN on THAT marketplace (Listings API, Manage Inventory or
the All-Listings report) before writing the spec — the two marketplaces of
a unified account can differ, parents especially.

**Editing existing listings (title / bullets / description / attributes):**
`operation: partialupdate` on the SKUs that already exist, carrying only
the fields being changed, and run `fill ... --ops partialupdate` so no row
of the spec can create a SKU or mint an ASIN. Never use a create row to
"refresh" copy on a live SKU. A complete edit spec — bare attribute names
are enough; `fill` resolves each to THIS marketplace's column (pass
`--marketplace`), and a list spreads over the field's `#1..#N` slots:

```json
{
  "product_type": "<the product type the listing has NOW>",
  "marketplace": "SA",
  "rows": [
    {
      "sku": "<existing SKU>",
      "operation": "partialupdate",
      "asin": "<that SKU's ASIN on THIS marketplace>",
      "fields": {
        "item_name": "<new title>",
        "bullet_point": ["<b1>", "<b2>", "<b3>", "<b4>", "<b5>"],
        "product_description": "<new description>",
        "generic_keyword": "<search terms>"
      }
    }
  ]
}
```

**`product_type` is the type each SKU has NOW**: read it from
`store-data/<slug>/listing-specs/sku-product-types.json` — Amazon may
have re-filed it after the create (a report warning `product type has
been updated from X to Y`), and the create spec's type is then stale.

No parentage or variation theme is needed (the ASIN is) — a partialupdate leaves
every attribute the row does not carry exactly as it is. It is also
idempotent: when the listing already shows some of the new copy (an
earlier run got part of the way), submit the whole edit again rather than
inspecting the live listing to decide which fields to skip — there is no
page here that reads bullets or the product type back, and hunting for
one costs more calls than re-sending fields that already match. Run one fill +
upload per marketplace (`--marketplace SA`, then `--marketplace AE` on
the `.ae` host), each from a template downloaded on that marketplace.
`inspect TEMPLATE --field <attribute>` lists the real columns.

Rule of thumb: **no SKU yet → create (pin the ASIN if one exists, see
below); the SKU exists → partialupdate.** The operation column is authoritative,
so set it **explicitly on every row, children included** — a blank child
operation is a common cause of a child failing to join its family.

> **A `create` with no ASIN MINTS one, and `fill` will not do that
> undeclared.** A seller SKU is **account-scoped**, so on a unified
> pan-regional account (MENA: SA / AE / EG) minting for a SKU that
> already sells anywhere **re-points that SKU to the new ASIN
> account-wide and orphans the old one** — reviews, ratings and rank go
> with it, and Amazon never re-issues a retired ASIN. Nothing in the
> feed report shows this: Amazon reports it as a clean create.
> So `fill` refuses the row until you say which case it is:
>
> * **Relisting** an existing product (another marketplace, same SKU) →
>   pin its current ASIN: `"asin": "B0EXAMPLE1"` on the row.
> * **Genuinely new** to this account → `"mint_new_asin": true`, on the
>   spec (covers every row) or per row. **In a spec that also pins an
>   ASIN somewhere, the top-level form is refused** — a blanket
>   declaration would wave through a pin you meant to set and lost,
>   which is the mistake this guard exists to catch, so each real mint
>   is owned on its own row. A rebuild that pins its children and mints
>   a fresh parent puts the key on the parent row only.
>
> Only `external_product_id_type: asin` counts as a pin. A UPC / EAN /
> GTIN identifies the *product*, not an existing listing, so Amazon can
> still mint; and `GTIN Exempt` says only that there is no barcode, not
> that there is no ASIN. Neither is a declaration.

## End-to-end flow (product link → live listing)

1. **Extract** the product from the supplier link (see
   `1688-sourcing.md`) or the uploaded detail images: every tile, into a
   fact sheet.
2. **Generate** an Amazon title, 5 bullet points, and a long
   description from the fact sheet — category, competitor check and
   writing rules in `references/listing-copy.md`.
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
