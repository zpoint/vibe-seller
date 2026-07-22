# Vision — AI image generation

General-purpose image generation for tasks: product photos, marketplace
listing images, infographics, banners, or an image the user just wants,
via kie.ai (Nano Banana Pro / Nano Banana 2). The agent proposes; the
**user confirms and can edit** the prompt/model before anything is
generated; the result renders inline in the task stream and is saved in
the task workspace.

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
