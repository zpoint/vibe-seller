---
name: amazon-listing
description: "Amazon listing CRUD via the category flat-file (Add Products via Upload). Create a variation family (parent + colour/size children) and edit existing listings with partial updates (each row pins its ASIN) — all in one template round trip. Full update runs only when the user asked for one by name (`--ops update`); delete is disabled (refused by the scripts). Also covers the end-to-end sourcing flow: a supplier link (e.g. 1688) or uploaded detail images → read every detail tile into a fact sheet → name the true category → check competitors on the storefront → generate title / bullet points / description → review with the user → propose the parent-child structure → fill the template → upload → read the processing report. Load before any browser-use action on sellercentral.amazon.<tld>/listing/upload, or when the task is to create or edit a listing."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
gates: [upload_verdict]
verify_by: |
  Read Manage Inventory through the helper, from the task workspace:
  `SKUS=<every attempted SKU, comma-separated>
  SC_HOST=sellercentral.amazon.<target-tld> browser-use <
  .claude/skills/amazon-listing/scripts/bh_listing_status.py` — every
  SKU in `rows` with its intended ASIN (nothing attempted in
  `missing`); the helper refuses unless the page's `ue_mid` is the
  target marketplace. Then that marketplace's Pricing view shows the
  price and stock. The full "Done when" list is in the body.
---

# Amazon — Listing CRUD (flat-file upload)

Load the `amazon-shared` skill (Skill tool) as well: it covers the Ziniao
login challenge loop, marketplace TLDs, switching marketplace, and
navigating by direct URL.

Amazon's **Add Products via Upload** takes a category **flat-file
template** — a macro-enabled `.xlsm` whose `Template` sheet is a wide
table (one column per attribute). One upload creates or edits a whole
**variation family** (a parent plus N colour/size children) at once. It is
the batch equivalent of the per-SKU web wizard, and the default for
anything touching more than one variant.

**Where files go.** Work from the task workspace root (your CWD): the
upload markers, batch verdicts and anything you deliver live there.
Templates and the filled `.txt` go in `~/.vibe-seller/downloads/<slug>/`,
because the browser must read them to upload and cannot read `/tmp`.
Durable data reused by later runs goes under `store-data/<slug>/`
(`listing-specs/`, `listing-copy/`, and dated captures under
`<area>/<YYYY-MM>/`).

References — load what the task needs:

- **`references/listing-copy.md`** — writing or rewriting title / bullets
  / description / search terms from a fact sheet, category and competitor
  check. Load before writing any copy, unless the task hands you confirmed
  final copy.
- **`references/template-round-trip.md`** — download → inspect → fill →
  upload → read-feedback, the two template dialects, the spec format, and
  parent vs child fields. Load for any listing CRUD.
- **`references/error-priors.md`** — fixes the processing report keeps
  asking for (two-click submit, 90502, 18320, 8560, per-marketplace
  offer/stock columns, reading `::submission_status`).
- **`references/verification.md`** — proving which marketplace a page
  shows, confirming live on Manage Inventory, benign "ASIN -" / "N/A"
  states, and how to report an image-deferred family.
- **`references/cross-marketplace.md`** — relisting a product on another
  marketplace with the same ASIN.
- **`references/1688-sourcing.md`** — turning a supplier link into a
  filled template (extraction, local OCR, bilingual review).
- **`references/fulfillment-channel.md`** — FBM (`DEFAULT`) vs FBA channel
  codes, and zeroing a merchant quantity for an FBA seller.

## Done when

- Every attempted SKU is live **on the target marketplace's own
  inventory**: `bh_listing_status.py` run with that marketplace's
  `SC_HOST` lists each SKU in `rows` with the intended real ASIN (not `-`)
  and nothing in `missing`. A batch row on a status page, or a listing
  that landed on a sibling marketplace, is not done.
- The whole family is there: the parent shows `Variations (N)` and every
  child has its ASIN. A parent with a missing child is not done.
- Every uploaded batch has a `parse-feedback` verdict, and the latest
  shows zero errors of any severity except the missing main image
  (18320). A SUCCESS (OTHER) / "Action required" row (e.g. 100476) is
  still unresolved.
- Price and stock show in that marketplace's Pricing view.
- For a copy-only partialupdate: the report says "applied without any
  errors" per SKU and the new title shows on every SKU (bullets and
  description have no reader; the report line is the evidence).
- An image-only gap is reported as "created, suppressed pending main
  image", not as "live".

Details and the Manage Inventory page layout: `references/verification.md`.

## Start from the last spec Amazon accepted

