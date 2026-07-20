---
name: amazon-image-studio
description: "Generate Amazon-ready product images (main image, 5-point-description infographics, lifestyle, colour-variant shots) from supplier photos (e.g. a 1688 link) plus the store's existing image style, using the vibe_seller_generate_image tool (kie.ai / Nano Banana). Load this whenever the task is to CREATE or EDIT product photos from a product link and/or the store's own style — e.g. 'match my store's image style; generate a main image and an infographic for this supplier link' (in any language). Covers: gathering reference images, writing generality-safe prompts in the user's language, the human confirm step, and the mandatory view-and-self-audit-against-the-reference loop."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
---

# Amazon Image Studio

Turn supplier photos + your store's look into Amazon-compliant images.
The generator is the `vibe_seller_generate_image` MCP tool. It **pauses
for the user to review and edit your prompt/model before anything is
generated** — that pause is expected; wait for the result. It **fails
immediately if no vision key is configured** — if so, tell the user to
set it in Settings → AI → Vision, then stop.

## The one rule that makes this work for ANY product

**The product's true appearance comes ONLY from the reference images,
never from your words.** Do not describe the material, texture, knit,
weave, colour, or shape in the prompt — pass the supplier photo and let
the model replicate it. Your prompt describes only:

- **layout / composition** (e.g. "worn on a model up top, a fan of the
  colourways below" — mirror the store's existing main-image layout),
- **background** (main image: pure white, RGB 255,255,255; product fills
  ~85% of the frame; no text, logo, props, borders),
- **on-image text** — infographics only, spelled EXACTLY, in the user's
  language,
- **compliance** (Amazon main-image rules above).

The only negative constraint is generic: **"replicate the references
faithfully; do not invent elements that aren't in them (text, logos,
patterns, pockets, accessories)."** Never write product-specific
negatives like "no ribbing" — that hardcodes one product and breaks the
next. A ribbed sock, a smooth sock, a lantern, a cable: same prompt
skeleton, the reference carries the truth.

**Declare each image's ROLE by position — always.** Images reach the
model in the exact order of `reference_images`, and Google's own prompt
templates address them by position ("the dress from the first image…").
Open the prompt by assigning roles, e.g. (in the user's language):
"Image 1 is the layout/style reference — take only its composition,
palette and typography, never its product. Images 2–5 are the product
photos; the product's appearance comes solely from them."
Without this the model guesses which image is fact and
which is style, and a style image's product can bleed into yours. Note
for the confirm popup: any image the user adds there is APPENDED after
yours — if you expect the user to add a style reference, say in the
prompt that "the last image (if present) is the style reference".

## Write the prompt in the user's language

Chinese task → Chinese prompt; English task → English prompt. The model
handles both, and the user reviews the prompt in their own language in
the confirm popup. Do not translate.

## Workflow

1. **Gather references.**
   - Supplier photo(s): the 1688 (or other) product image URL(s). Pass
     URLs directly — the tool accepts them.
   - Store style: open a representative existing listing on the store
     and grab its main image + one infographic (their layout, colour
     palette, headline style, bottom feature-chip band). Pass these as
     additional `reference_images` so the output matches the shop's look.
   - **Verify every style reference is a REAL live product image before
     using it.** Listings whose images were never uploaded show a
     "No image available" placeholder, and lazy-loading pages serve tiny
     GIF stand-ins (e.g. a 60×40 pixel image) until scrolled — passing
     either poisons the generation. Judge what you actually fetched: is
     it a full-resolution product photo? Never pass a placeholder
     "just in case".
   - **Style-reference search order — fully autonomous, never wait for
     the user to supply images:**
     1. A live-imaged listing of the SAME product type on this store.
     2. Otherwise ANY live-imaged listing on this store, even another
        category — brand style (white-background hero look, layout,
        palette, chip band) carries across categories; you reference
        its composition, never its product.
     3. If the whole store has no live images at all, ASK the user once
        (AskUserQuestion, in their language): would they like to provide
        a style image — they can drag/upload it in the confirmation
        popup's reference-image area — or proceed with supplier photos only? A
        user-provided style reference gives a better result, but it is
        OPTIONAL: if they decline or choose to proceed directly, continue
        immediately with supplier photos alone. Never stall waiting for
        an upload without having asked.
2. **Main image** — call `vibe_seller_generate_image` with
   `kind: "main"`, `model: "nano-banana-pro"` (or `nano-banana-2` for a
   cheaper no-text shot), the supplier + style references, and a prompt
   that only sets layout/background/compliance.
3. **Infographic(s)** — for a 5-point description, generate one
   infographic per theme (or one that lists the points). Use
   `nano-banana-pro` (best in-image text). Put the store's infographic
   as a style reference and spell every headline/label EXACTLY.
4. **Self-audit — mandatory.** After each image is saved, VIEW it (read
   the returned workspace `path`) and compare it against the ORIGINAL
   supplier photo item by item: texture, colour, shape, cuff/opening,
   proportions; for infographics, proofread every character. If anything
   differs, regenerate with a prompt that names the specific fix (e.g.
   "the toe seam should match the reference exactly") — keep the layout,
   add the correction. Do not hand a wrong image to the user as final.

## Model choice

- `nano-banana-pro` — infographics and anything with on-image text
  (industry-best multilingual text rendering); premium main images.
- `nano-banana-2` — cheaper/faster main and colour-variant shots with no
  text.

## After generation

The images are saved in the task workspace and shown inline to the user.
To put them on a listing, hand off to the `amazon-listing` flow (Manage
Images) — this skill only produces the images.
