# Writing listing copy: facts → category → competitors → copy → review

> **Load this before writing or rewriting ANY title / bullets /
> description / search terms** — a new listing, a relist on another
> marketplace, a copy refresh, Amazon or noon — whatever the material is:
> a supplier link (`1688-sourcing.md` gets you the images and OCR, then
> come back here), detail images the user uploaded, or product photos.
> If the task hands you **final, confirmed copy**, use it verbatim and
> skip this file.

Why this exists: several product families went live with copy written
from a glance at a thumbnail. The supplier detail page was one image
~30,000 px tall; read whole it shrinks until no text survives, so the
agent saw little more than the product's shape and colours and wrote the
rest from the SKU name. One listing named the wrong product category
outright; another stated a material the spec table contradicted. Shoppers
searching the real category never saw them, and the bullets promised
things the page never said. Every step below closes one of those gaps.

## 1. Read every word the supplier gave you → a fact sheet

- **Tall images are tiled for you.** When a task has uploads, the prompt
  lists each tall detail page followed by its tiles
  (`uploads/.tiles/<name>/partNN.jpg`). Read **all** tiles, in order —
  never only the original, never only the first few. For images you
  downloaded yourself (1688), run `ocr_1688.py` and also Read the images;
  OCR misses text set on photos.
- Write the facts to `store-data/<slug>/listing-copy/<family>.facts.md`
  (family = the SKU stem, e.g. `CHG-011`), one line per fact with the
  tile it came from. Cover: what the supplier calls the product (their
  headline, often in English on the image), the product type and form,
  materials and their percentages, rated specs (power, capacity,
  dimensions, compatibility), construction, stated functions, colours,
  pack contents, sizes or the size chart, care / safety notes.
  **Unknown stays unknown** — no field is filled from what similar
  products usually have.
- **SKU names, folder names and earlier listings are not evidence.** A
  SKU stem is an internal code someone typed, and one that names a
  category has named the wrong one before. Old copy on the listing is
  what you are here to check, not a source.

## 2. Name the product the way the marketplace does

Decide the category from the facts, then translate it into the words
shoppers on the target storefront search. Some terms whose literal
translation misses what shoppers type:

| supplier says | marketplace words | not |
|---|---|---|
| 氮化镓充电器 | GaN charger, compact fast charger | gallium nitride adapter |
| 快充 / PD / QC | fast charging, USB-C PD 65W (the protocol and wattage printed) | "quick charge" alone |
| 多口 | multi-port, 2-port / 3-port | multi-mouth |
| 编织线 | braided cable | woven line |
| 电热水壶 | electric kettle | electric water pot |
| 304 不锈钢 | 304 stainless steel (only if the spec table says 304) | stainless steel food-grade |
| 硅藻泥地垫 | diatomite bath mat, quick-dry stone mat | diatom mud carpet |
| 高腰 (apparel) | high waisted / high waist | mid-rise |
| 无痕 | seamless, no-show | traceless |

Write the category, its two or three search phrases, and the matching
marketplace product type (or noon family) at the top of the fact sheet.

## 3. Look at the competitors before writing a word

On the **target storefront** (e.g. `amazon.ae/s?k=…&language=en_AE`,
public pages — an `-aux` browser session, not seller central):

1. Search the 2–3 category phrases from step 2.
2. Pick the 3–4 results whose **main image** looks most like ours (form,
   size, ports or parts, material look, pack format). If unsure, set the images side by
   side in one page and screenshot it — compare pictures, not titles.
3. Open each and record title, the five bullets, and the breadcrumb.
   The breadcrumb leaf tells you the category the marketplace files
   this product under; it should match the product type you chose.

Save a short table (ASIN, title, price, reviews, why it is comparable) in
the fact sheet. Competitors set the **vocabulary and order**; their claims
are not ours — copy no sentence and no fact.

## 4. Write the copy from the fact sheet only

**Match the store's own house style first.** Open 2–3 of this store's
own live listings on the same storefront (search the brand; prefer the
same or a nearby category, and the ones with reviews) and copy their
*form*: the title pattern, the bullet header shape (`【Feature, Benefit】` in two parts is common),
and the bullet length. A family that reads shorter or plainer than its
siblings looks unfinished next to them — one review sent back bullets of
120–200 chars beside the store's 180–250. Their *facts* are theirs; only
the form carries over.

- **Title** (≤ 200 chars incl. a child's `, M` / `, XXL` suffix): who it
  is for → the category phrase shoppers search (step 2) → pack count →
  2–4 differentiators from the facts. **No brand word in the title** on
  Amazon or noon — the brand field already carries it, and the title's
  characters are worth more as search terms; include it only when the
  user explicitly asks for it. No promotional words, no claims the
  bullets cannot back, and **no word more than twice** — Amazon rejects
  the title (`fill` refuses it first).
- **noon silently drops a save containing `®` / `™`**: Save Changes →
  Submit reports nothing and the reload shows the old content. Leave
  those marks out of every noon field.
- **Five bullets**, each `【Header】` + two or three sentences, as long as
  the store's own (~180–260 chars when unsure): the header is the
  benefit, the sentences are the fact and why it matters. Order:
  category & fit → material → the standout feature → construction / use
  → pack, colours, sizes or specs, care / safety.
- **Description**: plain sentences built from the same facts, ending with
  sizes or specs and care.
- **Search terms**: shopper phrases and synonyms not already in the
  title, ≤ 250 bytes, no brand names (ours or others), no ASINs.
- **Never**: a claim with no fact behind it; medical, antibacterial or
  certification claims the page does not prove (a wattage, a safety mark,
  a grade of steel); another brand's name.
- noon needs the same copy in **Arabic** (title, bullets, description) —
  translate the confirmed English, do not rewrite it.

## 5. Review before anything is uploaded

Unless the task already gives confirmed copy, the copy is a proposal and
the user decides. Write the review (`LISTING_COPY_REVIEW.md` in the task
workspace), in the user's language plus the marketplace language:

- the fact sheet's key lines (with tile references),
- the category decision and the competitor table,
- title, bullets, description, search terms,
- for a new family: the proposed parent / child structure;

then ask with `AskUserQuestion` (approve / change) and **upload nothing**
until the answer comes back — the task waits in WAITING for the operator,
and resumes with the answer. Apply the edits the answer asks for; never
re-ask about copy already confirmed. Save what was proposed to
`store-data/<slug>/listing-copy/<family>.json`
(`{"title", "bullets", "description", "generic_keywords", "category",
"facts": "<path>"}`) so that task — and any later marketplace or noon
relist — starts from it instead of from scratch.

When the confirmed copy lands on a listing that already exists, it goes
through `operation: partialupdate` only (`SKILL.md` § "Editing existing
listings"); on noon through Product Import / the Content tab of the
existing SKU — never a create.
