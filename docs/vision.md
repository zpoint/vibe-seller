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
Image 2 (1K/2K/4K), GPT Image 1.5 (Medium/High); ByteDance — Seedream 5
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

## Testing / VISION_FAKE

`VISION_FAKE=1` short-circuits the kie.ai network and returns a
deterministic placeholder PNG, so the whole confirm→save→display path is
exercised offline and for free. Used by the workflow tests
(`tests/workflow/test_wf_vision_image.py`) and the Playwright e2e
(`tests/e2e/test_vision_image_ui.py`). Unit tests:
`tests/unit/test_vision.py`.
