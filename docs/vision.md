# Vision — AI image generation

General-purpose image generation for tasks: product photos, marketplace
listing images, infographics, banners, or an image the user just wants,
via kie.ai's unified API. The agent proposes; the **user confirms and can
edit** the prompt/model before anything is generated; the result renders
inline in the task stream and is saved in the task workspace.

## Model catalog (one key, many providers)

kie.ai is a unified aggregator — a **single** configured key reaches every
image model through the same `POST /api/v1/jobs/createTask` endpoint. The
selectable set is a curated, static catalog in `app/vision.py`
(`IMAGE_MODELS`), one `ImageModel` per row: our stable `id` (the contract
the agent/frontend/tool pass and we validate) → kie.ai's exact `slug`,
plus the `provider`/`label` and a per-image `usd` price. The confirm card
shows the price as **$** (English) or **¥** (Chinese, fixed `USD_CNY`
rate — an illustrative hint, not a bill). Default is **Nano Banana Pro ·
2K**.

**One selectable option per resolution/quality tier.** The catalog is
defined as per-family specs (`_FAMILIES`) and flattened to one
`ImageModel` per tier, so the card shows the *real* price of each tier
(GPT Image 2 at 1K/2K/4K = $0.03/$0.05/$0.08, etc.) instead of a single
hand-picked number — the earlier bug that made cross-model prices look
inconsistent (different models were shown at different, unlabeled
resolutions). Each variant's `extra` injects its own kie param
(`resolution` / `quality` / `rendering_speed`), so generation targets
that tier; pinned by `test_tier_variants_carry_their_param`.

The other thing that differs per model is the **reference-image input**:
field name and cardinality (`image_input`/`input_urls`/`image_urls`
arrays, or a single `image_url`). `generate_image()` builds the `input`
payload from each model's `ref_field`/`ref_array` + merged `extra`, so a
non-nano model receives its references (and tier param) instead of
silently dropping them. Prices/slugs/tier params verified against
kie.ai's pricing API + docs (2026-07); refresh manually.

Providers/models (image-to-image, tiers in parens): Google — Nano Banana
Pro (2K/4K), Nano Banana 2 (1K/2K/4K), Nano Banana Edit; OpenAI — GPT
Image 2 (1K/2K/4K), GPT Image 1.5 (Medium — cheapest OpenAI; its High
tier is omitted as it costs more than the newer, better GPT Image 2·4K);
ByteDance — Seedream 5
Pro (Basic/High), Seedream 4.5; Black Forest — Flux-2 Pro (1K/2K), Flux 2
Flex (1K/2K); Qwen — Image Edit; Ideogram — V3 Remix (Turbo/Balanced/
Quality). Pure utilities (Recraft bg-removal, Topaz upscale) are
intentionally excluded — they take no prompt and don't fit the card.

**Layering**: the MCP tool is platform-agnostic infrastructure. Platform
knowledge lives in skills — `amazon-image-studio` (Amazon image
requirements, gathering references from Amazon/1688, placeholder
pitfalls) is the first; other marketplaces (noon, MercadoLibre, …) add
their own skills on top of the same tool.

## Pieces

| Concern | Where |
|---|---|
| Config + kie.ai client + confirm registry | `app/vision.py` |
| HTTP endpoints | `app/routers/vision.py` |
| MCP tool `vibe_seller_generate_image` | `app/mcp_tool_schemas.py` + `app/mcp_server.py` |
| Settings UI | `frontend/src/components/settings/VisionPanel.tsx` (Settings → AI → Vision) |
| Confirm card + inline image | `frontend/src/components/conversation/ImageRequestCard.tsx`, `GeneratedImageCard.tsx` |
| Skill | `app/skills_v2/amazon-image-studio/SKILL.md` |

## Config / secret

The kie.ai key lives in `~/.vibe-seller/vision.json` (mode 0600) — a
secret, so **not** the DB, mirroring `profiles.json`. It is read back
masked (last-4). Admin-only to set. The `KIE_API_KEY` env var overrides
the file (for CI). See `app/vision.py`.

## Not configured → tool hidden, agent guides the user

