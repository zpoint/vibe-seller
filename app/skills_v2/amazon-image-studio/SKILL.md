---
name: amazon-image-studio
description: "Amazon listing-image knowledge for the general vibe_seller_generate_image tool: Amazon's image requirements (main/secondary), how to collect reference images from Amazon listings and supplier pages (e.g. 1688), and how to avoid poisoning generation with placeholder/blank images. Load this ONLY when the image work is FOR Amazon — creating or editing listing images (main image, gallery, infographic/A+), or when the user asks to match an Amazon listing's image style or meet Amazon requirements. Do NOT load it for generic image requests with no Amazon connection ('edit this picture', 'make me a fun image') — the vibe_seller_generate_image tool alone handles those."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
---

# Amazon Image Studio

Amazon-specific knowledge for generating listing images with the
general `vibe_seller_generate_image` tool. The tool itself documents
the generic contract (user-language prompt, references carry a real
subject's appearance, roles by position, the user-confirm pause) — this
skill adds only what is Amazon:

## 1. Amazon image requirements

**MAIN image** (the one search results show — strictest):
- Pure white background, exactly RGB (255,255,255) — off-white fails
  Amazon's automated scan.
- Product fills ~85% of the frame, fully visible, not cropped.
- NO text, logos, badges, watermarks, borders, props, or accessories
  not included in the purchase. Product only, as the buyer receives it.
- ≥1000px on the longest side; ≥1600px recommended (enables zoom).
  Square (1:1) displays best. JPEG/PNG.
- Category variations exist (e.g. adult apparel is usually shown on a
  model; shoes as a single shoe at an angle). When in doubt, mirror
  what the store's own live listings of the same category do.

**Secondary images** (gallery slots 2-7+): lifestyle shots,
infographics with feature callouts, dimension/scale charts and
comparison tables are all allowed and convert well. On-image text is
fine HERE (never on the main image) — spell every word exactly in the
prompt and proofread the result character by character.

## 2. Collecting reference images

- **Supplier page (e.g. 1688)**: extract the original gallery image
  URLs from the page (full-size, not thumbnails) and pass them as
  `reference_images` directly — the generator fetches URLs itself.
- **Amazon listing** (style reference): open the listing's dp page and
  take the hi-res image URLs (`m.media-amazon.com/images/I/…`, request
  the large `_SL1600_` variant). These carry the store's composition,
  palette and infographic layout.
- **Never pass a blank/placeholder image.** Two traps produce them:
  - listings whose images were never uploaded show a "No image
    available" placeholder;
  - lazy-loading pages serve tiny stand-in GIFs (e.g. 60×40px) until
    the image scrolls into view.
  Judge what you actually fetched — is it a full-resolution product
  photo? A placeholder passed "just in case" poisons the generation.
- **Style-reference search order** (autonomous, never stall):
  1. a live-imaged listing of the same product type on this store;
  2. otherwise ANY live-imaged listing on this store (brand style —
     hero look, palette, chip band — carries across categories; you
     reference its composition, never its product);
  3. if the whole store has no live images, ask the user once
     (AskUserQuestion): provide a style image (they can drag one into
     the confirmation popup's reference area) or proceed with supplier
     photos only — optional; proceed immediately if declined.

## 3. Generate, audit, hand off

- One `vibe_seller_generate_image` call per image (`kind`: "main",
  "infographic", …). Use `nano-banana-pro` for anything with on-image
  text.
- **Every image comes from the model — including "just remove the
  background".** "Keep the product, only drop the background", "put it
  on white", "crop it square" are all *generation* jobs: one
  `vibe_seller_generate_image` call whose `reference_images` is the
  photo and whose prompt says to keep the product identical and change
  only the background. Do **not** reach for `rembg`, Pillow/OpenCV
  compositing, or ImageMagick — a PreToolUse guard blocks them, and
  `generated_images/` is the tool's directory (each file's recorded
  prompt + model is what the user's inline card claims). A local cutout
  also loses what the model gives you for free: relit edges, a clean
  contact shadow, and no halo on hair/mesh/glass.
- After each image is saved, view the returned workspace `path` and
  compare against the ORIGINAL supplier photo item by item (shape,
  colour, texture, proportions; infographic wording). Regenerate with a
  prompt naming the specific fix if anything differs.
- **If you cannot actually SEE the image** (the Read comes back as
  something you can't look at — some models are text-only), say so and
  let the user judge: the image renders inline in the task. Do NOT
  substitute a pixel-measurement script for looking, and never act on
  what such a script measured — corner-RGB sampling and
  coverage-percent arithmetic answer a different question than "is this
  the right product image", and "fixing" those numbers locally is how
  the deliverable stops being the model's.
- **Off-white background or an over-filled frame** — the two things a
  first pass gets wrong on a main image (e.g. RGB(254,254,253) instead
  of pure white, or the product bleeding to the edges). Fix by
  regenerating, never by post-processing: call the tool again with the
  PREVIOUS generated image as the `reference_image` and a prompt naming
  only the fix — "keep the product pixel-identical; background must be
  pure white RGB(255,255,255); leave a clear margin on all four sides
  so the product fills about 85% of the frame and touches no edge".
- **Revising on user feedback** — for ANY change the user asks after
  seeing a generated image (lighter/darker, bigger/smaller, recolour,
  remove or add an element, change composition, …): call
  `vibe_seller_generate_image` again and ALWAYS pass the PREVIOUSLY
  GENERATED image (its `generated_images/…` path) as a `reference_image`,
  with a prompt describing ONLY the requested change. This EDITS the
  prior result — keeping what the user was happy with — instead of
  regenerating from the supplier photo and drifting. If the user also
  drops a NEW photo for the change (e.g. "this one's good, add my dog in
  the middle"), pass BOTH the previous generated image AND the new photo
  as `reference_images` and name each image's role by position in the
  prompt ("image 1 is the current design to keep; image 2 is the dog to
  add in the center").
- Putting the images ON a listing is the `amazon-listing` flow (Manage
  Images / flat file) — this skill only produces the files.