Before writing a spec, look in `store-data/<slug>/listing-specs/` for
`<product_type>__<CC>.json`. It is the spec behind the last batch of this
category Amazon accepted on that marketplace, saved automatically by
`parse-feedback --batch-id` when the verdict came back clean. Copy its
`spec`, change only what this listing changes (SKUs, ASIN pins, copy,
price), and keep every other field.

Fields Amazon enforces on a create are flagged only "Conditionally
Required" in the template, so nothing warns about them locally; Amazon
rejects them one feed at a time, 30–60 minutes per round. The accepted
spec already carries them. `fill` names every field the accepted spec set
that yours leaves empty — treat that warning as a rejection you haven't
waited for yet. No entry for your marketplace? Another marketplace's entry
for the same product type is the next-best reference; the attribute set
belongs to the category.

## Work it like a human: upload → read the report → fix → repeat

Templates, required fields, valid values and upload mechanics change per
product type and over time, so run the loop rather than a remembered
recipe:

1. **Download a fresh template** for the exact product type (Amazon's own
   errors say "download the latest template").
2. **`inspect` it.** This category's field set, required fields and valid
   values are the ground truth.
3. **Fill, upload, fetch the processing report, `parse-feedback` it.** It
   prints `sku=… field=… : MESSAGE` from the summary and the per-cell
   comments (批注).
4. **Fix exactly the field each error names**, with a value from the
   template's valid set (`inspect --field NAME`). Act on the report's
   words rather than theorising a root cause. See
   `references/error-priors.md` for the recurring ones.
5. **Re-upload and repeat** until only the missing main image remains.
6. **Verify on Manage Inventory** with `bh_listing_status.py` — the feed
   count is not the evidence.

### Finishing an upload

Each upload is tracked by files in the task workspace. The verdict is
checked once, when you submit the result: an upload with no clean verdict
is raised there and then, and one still unresolved ships as a caveat on
the result rather than keeping the task from finishing.

- `bh_upload_flatfile.py` writes `UPLOAD_ATTEMPT.json` as soon as its own
  checks pass and it is about to put the file on the live page — that is
  the declaration that an upload is happening.
- The same helper writes `UPLOAD_BATCH_<id>.json` for each submitted
  batch.
- `listing_bulk.py parse-feedback <report> --batch-id <id>`, run from the
  task workspace root, writes `BATCH_<id>_VERDICT.json`.

Nothing else arms this: downloading or inspecting a template, and filling
one for the user to review, are not uploads. Every batch needs a verdict,
and the latest one needs to be clean (no errors except the missing main
image) — run parse-feedback before you submit. If the helper failed and
you uploaded by hand, find the batch id on the status page and verdict it
the same way. If no upload happened after all, delete
`UPLOAD_ATTEMPT.json` and say in the result why the upload was abandoned.

## Deterministic browser helpers (use these first)

Four env-parameterised harness scripts handle the finicky browser steps.
Run them through the store wrapper from the task workspace root and read
the single `RESULT {json}` line.

Seller Central renders in whatever language the session is set to
(English, Chinese or Arabic on the same URL), so the helpers never key on
a control's wording: readiness is the Submit button turning enabled, and
the report link is found by its href or `icon` attribute. Hand-drive the
same way — prefer a state change, an attribute or an href over a label.
A helper's `ok=false` names its own reason; a stale banner from an
earlier page is not that reason.

If a helper misses on timing, re-run it with its env knobs before
hand-driving: `bh_upload_flatfile.py` takes `UPLOAD_LOAD_WAIT` (default
15 s) and `UPLOAD_INTROSPECT_WAIT` (default 12 s). Fall back to hand
exploration only for a structural reason the helper names (widget absent,
region-stamp mismatch), and prefer fixing the input (regenerate the
template, fix the spec) over hand-clicking.