Image generation is **conditionally registered**: when no key is set
(and not `VISION_FAKE`), `mcp_server._visible_tools()` drops
`vibe_seller_generate_image` from `tools/list`, so the agent never sees
(or dead-end-calls) a tool it can't use and the tool list stays clean —
industry practice over advertise-then-error. The MCP process is
per-task, so the set is read once at task start (no `list_changed`;
a key added mid-task appears on the next task).

Discoverability is one line, in ONE place — **not** per-skill:
`VISION_SETUP_BREADCRUMB` in `app/task_runner.py`, appended by
`_build_system_extra()` **only** under the same unconfigured condition
(configured tasks pay zero tokens). It tells the agent to guide the user
to Settings → AI → Vision and to emit the link `[Settings → AI →
Vision](#vision-setup)`. The frontend (`MessageBubble`) renders that
`#vision-setup` href as an inline CTA button that navigates to the
Vision settings panel — the visible text may be translated, the href is
the stable contract. Skills carry nothing about setup.

Tests: `tests/unit/test_mcp_tool_visibility.py` (hide/show/fake) +
`tests/unit/test_prompt_assembly.py::TestVisionSetupBreadcrumb`.

## Routes

| Method | Path | Notes |
|---|---|---|
| GET | `/api/vision/config` | `{kie_api_key_set, kie_api_key_masked, models, default_model}` — never the raw key |
| PUT | `/api/vision/config` | Set the key (admin only) |
| POST | `/api/tasks/{id}/image/generate` | MCP-tool entry + confirm gate (see below) |
| POST | `/api/tasks/{id}/image/confirm` | User's approve/edit/cancel |

## Poll budget is per resolution tier (and a timeout is resumable)

kie.ai generation is async: `createTask` returns a `taskId`, then we poll
`recordInfo` until `success`. Two rules, both learned from one live
failure:

**1. The budget scales with output pixels.** It used to be a flat
`60 × 4s = 240s` with a comment sized for 2K ("Pro 2K ~ 60s"). Measured
live against `nano-banana-pro` with the same prompt + reference: 2K lands
in ~60s, **4K took 147s** on one run and **exceeded 240s** on another —
a real user generation, which the flat budget killed at 4m17s. So 4K's
*tail* is what needs covering, not its median: `poll_budget_s()` in
`app/vision.py` reads the tier from the model's own
`extra['resolution']` and gives 1K/2K 240s, 4K **600s**. Quality- or
speed-tier models (`quality`, `rendering_speed`) get the default.
`vibe_seller_generate_image`'s MCP call passes `timeout=None`
(`mcp_server.call_api`), so nothing upstream caps this.

**2. Giving up does not cancel the job — it finishes and is billed.**
The old code raised a bare `RuntimeError('timed out')` and dropped the
`taskId`, orphaning an image the user had already paid for. Now
exhaustion raises `vision.GenerationTimeout`, which carries
`kie_task_id`; the router stores it in `_INFLIGHT[task_id] = (kie_task_id,
model)` and returns **504** whose message tells the agent to retry the
**same** model. That retry resumes the existing job via
`generate_image(resume_task_id=…)` — skipping both the reference upload
and `createTask`, so the already-billed image is collected instead of
bought twice. The handle is keyed by model as well, because resuming
another model's job would return the wrong image.

**Why the message insists on the same model.** The model on the confirm
card is the *user's* choice. On the live failure the agent read
`timed out`, reasoned "let me try a different model", and silently
dropped from the 4K the user had selected to `gpt-image-2-2k` — a
different image, a second charge, and the 4K job abandoned mid-flight.
The 504 text and the tool description now both say: retry the same model,
never substitute.

## Confirm-gate flow

The confirmation is a **server-side block**, not a permission hook — so
it works regardless of the agent's permission mode (auto/bypass or plan):

1. The agent calls `vibe_seller_generate_image`. The MCP proxy forwards
   it to `POST /api/tasks/{id}/image/generate` with a long httpx timeout.
2. The endpoint **fails immediately with 400 if no key is configured**.
3. Otherwise it registers a per-request `asyncio.Future`, emits an
   `image_request` SSE event `{task_id, request_id, prompt, model,
   models, reference_images, ...}`, and awaits the future.
