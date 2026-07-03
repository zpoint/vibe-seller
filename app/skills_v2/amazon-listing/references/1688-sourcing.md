# Sourcing a listing from a supplier link (1688) → filled template

> **Load this when the task starts from a product link.** The goal:
> turn a 1688 offer URL into a reviewed, filled Amazon template. The
> agent (an LLM) does the extraction, copywriting, and review; the two
> scripts do the deterministic OCR and template writing.

## 1. Open the offer and extract core data

Use the store's browser wrapper (an `-aux` session is fine — this is a
public third-party page, not seller-central):

```bash
~/.vibe-seller/bin/<slug>/browser-use --session <slug>-aux <<'PY'
new_tab("https://detail.1688.com/offer/<OFFER_ID>.html?offerId=<OFFER_ID>&forcePC=1")
wait_for_load()
PY
```

The core product data lives in the page's `window.context` JSON and the
DOM — **no login needed** for it:

```bash
~/.vibe-seller/bin/<slug>/browser-use --session <slug>-aux <<'PY'
# title, price, sku props (colour/size), weight, category, detail-image url
print(js("return JSON.stringify(window.context && window.context.result ? window.context.result : {}).slice(0,4000)"))
# gallery + detail image srcs
print(js("return JSON.stringify(Array.from(document.images).map(i=>i.src).filter(s=>/alicdn|tmall/.test(s)))"))
PY
```

Read off: the title, the **SKU property that varies** (usually Colour —
this becomes your `variation_theme`), the size, the unit weight, the
leaf category, and the image URLs. Login (a **QR scan**) is only needed
for extra bulk specs — if you need it, **ask the user to scan the QR**
and wait; do not authenticate for them. The page auto-switches to a
Global/English view.

## 2. Download the images (referer-protected)

The gallery/detail CDN (`cbu01.alicdn.com`, `itemcdn.tmall.com`) rejects
a bare `curl`. Read the `src`s from the DOM (above) and fetch each with
a 1688 referer:

```bash
curl -sL -e "https://detail.1688.com/" -o /tmp/<task>/img_$N.webp "<img-src>"
```

Save captures to `/tmp/<task>/`, never `knowledge/` (capture rule).

## 3. OCR the detail images (local, no GPU)

1688 puts the spec table, feature callouts, and size/colour charts
inside the long **description images**, not the HTML. OCR them locally:

```bash
$PY $S/ocr_1688.py /tmp/<task>/ --json --min-conf 0.6 > /tmp/<task>/ocr.json
```

`ocr_1688.py` uses `rapidocr-onnxruntime` (ONNX runtime on CPU — no GPU,
no torch/paddle, no cloud) and reads mixed Chinese + English. WEBP is
converted to PNG in memory. Fold the recognised lines into the
product-info blob you pass to the copy-generation step.

## 4. Generate the Amazon copy

From the extracted page data + OCR text, generate:

- **Title** — lead with the registered brand (from the template's
  `brand_name` valid values), then the key nouns/specs a buyer searches;
  respect the category's title length. Do not copy the supplier's
  keyword-stuffed title verbatim.
- **5 bullet points** — one benefit each, specification + why it matters.
- **A long product description** — cover materials, use, care, sizing.
- **Search terms** (`generic_keywords`) — short 1–2 word phrases, the
  category essentials + differentiators; not long model numbers.

Translate the source (often Chinese) into the **target marketplace
language**. Keep the copy truthful to what the OCR/page actually say —
do not invent specs.

## 5. Bilingual review (required, interactive)

Present to the user, side by side:

- the generated **title / bullets / description** in **both the user's
  language and the target marketplace language**, and
- the **proposed parent-child structure**: the variation theme (e.g.
  Colour), the parent SKU, and each child (colour → SKU).

Wait for the user to confirm or edit. This is the one step that is
genuinely the user's call — the copy and the variation split are
judgement, not mechanics. Only after confirmation proceed to fill.

## 6. Fill + upload

Build the spec (parent + one child per confirmed colour/size) and hand
off to the template round trip (`template-round-trip.md` §3–4): `fill`,
upload, `parse-feedback`, iterate on row errors.

The reference implementation that predates this skill
(`AmazonListingCreator/listing_util`) fills a **single** variant and
matches by localised label; this skill supersedes it with multi-row
parent/child filling keyed by field API name and an explicit operation
column.