```bash
S=.claude/skills/amazon-listing/scripts
DL=~/.vibe-seller/downloads/<slug>

# 1. Generate + download ONE marketplace's template. Ticks the target
#    store and reads the tick back (kat-checkbox ignores JS clicks);
#    ok=false means the target never got ticked — don't fill from it.
#    Reading a template this way arms nothing — no upload is owed.
SC_HOST=sellercentral.amazon.<tld> PRODUCT_TYPE=<keyword> \
STORE_LABEL=Amazon.<tld> DOWNLOADS_DIR=$DL \
browser-use < $S/bh_download_template.py
# `listing_bulk.py inspect` prints "marketplaces:" — the target has to be
# listed; `fill --marketplace <CC>` hard-fails on a wrong-region template.

# 2. Stage + submit the filled .txt (file-chooser intercept, two-click
#    Submit, batch id). Writes UPLOAD_ATTEMPT.json at the arm point, then
#    UPLOAD_BATCH_<id>.json per submitted batch.
UPLOAD_FILE=$DL/out.txt SC_HOST=sellercentral.amazon.<tld> \
MARKER_DIR="$PWD" browser-use < $S/bh_upload_flatfile.py

# 3. Fetch THAT batch's report and verdict it. Parse the `…__batch<id>`
#    copy it hands back: reports don't name their batch and same-named
#    downloads overwrite each other.
SC_HOST=sellercentral.amazon.<tld> BATCH_ID=<id> DOWNLOADS_DIR=$DL \
browser-use < $S/bh_fetch_report.py
python3 $S/listing_bulk.py parse-feedback <report> --batch-id <id>
# run from the task workspace root: the verdict lands in the current dir.

# 4. What Manage Inventory says each SKU is, on THAT marketplace: exact
#    SKU -> ASIN plus the missing SKUs, refused unless ue_mid matches.
#    One call per family per marketplace:
SKUS=<parent>,<child-1>,<child-2> SC_HOST=sellercentral.amazon.<tld> \
browser-use < $S/bh_listing_status.py
```

The helpers encode the mechanics (decoy input, region stamp, two-click
submit, shadow-DOM rows); your judgement stays with the spec: which
operation per row, which fields, and how to fix what the report names.

## The scripts

```bash
S=.claude/skills/amazon-listing/scripts
PY=python3   # the agent venv's python; install requirements.txt if openpyxl /
             # rapidocr-onnxruntime are missing
```

The references use these two variables.

- **`listing_bulk.py`** — the deterministic template writer. It keys every
  field by its API name, identical in every console language, and
  auto-detects both template dialects (legacy `fptcustom` and the unified
  NGS spreadsheet), so the same spec drives either. Always produce the
  upload file through `fill`: it clears the unified template's prefilled
  example/instruction rows, which would otherwise upload as SKUs and leave
  the children failing. See `references/template-round-trip.md` § 0.
  - `inspect TEMPLATE.xlsm [--field NAME]` — dialect, field set, required
    fields, accepted enum tokens, and which column each friendly role
    (sku / operation / parentage / parent_sku / variation_theme / brand /
    offer) maps to. Run it on every fresh template.
  - `fill TEMPLATE.xlsm --spec SPEC.json --out OUT.xlsm` — writes parent
    and child rows, the operation per row, validates against the
    template's metadata, preserves macros, and writes the `.txt` to upload.
    An off-list enum value is a warning, all named at once; Amazon's real
    rule is the accepted-spec library (`--allow-unlisted-enum FIELD`
    silences one you checked).
  - `parse-feedback REPORT [--batch-id ID]` — Amazon's verdict per SKU and
    field. A report whose only error is the missing main image prints
    DONE; don't re-upload for it. How to read it:
    `references/error-priors.md` § "Reading the report".
- **`ocr_1688.py`** — local, GPU-free OCR of supplier detail images.

## Operation rules (the in-sheet `update_delete` column)

The operation is chosen per row in the spec and set explicitly on every
row, children included:

| `operation` in spec | `update_delete` cell | Use when |
|---|---|---|
| `create` (default) | *blank* | New SKU. Needs a pinned `asin` **or** an explicit `"mint_new_asin": true`. |
| `partialupdate` | `partialupdate` | Edit an existing SKU: only the fields present change. Pins `asin`. |
| `update` | `Update` / `Create or Replace (Full Update)` | Only when the user asked for a full update by name. Clears every attribute the row leaves blank. Pins `asin`. |
| `delete` | — | Disabled. |

Rule of thumb: no SKU yet → create (pin the ASIN if one exists); the SKU
exists → partialupdate.