4. The frontend renders `ImageRequestCard` — an editable prompt textarea
   + model dropdown + Confirm/Cancel — and on submit calls
   `POST /api/tasks/{id}/image/confirm {request_id, action, prompt, model}`,
   which resolves the future.
5. On confirm the endpoint calls kie.ai (create → poll → download), saves
   the PNG to `~/.vibe-seller/tasks/{id}/generated_images/<name>.png`,
   emits `image_generated {task_id, request_id, path, url}`, and returns
   the workspace path to the agent. On cancel it returns a `cancelled`
   status and writes nothing.

The user's edited prompt/model win over the agent's proposal. The saved
image is served by the existing `GET /api/tasks/{id}/files/{path}`
endpoint and shown inline via `GeneratedImageCard` (distinct from the
finished-task file explorer).

## Prompt-generality contract (baked into the tool + skill)

The product's appearance comes **only from the reference images**, never
from prompt words — so the same prompt skeleton works for any product
(a ribbed sock, a smooth sock, a lantern). The prompt sets only layout,
background, on-image text (infographics, spelled exactly, in the user's
language), and compliance. The one negative constraint is generic: "do
not invent elements not in the references." The agent writes the prompt
in the user's language and self-audits each result against the original
supplier photo, regenerating with a specific correction if it differs.

## `generated_images/` is model-owned (enforced)

The directory has exactly **one writer**: the vision router, after the
user confirms a generation. Every file there carries provenance — a
`generated_image` task message recording the prompt and the model — and
that message is the claim `GeneratedImageCard` renders those bytes
under. So an image deliverable must come from the **model**, never from
local pixel editing, no matter how the request is phrased ("just remove
the background", "only crop it", "make it pure white" are all
generation jobs).

Two PreToolUse guards in `app/ai/image_guards.py` hold the invariant:

| Guard | Denies |
|---|---|
| `check_local_image_edit` (Bash, registered in the `first_bash_deny` chain) | `rembg`/`backgroundremover`/`carvekit` in any form (including `pip install`); an inline snippet that both uses an imaging library and calls a produce/mutate function (`.save(`, `imwrite(`, `Image.new(`, `.paste(`, …); ImageMagick/ffmpeg against an image file; any write landing in `generated_images/` (redirect, `cp`/`mv`/`rm`/`tee`, inline save) |
| `check_generated_image_write` (Write/Edit/MultiEdit/NotebookEdit tool args) | the file-tool hop around the above — any `generated_images/**` target |

**Reading an image is never blocked** (`file`, `stat`, `Image.open` +
numpy): only *producing* one is. Guards inspect the inline command text
only, so skill scripts that legitimately touch images
(`amazon-listing/scripts/ocr_1688.py` reads them for OCR) are
unaffected. The deny message routes the agent to the correct fix —
regenerate with the previous generated image as a `reference_image` and
a prompt naming only the change — and states that the rule is platform
policy, so a reflection pass does not record a local-processing
workaround as knowledge.

**Why it is a guard and not just prose.** Task `2fde3b9c` ("keep the
content, only remove the background") generated the image with the
tool, then could not *see* the result: the task ran on a text-only
profile, so `Read` returned an image block the model discarded. It
substituted a numpy pixel audit, judged the background off-white
(254,254,253) and the product over-filled, and rebuilt the deliverable
with `rembg` + Pillow **over the tool's own PNG at the same path**. The
user's inline card still read `model: gpt-image-2-2k` while displaying
a locally composited image, and the reflection wrote "prefer rembg over
`vibe_seller_generate_image`" into store knowledge — which would have
made every later image task do the same. Hence the companion rule in
the tool description and `amazon-image-studio` §3: **if you cannot
actually see the image, hand it to the user to judge** (it renders
inline) — never substitute a pixel-measurement proxy, and never act on
what one measured.

## Testing / VISION_FAKE

`VISION_FAKE=1` short-circuits the kie.ai network and returns a
deterministic placeholder PNG, so the whole confirm→save→display path is
exercised offline and for free. Used by the workflow tests
(`tests/workflow/test_wf_vision_image.py`) and the Playwright e2e
(`tests/e2e/test_vision_image_ui.py`). Unit tests:
`tests/unit/test_vision.py`.