**`update`** is refused by `fill` unless the run passes `--ops update`, and
by `bh_upload_flatfile.py` unless `ALLOW_FULL_UPDATE=1` (it reads the
file's operation column). Use them only when the request says "update" /
"full update" in so many words — e.g. re-listing SKUs the user deleted by
hand under their old SKU and ASIN. A full-update row has to carry the whole
listing, because whatever it leaves blank is cleared.

**`delete`** is refused with no opt-in. On a unified account a delete
removes the SKU from every marketplace, and a deleted SKU can never be an
FBA offer again. If a task seems to need one, stop and tell the user.

**Every `partialupdate` / `update` row pins the ASIN it edits** (a parent
row pins the parent ASIN); `fill` refuses one that doesn't. Without a
product id Amazon re-runs catalog matching on the new attributes and can
move the family's SKUs to new ASINs on one marketplace. Read each SKU's
ASIN on that marketplace (`bh_listing_status.py`) before writing the spec;
the marketplaces of a unified account can differ, parents especially.

> **A `create` with no ASIN mints one, and `fill` won't do that
> undeclared.** A seller SKU is account-scoped, so minting for a SKU that
> already sells anywhere on a unified account re-points that SKU to the
> new ASIN account-wide and orphans the old one with its reviews, ratings
> and rank. The feed reports it as a clean create. So declare which case
> it is:
>
> * **Relisting** an existing product → pin its current ASIN:
>   `"asin": "B0EXAMPLE1"` on the row.
> * **Genuinely new** to this account → `"mint_new_asin": true`, on the
>   spec or per row. In a spec that also pins an ASIN somewhere, the
>   top-level form is refused, so a pin you meant to set can't be waved
>   through; a rebuild that pins its children and mints a fresh parent
>   puts the key on the parent row only.
>
> Only `external_product_id_type: asin` counts as a pin. A UPC / EAN /
> GTIN identifies the product, not a listing, and `GTIN Exempt` only says
> there is no barcode.

### Editing existing listings (title / bullets / description / attributes)

`operation: partialupdate` on the existing SKUs, carrying only the changed
fields, with `fill ... --ops partialupdate` so no row can create a SKU or
mint an ASIN. Bare attribute names are enough; `fill` resolves each to
this marketplace's column (pass `--marketplace`), and a list spreads over
the field's `#1..#N` slots:

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

`product_type` is the type each SKU has now: read it from
`store-data/<slug>/listing-specs/sku-product-types.json`, since Amazon may
have re-filed it after the create (a report warning `product type has been
updated from X to Y`). No parentage or variation theme is needed. A
partialupdate is idempotent: if an earlier run got part of the way, send
the whole edit again rather than hunting for a page that reads bullets
back (there isn't one). One fill + upload per marketplace, each from a
template downloaded on that marketplace.

## Relisting the same product on another marketplace

When a product lives on one marketplace and the user wants it "the same"
on another, the default intent is the **same ASIN on both**: Amazon pools
ratings and reviews by ASIN, so a new ASIN restarts the new marketplace at
zero stars. Create a new ASIN only when the user asks for a separate
listing or the marketplaces are on separate catalogs.

New ASINs AND the same children on both marketplaces is not a
contradiction — it is a sequence. Create on ONE marketplace first with
`mint_new_asin`, wait for its report, read the ASINs it minted with
`bh_listing_status.py`, and only then create on the second marketplace
pinned to exactly those. Never
mint on both (two different ASINs), and don't upload the second before the
first's ASINs are read: on a unified account a second mint for the same
SKU re-points it account-wide. Re-pinning the family's old ASINs answers a
different request. If the request is ambiguous about new vs existing
ASINs, ask.

Make the match **proactively** rather than waiting for an `8560`:

1. **Get the source ASINs — one helper call per marketplace.** Map every
   SKU (parent and each child) to its ASIN on the SOURCE marketplace, and
   see what the TARGET already holds, before planning a create: run
   `SKUS=<parent>,<child-1>,... SC_HOST=sellercentral.amazon.<tld>
   browser-use < $S/bh_listing_status.py` once with the source tld and
   once with the target's. Manage Inventory's search view collapses a
   family to its parent row, and a public product page proves an ASIN
   exists, never which SKU is on it. Reuse the same SKUs on the target;
   that keeps it idempotent.
2. **Work on the target marketplace, pin every row, pick the operation by
   whether the ASIN is in the target catalog, supply the full offer, and
   verify the child ASINs match.** All of it is in
   `references/cross-marketplace.md`.

## End-to-end flow (product link → live listing)

1. **Extract** the product from the supplier link
   (`references/1688-sourcing.md`) or the uploaded detail images: every
   tile, into a fact sheet.
2. **Generate** a title, 5 bullet points and a description from the fact
   sheet (`references/listing-copy.md`).
3. **Bilingual review** — show the copy in the user's language and the
   marketplace language, plus the proposed parent-child structure, and
   ask with `AskUserQuestion`. Upload nothing until the answer comes back;
   this is the one interactive step.
4. **Download** the template into `~/.vibe-seller/downloads/<slug>/` and
   `inspect` it.
5. **Fill** a spec (parent + children), starting from the accepted spec if
   one exists.
6. **Upload and run the loop** until the only remaining error is 18320,
   then verify per "Done when".

## Sourcing login

Some supplier (1688) bulk specs need a login; the core product data does
not. When a login is needed it is a QR scan: ask the user to scan it (say
which QR) and wait, rather than authenticating on their behalf.
