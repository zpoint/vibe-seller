# Backend

Python FastAPI backend serving the REST API, managing browser sessions, and executing tasks.

## Entry Point

- **`main.py`** — FastAPI app setup, CORS middleware, router registration, lifespan (DB init on startup)
- **`config.py`** — Paths (`BASE_DIR`, `DATA_DIR`, `SCREENSHOTS_DIR`, `DB_PATH`), `DATABASE_URL`, default user constants, env vars
- **`database.py`** — SQLAlchemy async engine, session factory, `init_db()` (creates tables + seeds default user), `get_db()` dependency

## Modules

| Directory | Purpose |
|-----------|---------|
| `models/` | SQLAlchemy ORM models (DB schema) |
| `models/base.py` | SQLAlchemy declarative `Base` (decoupled from `database.py` to break circular imports) |
| `task_runner.py` | Task prompt assembly + execution pipelines (extracted from `routers/tasks.py`) |
| `schemas/` | Pydantic request/response schemas |
| `routers/` | FastAPI route handlers (API endpoints) |
| `browser/` | Pluggable browser backends + session management |
| `ai/` | AI agent abstraction (Claude Code CLI backend); provider-preset catalog (`provider_presets.py`) and its auto-sync from `main` (`provider_presets_sync.py`, cache `~/.vibe-seller/cache/provider_presets.json`, env `PROVIDER_PRESETS_URL`; see [Auto-synced preset catalog](#auto-synced-preset-catalog-aiprovider_presets_syncpy)); per-model token prices + context windows for the usage meter (`model_pricing.py`, generated `model_pricing_snapshot.py`, `model_pricing_sync.py`) and the per-call meter itself (`usage_meter.py`; see [Model pricing & usage meter](#model-pricing--usage-meter-aimodel_pricingpy)) |
| `vision.py` / `vision_catalog.py` / `vision_catalog_sync.py` / `vision_models.py` | Image generation via kie.ai: config + client + confirm registry (`vision.py`); the curated model catalog as literal data (`vision_models.py`); the served catalog — validation + served state (`vision_catalog.py`) and the network steps of its `vision_catalog` sync job (`vision_catalog_sync.py`) — catalog from `main` (literal parse + schema validation), live per-tier prices from kie.ai's public pricing table, availability via `GET /api/v1/models` (deny-list), cache `~/.vibe-seller/cache/vision_catalog.json`, env `VISION_CATALOG_URL`; runs only with a kie.ai key. See [vision.md § Kept current](vision.md#kept-current-by-the-vision_catalog-sync-job) |
| `executor/` | Legacy browser task runner (deprecated — browser automation now via browser-use CLI) |
| `events/` | SSE event bus for real-time streaming |
| `events_system/` | Business event extraction + sync backends |
| `channels/` | Message channel integrations (WeCom self-built app) |
| `agent_mcp/` | The hosted MCP endpoint (`POST /api/mcp`, 32 tools): JSON-RPC dispatch, tool registry, lifecycle/status guidance, and one thin adapter module per tool family — every adapter calls the REST route handlers via `call_route`, never the DB directly. See [api.md § Hosted MCP endpoint](api.md#hosted-mcp-endpoint) and [agents.md](agents.md). |
| `notifiers/` | Chat bot webhook senders (WeCom / DingTalk / Feishu / Slack) — `base.py` holds the `PLATFORMS` registry + `detect_platform` (platform is derived from the URL host, never client-supplied); one module per platform; never echoes the webhook URL in errors |
| `scheduler/` | Task scheduling (cron, fan-out, concurrent per-store queue) |
| `workspace/` | Workspace management (~/.vibe-seller/) |
| `knowledge/` | Project knowledge files (synced to workspace) |
| `skills/` | Built-in skills (synced to workspace) |
| `prompts/` | LLM system prompts (loaded at import time) |
| `plugins.py` | Plugin framework (IoC registry) — core reads gates/guards/backends/skills/services from here instead of hardcoding them. See [Plugin Framework](#plugin-framework). |
| `builtin_plugin.py` | The OSS "builtin plugin" — registers every core contribution through the plugin API. |
| `utils/` | Shared utilities (crypto, etc.) |
| `api_tokens.py` | Personal API tokens (`vst_…`): generation, SHA-256 hashing, expiry/revocation rules. Rows live in `api_tokens`; the plaintext is returned exactly once, at mint. |
| `api_token_secrets.py` | The REST side of the write-only-secret contract for API-token callers: one ASGI middleware that masks secret-classified fields in JSON responses and restores masked echoes on write routes (`RESTORE_RULES`). See [api.md § API tokens](api.md#api-tokens-personal-access-tokens). |
| `interactive_only.py` | Routes only a person at the web UI may call (today: `PUT /api/email-accounts/{id}/send-permission`). `INTERACTIVE_ONLY_ROUTES` + `InteractiveOnlyMiddleware`, registered in `main.py` next to the API-token scope middleware, refuse every machine credential (any `Authorization` header, a `vst_` cookie, the `ai_bot` cookie JWT). Agent-proof only while login is on. See [api.md § Email accounts](api.md). |
| `uploads.py` | One definition of what may be uploaded and how big: MIME allow-list, extensions, `MAX_UPLOAD_SIZE` (100MB), the streaming `save_upload()` writer, and `UploadBodyLimitMiddleware`. Every multipart route imports from here — the limit used to live in four places and had already drifted. See [Upload limits](api.md#upload-limits). |
| `text_utils.py` | Surrogate-safe text: `SafeStr` (request fields), `SafeText` (DB columns), `sanitize_text` (file writes). Agent text can carry a lone surrogate that no UTF-8 encode accepts — see [Lone surrogates](#lone-surrogates-in-agent-text). |
| `platform.py` | Cross-platform abstractions (Windows/macOS/Linux) — psutil-based process management (`kill_process`, `find_processes_by_pattern`, `reap_task_agents`, `collect_agent_descendants`), venv path helpers (`Scripts/` vs `bin/`, `venv_python`, `venv_executable`), `prepend_to_path` (uses `os.pathsep`), `safe_chmod` (no-op on Windows). Centralises every platform difference so the rest of the code stays platform-agnostic. See [Cross-platform support](subsystems.md#cross-platform-support-native-windows). |

### Shutdown cleanup — reaping task agents

A running task is a `claude -p` subprocess tree (claude → MCP server →
`skill_cli.daemon` → browser-use). Stopping the server must take that
whole tree down; a survivor keeps calling `POST /stores/<id>/browser/start`
on the next server and, because the anti-detect browser client is a
single shared process, competing survivors thrash it. Two layers ensure
no orphans:

- **`ClaudeCodeBackend.stop_all()`** (`ai/claude_backend_manager.py`) —
  stops every running `AgentSession` (each `stop()` killpg's its subtree;
  Windows uses `taskkill /F /T` via `process_utils.taskkill_tree`).
  Wired into `main.py`'s `lifespan` shutdown so a graceful SIGTERM never
  orphans an agent.
- **`platform.reap_task_agents(pids=None)`** — a process-level backstop
  for the paths that bypass graceful shutdown (a SIGKILL of the server;
  Windows `TerminateProcess` from `vibe-seller stop` / the tray
  quit-restart). Matches the headless-agent, `skill_cli.daemon`, and
  running-wrapper signatures and kills each with its child tree.
  `vibe-seller stop` scopes it with `collect_agent_descendants(server_pid)`
  (snapshotted before the kill) so a second server instance's agents are
  left alone.

## Request Flow

```
Client Request
    → FastAPI Router (app/routers/)
    → Pydantic Schema validation (app/schemas/)
    → SQLAlchemy Model CRUD (app/models/)
    → BrowserManager (app/browser/) if browser needed
    → AI Agent (app/ai/) for task execution
    → EventBus (app/events/) for real-time updates
    → SSE stream back to client
```

## Database Models

SQLAlchemy 2.0 ORM models. All extend `Base` from `app/database.py`.

| Model | Table | Description |
|-------|-------|-------------|
| `AppSettings` | `app_settings` | Key-value app settings (`auth_required`, `admin_credentials_set`, `max_agent_concurrency`, `default_schedule_phase_mode`, `default_schedule_timezone`, `task_retention_days`, `google_workspace_enabled`) |
| `User` | `users` | Team members with JWT auth. `username` (unique, required), `email` (unique, nullable, validated). Login by username or email. Admin seeds from env (`ADMIN_USERNAME`, `ADMIN_EMAIL`). Per-user prefs: `plan_mode_default`, `debug_mode`, `default_profile_id`, `sync_profile_to_schedules` (re-pin owned schedules on default-profile change — see [subsystems.md § Schedule AI profile resolution](subsystems.md#schedule-ai-profile-resolution)). |
| `ApiToken` | `api_tokens` | Personal API tokens (`vst_…`) for external agents: SHA-256 `token_hash` only (plaintext returned once at mint), owner FK to `users`, display `prefix`, `expires_at` (null = never), soft `revoked_at`, throttled `last_used_at`. Deleting a user deletes their tokens. |
| `Store` | `stores` | E-commerce stores with browser config |
| `BrowserSession` | `browser_sessions` | Active browser sessions per store |
| `Task` | `tasks` | Units of work created by users |
| `TaskStep` | `task_steps` | Individual steps within a task execution |
| `Screenshot` | `screenshots` | Browser screenshot references (file paths on disk) |
| `TaskLog` | `task_logs` | Execution log entries |
| `TaskAttachment` | `task_attachments` | File attachments for tasks |
| `TaskMessage` | `task_messages` | Agent chat history per task |
| `ZiniaoAccount` | `ziniao_accounts` | Ziniao anti-detect browser accounts |
| `Event` | `events` | Business events (deadlines, campaigns, cases) |
| `EventActivity` | `event_activities` | Activity timeline entries per event |
| `Schedule` | `schedules` | Recurring task schedules (daily/weekly/monthly, store-specific or all-stores) |
| `ScheduleState` | `schedule_state` | Agent-managed cross-run cursor per schedule (`schedule_id`, `key`) → `value`. Lets one scheduled run persist a watermark (e.g. last processed email date) for the next run to resume from. |
| `EmailAccount` | `email_accounts` | Email accounts with Fernet-encrypted IMAP passwords; `allow_send` (default true; the user can turn it off per mailbox) gates agent sends — enforced in `app/email/sender.py` |
| `StoreEmailLink` | `store_email_links` | Many-to-many store↔email with watermark tracking |
| `AIProfile` | `ai_profiles` | AI agent configuration profiles |

### Key Fields

**Task**: `store_id`, `status`, `plan`, `result`, `todos` (JSON), `plan_mode`, `wait_condition` (JSON), `ai_profile_id` (FK), `batch_id`, `platform`, `country`

**Store**: `browser_backend` (`"chrome"` | `"ziniao"`), `browser_config` (JSON), `platforms` (JSON), `countries` (JSON), `platform_countries` (JSON — platform→country mapping)

**BrowserSession**: `store_id` (UNIQUE), `cdp_port`, `proxy_port`, `chrome_pid`, `status`

## Pydantic Schemas

Request/response schemas in `app/schemas/`. Key conventions:
- Schemas mirror model fields but handle JSON parsing (models store JSON as TEXT, schemas use native Python types)
- Response schemas use `Config.from_attributes = True` for ORM → schema conversion
- Request schemas only include user-provided fields (IDs and timestamps are auto-generated)
- **Any field carrying agent- or user-authored prose is `SafeStr`, not `str`** — see below

### Lone surrogates in agent text

Claude's `stream-json` occasionally emits an unpaired surrogate
(U+D800–U+DFFF). JSON escapes them losslessly, so the request body is
valid on the wire and `json.loads` hands back a `str` that Python
**cannot encode to UTF-8**. Everything downstream then dies with
`surrogates not allowed`, and the agent sees an opaque tool error it
cannot fix (the character is invisible in its own output), so it retries
the same text.

Two layers, because there are two ways in:

| Layer | Covers |
|---|---|
| `sanitize_json` in `mcp_server.call_api` | The agent's actual ingress. `httpx` serializes `json=` with `ensure_ascii=False` and then encodes UTF-8, so a surrogate raises **inside the MCP process** and no request is ever made — the server-side guards below would never see it |
| `SafeStr` on request fields | Everything a request touches — including consumers that are *not* writes: the result gates hand the text to a language detector (a Rust extension) that encodes to UTF-8, so a submission crashed **before** any persistence. Also covers clients that are not our MCP server (the UI, curl) |
| `SafeText` on DB columns | Text that never passed through a request body — the stream reader persisting `task_messages`, plan text, internally assembled prose |

File writes are sanitized inside the managers that own them
(`workspace/manager.py`, `workspace/skills_manager.py`).

Composing `SafeStr` with `StringConstraints` has one trap:
`Annotated[SafeStr, StringConstraints(...)]` silently **drops** the
constraint. Wrap the constrained type instead —
`Annotated[Annotated[str, StringConstraints(...)], SANITIZE_SURROGATES]`
(see `routers/tasks_schedule_state.py`).

## External Config Detection (`ai/external_config.py`)

Detects when an external tool (e.g. cc-switch — https://github.com/farion1231/cc-switch) has written `ANTHROPIC_*` env entries into `~/.claude/settings.json`. Claude Code applies those entries with **higher precedence than the subprocess env our agent passes at spawn time**, so without this guard, a user's AI-profile selection silently becomes a no-op and the agent runs against whatever endpoint the external tool configured.

| Symbol | Purpose |
|---|---|
| `detect_claude_settings_overrides()` | Returns the sorted list of `ANTHROPIC_*` keys present in `settings.json`'s `env` block. Prefix-match (no hardcoded enumeration) so a future Anthropic env var is caught automatically. |
| `assert_profile_compatible(profile_id)` | Raises `ExternalConfigOverrideError` if `profile_id` is non-default *and* the env block has any `ANTHROPIC_*` keys. No-op for the default profile (the documented escape hatch — lets the external tool fully own routing). |
| `ExternalConfigOverrideError.to_api_detail()` | Structured `HTTPException.detail` payload (`code`, `profile_id`, `overriding_keys`, `settings_path`, `clear_command`, English-fallback `message`). The frontend renders this in the user's locale via `errors.externalConfigOverride.*` i18n keys (see `frontend/src/components/ExternalConfigOverrideModal.tsx` and `ExternalConfigOverrideErrorCard.tsx`). |

### Wired into

- `routers/profiles.py` — POST `/api/profiles`, PUT `/api/profiles/{id}`, PATCH `/api/profiles/{id}/set-default` all return HTTP 409 with the structured detail when a non-default profile is selected while overrides exist.
- `task_runner_auto.auto_run_task` — fails the task fast with `error_category='external_config_override'` and the JSON-encoded detail in `task.error` *before* the agent is spawned. The frontend's `ExternalConfigOverrideErrorCard` parses the JSON and renders the localized template on the failed-task panel.

### Test surface

- `tests/unit/test_external_config.py` — detection across no-settings / no-env / malformed JSON / non-`ANTHROPIC` keys / single + multiple overrides; default-profile escape hatch; load-bearing pieces of the user message; future-key prefix-match.
- `tests/workflow/test_wf_external_config_override.py` — end-to-end across the profile router (409) and the task runner (fail-fast).

## Profile Endpoint Validation (`ai/profile_validation.py`)

Every non-`default` profile routes the agent through an Anthropic-compatible endpoint declared entirely in the profile's `env` (`ANTHROPIC_BASE_URL` + a token + `ANTHROPIC_MODEL`). Nothing used to check that those three actually work together, so a typo'd base URL, a wrong/expired key, or a model id the provider has since retired all saved cleanly and only surfaced later as an opaque agent error. This module lets the config page probe the config on save.

`validate_profile_env(env, *, client=None)` (async) makes one minimal `/v1/messages` request against the profile's own endpoint and returns a `ProfileValidationResult`:

| Field | Meaning |
|---|---|
| `ok` | The gate the router/UI enforces. |
| `code` | Stable machine-readable outcome: `ok`, `no_endpoint` (native Claude — no base URL, no-op pass), `missing_key`, `missing_model`, `unreachable`, `auth`, `not_found`, `http_error`, `protocol`. |
| `error` | Human-facing message, often the provider's own text (e.g. a retired model id) — the stale-model signal. |
| `reported_model` | The `model` string the endpoint echoed back on success. Reveals silent fallbacks: lenient providers (e.g. MiniMax) 200 on an unknown model and report the real served model instead of rejecting. |

Design notes:

- Auth mirrors Claude Code so a pass here means a pass at runtime: `ANTHROPIC_AUTH_TOKEN` → `Authorization: Bearer`, `ANTHROPIC_API_KEY` → `x-api-key` (both if both set). The token is **never logged**.
- No `ANTHROPIC_BASE_URL` → no-op pass (`no_endpoint`): the native-Claude/`default` shape has no third-party endpoint to probe.
- `client` is injectable so unit tests drive it with an `httpx.MockTransport`.

### Wired into

- `routers/profiles.py` — `POST /api/profiles/validate` calls it and returns `result.to_dict()` (always HTTP 200; verdict in the body). Persistence-free — the endpoint never writes a profile. See [api.md](api.md#profilespy--ai-agent-profiles).
- `frontend/src/components/ProfileModal.tsx` — the save handler awaits `/api/profiles/validate` first and blocks the save (showing `error`) when `ok` is false.

### Provider presets & model options (`ai/profiles.py`)

`PROVIDER_PRESETS` holds the per-provider env template (base URL, timeouts, per-tier model aliases). `PROVIDER_MODELS` holds the config UI's model dropdown per provider — each option is `{id, label, context?, vision?}`; the first entry is the default and must equal the preset's `ANTHROPIC_MODEL` (pinned by a test). `get_provider_presets()` merges the two for the UI — reading the CURRENT catalog (auto-synced cache or bundled, see below), never the module dicts directly. Both are convenience shortlists (the UI also offers free-text "Custom"), and every choice is endpoint-validated on save, so a stale entry is caught rather than silently breaking a run.

A preset may also carry `group` + `variant`: presets sharing a `group` collapse into one top-level button in the picker that reveals a variant sub-row, instead of cluttering the flat list. The rule: **every official region / payment-path combination that needs a different host or model is its own variant**, so a user never hand-edits `ANTHROPIC_BASE_URL` or a model name. Current groups:

| Group | Variants (preset id) |
|---|---|
| `Kimi` | Kimi Code (membership) (`kimi`), Pay-as-you-go (China) (`kimi_paygo`), Pay-as-you-go (International) (`kimi_paygo_intl`) |
| `MiniMax` | China (`minimax`), International (`minimax_intl`) — one host per region serves both pay-as-you-go and M Plan keys |
| `GLM` | China (`glm`), International (`glm_intl`) — likewise one host for both key types |
| `Alibaba Cloud` | Pay-as-you-go (China) (`qwen`), Pay-as-you-go (International) (`qwen_intl`), Token Plan (China) (`qwen_token`), Token Plan (International) (`qwen_token_intl`), Coding Plan (China / International, existing subscribers only) (`qwen_coding`, `qwen_coding_intl`) — last because Alibaba no longer sells Coding Plan to new subscribers |
| `MiMo` | Pay-as-you-go (`mimo`), Token Plan China / Singapore / Europe cluster (`mimo_token`, `mimo_token_sgp`, `mimo_token_ams`) |

Each preset also carries `purchase_url` (the provider's own console page to buy, top up or create a key) and `guide_url` (`{zh, en}` → that provider's section of our [Get an API Key](llm-keys.md) docs page). The modal shows both as links under the API key field. Every display string (`name`, `group`, `variant`, model `label`) is plain English in the catalog, written as `Head` or `Head (term, term)`. The UI translates it into the current language (`frontend/src/components/presetI18n.ts`, terms in `profiles.presetTerms`), and `tests/unit/test_preset_i18n.py` fails when a new English term has no Chinese entry.

`deepseek` stays standalone (one host). Preset ids are never renamed or reused — existing profiles, telemetry's `profile_kind` and the presets sync key off them — and every preset's base URL is unique (`profile_kind` maps a profile back to its preset by base URL), both pinned by `tests/workflow/test_wf_profiles.py`. `app/ai/provider_presets.py` is excluded from the 800-line pre-commit limit: the sync on every released client fetches that one file and needs both literals in it, so it cannot be split.

`vision` is **verified, not casually web-sourced**. Where an account key exists it is **live-probed**: each id was sent a 64×64 solid-red **and** solid-blue image and had to name both correctly, three times. Latest results (2026-10-29): `glm-5.3-flash` (Z.AI), `deepseek-flash`, `MiniMax-M3.1-Flash`, and `MiniMax-M3` (api.minimax.cn) read images → vision; `glm-5.3` and `glm-5.2` misname the colors and `qwen3.7-max` 400s on images → text-only, matching DeepSeek's pricing table (`deepseek-flash` 图像理解 supported, `deepseek-v4-pro` not). The earlier live-verified Qwen results (`qwen3.7-plus` / `qwen3.6-flash` / the Qwen-VL series → vision) still stand; `qwen3.8-max` / `qwen3.8-flash` (pay-as-you-go and China Token Plan) and the China Token Plan's `auto` router read images 6/6 (2026-11-10) → vision; the international Token Plan's `auto` has no key, so it stays unlabelled. Where no key exists, the label is set only from **first-party evidence**: **Kimi** `k3.1-pro` / `k3` / `kimi-for-coding` → vision (Moonshot's official vision guide lists `kimi-k3.1-pro` / `kimi-k3` / `kimi-k2.7-code` — doc-verified, not live: the account's Kimi Code subscription lapsed), and **MiMo** `mimo-v2.6-pro` / `mimo-v2.6-flash` / `mimo-v2.6-pro-ultraspeed` → vision (MiMo's image-understanding guide lists all three v2.6 chat models as supporting image input, and its Anthropic endpoint takes base64/url image blocks — doc-verified, not live: no MiMo key on this machine). `context` is doc-sourced (a 1M window isn't practical to probe) and is the model's OWN window. The chip badge instead shows the window Claude Code will actually use under the profile (`frontend/src/components/contextWindow.ts`): `CLAUDE_CODE_MAX_CONTEXT_TOKENS` if set, else 1M for an id ending in `[1m]`, else the 200K default, capped at `context`. A bare id whose `[1m]` twin is also listed, such as `MiniMax-M3.1-Flash` or `deepseek-flash`, therefore reads 200K, and its label says so. Add a key for a doc-verified provider and re-run the probe to upgrade it to live-verified.

A provider only gets a preset if its **own** docs ship an Anthropic-Messages-compatible endpoint for Claude Code — no third-party gateways. That bar is why **xAI/Grok is absent**: `POST https://api.x.ai/v1/messages` does answer Anthropic-shaped requests, but xAI's own REST reference marks it "Deprecated: The Anthropic SDK compatibility is fully deprecated. Please migrate to the Responses API or the gRPC API." and xAI publishes no Claude Code guide (its official Claude Code integration is the Grok Build CLI + `grok-build-plugin-cc`, which shells out to a local binary rather than re-formatting the API). Google Gemini and OpenAI are absent for a plainer reason: they ship no Anthropic-Messages endpoint for their own models at all — `POST https://api.openai.com/v1/messages` and `POST https://generativelanguage.googleapis.com/v1/messages` both 404. (Google's Anthropic-format endpoints on Google Cloud serve **Claude**, not Gemini.)

### Auto-synced preset catalog (`ai/provider_presets_sync.py`)

The catalog above is not frozen at release time. A background loop
pulls `app/ai/provider_presets.py` from the project's `main` so a
running install picks up new providers without an upgrade — and the
fetched unit is the **`.py` file itself** (parsed with `ast` +
`ast.literal_eval`, never executed or imported), so the provenance
comments stay the single source of truth instead of being lost to a
JSON mirror.

**Serve path** (no network on a request): `current_presets()` /
`current_models()` serve the in-memory catalog, seeded at first use
from `~/.vibe-seller/cache/provider_presets.json` and falling back to
the bundled dicts when that cache is missing, corrupt or fails
re-validation. `GET /api/profiles/presets`, the telemetry
`profile_kind()` lookup and `model_sees_images()` all read it, so the
Settings → AI picker shows the synced catalog the moment it lands.

**Fetch path**: the `presets` job of the central sync scheduler
(`app/sync/jobs.py`) — first run at boot, then every 48 h (the job's
`sync_state` row computes the next run). The fetch is a **conditional
GET**: the cached `ETag` goes back as `If-None-Match` (only while the
cache is valid, is what the accessors serve, and was fetched from the
current URL), and a `304` is an `unchanged` no-op — nothing read, cache
mtime untouched. Servers without a usable `ETag` (mirrors,
`http.server`) fall back to the body hash: a body whose sha256 matches
the cache is not rewritten. Anything else is validated, written
atomically (tmp + rename) and swapped in. The bookkeeping — body sha256,
`ETag`, source URL — lives in the `presets` job's `sync_state` row; the
cache file is catalog data and nothing else. A changed validator or
source URL on an otherwise identical body refreshes just that metadata
so the cheap path can resume. A `304` to a
request that carried no `If-None-Match` proves nothing and is treated as
a failure — the install keeps what it serves.

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER_PRESETS_URL` | raw.githubusercontent copy of `app/ai/provider_presets.py` on `main` | Where the catalog is fetched from (a mirror or a local test server) |
| `VIBE_SYNC_DISABLED` | (unset) | `1` registers no sync jobs at all — what `tests/conftest.py` sets so nothing under `tests/` reaches GitHub |
| `VIBE_SYNC_CADENCE_OVERRIDE` | (unset) | `name=seconds,...` — compress named sync jobs (tests) |
| `VIBE_SYNC_JITTER` | `1` | Scales the per-install stagger; `0` zeroes it |

**Remote content configures API endpoints, so it is treated as
hostile.** Per preset: env keys are allowlisted (no `ANTHROPIC_AUTH_TOKEN`
/ `ANTHROPIC_API_KEY`, no `HTTPS_PROXY` / `NODE_OPTIONS`, no
Bedrock/Vertex switch), `ANTHROPIC_BASE_URL` must be `https` with a host
and no userinfo (`{WorkspaceId}` placeholders stay legal),
`purchase_url` / `guide_url` links must be `https` with no userinfo and
no placeholder (only `zh` / `en` guide languages are kept),
`load_global_mcp` can never turn on, unknown fields are dropped, and the
model dropdown must agree with the preset's `ANTHROPIC_MODEL` (first
entry). A preset that fails falls back to the BUNDLED copy of the same
id; the bundled catalog is also the FLOOR for ids the body omits, so a
stale or partial `main` can never unpick a provider the release shipped
(ids only `main` ever had do live and die with `main`). A body that
parses but yields nothing valid, is oversized (512 KB cap) or is not
literal data is rejected whole — the install keeps what it was serving.
The cache is re-validated on load for the same reason. Residual trust
assumption (documented in the module docstring): the source is the
project's own `main`, i.e. the same trust as a release — and presets
only ever seed **newly created** profiles, never retargeting saved ones.

### Test surface

- `tests/unit/test_profile_validation.py` — the full verdict matrix via `httpx.MockTransport` (success/auth/not-found/stale-model/unreachable/wrong-protocol/non-JSON/missing-key/missing-model, trailing-slash join, header mapping).
- `tests/workflow/test_wf_profiles.py` — the `/validate` endpoint contract (200, no persistence) and the model-options contract.
- `tests/ai/test_profile_validation_live.py` — `ai`-marked, against real vendor endpoints using CI's `{PROVIDER}_API_KEY` secrets: good key/model → ok, bad model → rejected or reports-a-different-model, bad key → rejected (each provider self-skips without a key).
- `tests/unit/test_provider_presets_sync.py` — the bundled file round-trips through the literal parser, every bundled env key is allowlisted, each hostile-body case (non-https/userinfo URL, credential or proxy env key, `load_global_mcp=True`, control chars, non-literal code, oversize body, first-model mismatch, unknown fields) is dropped or rejected, the bundled-fallback/floor rules, cache re-validation, and the loop (mocked `httpx.MockTransport`, ~0.05 s interval: repeated ticks, changed-body update, unchanged-body no-op, 500/timeout/garbage keep the previous data, clean cancellation).
- `tests/workflow/test_wf_presets_sync.py` — `GET /api/profiles/presets` serves the synced catalog, follows a changed body, and falls back to bundled on a corrupt cache or a rejected body.

## Model pricing & usage meter (`ai/model_pricing.py`)

Prices the live usage meter shown in the task footer. Same
cache-or-bundled pattern as the [preset catalog](#auto-synced-preset-catalog-aiprovider_presets_syncpy).

| Piece | Role |
|---|---|
| `ai/model_pricing.py` | Validation (`slice_catalog` / `normalize_catalog`), served state, lookup (`price_for`) and the cost math (`cost_usd`). `PRESET_PROVIDERS` maps every provider preset id (plus `default` → `anthropic`) to ONE models.dev provider; a test fails for a preset without an entry. |
| `ai/model_pricing_snapshot.py` | Generated data: the models.dev slice this release ships (offline floor). Regenerate with `scripts/gen_model_pricing_snapshot.py`; excluded from the line-limit hook. |
| `ai/model_pricing_sync.py` | The DAILY `model_pricing` job on the central sync scheduler: fetch `https://models.dev/api.json` (env `MODEL_PRICING_URL`), keep only tracked providers' `limit.context` + `cost`, cache to `~/.vibe-seller/cache/model_pricing.json`. A body with fewer than half the tracked providers is rejected; the served catalog is kept. |
| `ai/usage_meter.py` | Per-process meter fed every stream-json event; returns the token / cost delta of each API call (deduplicated by message id). |
| `task_usage.py` | Adds a delta to the task row and emits the `task_usage` SSE event. |

**`cost_basis`** (on `TaskResponse` and `task_usage`):

- `catalog` — priced from the run's provider rates. Each bucket is
  charged at its own rate; a long-context tier applies to the whole
  call; one-hour cache writes are charged at 2x input.
- `included` — the provider's rates are all zero (a subscription plan:
  Kimi Code, Coding / Token Plans). Tokens are counted, cost is 0.
- `unpriced` — the model is not in the catalog (a custom profile).
  Tokens only.

Why per call and not the CLI's `total_cost_usd`: that figure is
cumulative for the process and prices every model as Claude. Details:
[subsystems.md § Task usage meter](subsystems.md#task-usage-meter--price-catalog-model_pricing).

## Plugin Framework

`app/plugins.py` is the inversion-of-control seam: **core knows no
customer.** Instead of importing customer gates/guards/backends, core
reads them from an `ExtensionContext` that plugins populate at startup.
Removing a customer = not installing its wheel — zero core edits,
nothing to re-leak. This is PR-1 of the public-OSS / private-plugin split
(design-of-record: `plugin_design_v2.md`).

### Contract

| Symbol | Purpose |
|---|---|
| `Plugin` (ABC) | A plugin subclasses this and implements `install(ctx)`. Optional `load_contexts`, `name`, `version`. |
| `ExtensionContext` | What `install` writes to. `register_gate`, `register_pretool_gate`, `register_browser_backend`, `register_skill_source`, `register_prompt_fragment`, `register_service`, `register_router`, `register_frontend_bundle`. |
| `load_plugins(ctx)` | Loads the builtin (direct import, **fail-closed** — a missing builtin aborts startup) then external plugins (logged-and-skipped on failure — **fail-open** so one bad wheel can't take the server down). |
| `get_extension_context()` | Process-wide singleton; loads plugins once, lazily (so app-less unit tests that hit the gate path still see a populated registry). |
| `registered_gates()` / `registered_pretool_gates()` / `registered_browser_backends()` / `registered_skill_sources()` | Convenience accessors core call sites read. |

Registration is **declarative** — `install` only records into `ctx`; it
never touches the live FastAPI app. App-level effects are applied
separately: `main._wire_plugins` mounts plugin routers / frontend-bundle
routes at module load; the lifespan starts/stops background services.

### Discovery & isolation

Per-customer isolation is at **pack/install** time: each customer's
deployment installs only that customer's plugin wheels, so other
customers' code is absent on the box. The on-box loader then just loads
whatever is installed:

- the OSS **builtin** (`app/builtin_plugin.py:BuiltinPlugin`) by direct
  import — always present, no entry point / reinstall needed;
- external plugins via the `vibe_seller.plugins` entry-point group
  their wheels declare (sorted by name; deterministic order).

### What it replaced

`stop_gates.get_registered_gates`, `bash_safety.first_bash_deny`, and
`BrowserManager._get_backend` previously hardcoded their lists (and
imported customer gates / a money-transfer guard / browser-backend
classes directly). They now read from the registry. The `BuiltinPlugin`
registers only core's own, customer-agnostic contributions; customer
gates/guards/skills arrive via externally-installed plugin wheels.

Test surface: `tests/unit/test_plugins.py`.

## Turn Lifecycle (process-per-turn model)

A **turn is one CLI process**, and the turn ends when the process
exits. The `claude` CLI never exits on its own while stdin is open
(spike-verified), so **closing stdin is the turn terminator** — owned
by the quiescence watchdog in `ai/claude_backend_turns.py`
(`_TurnLifecycleMixin`, ticked from the stream's readline-timeout
branch).

**When stdin may close (soft linger)** — ALL of:
- an accepted result exists for the current turn (`_turn_result_seen`),
- the review/exec gates pass (same composite + fail-open budget as the
  result branch),
- no tracked async work is pending — **async subagents (Agent tool) OR
  background shell commands** (Bash/PowerShell auto-backgrounded past the
  blocking budget or `run_in_background`). Both register in `_async_agents`
  (`ai/claude_backend_subagents.py::_track_async_agents`): subagents on
  their "Async agent launched" ack, shells on their "…background… with
  ID: <id>" ack, each cleared on its `<task-notification>` completion. So
  a turn does not end while a `find`/export it launched is still running,
  and that command finishes + is incorporated instead of being orphaned,
- no AskUserQuestion is parked and not in the planning phase,
- the stream has been idle for the tier window.

**Never** while a question is pending or plan approval is parked.

**A process hosts many turns** (gate redrives, `<task-notification>`
continuations, injected follow-ups): every accepted execute-phase
result event is a turn boundary that emits its own `role='result'`
card, and the LAST non-empty one is the deliverable (`task.result`;
an explicit `set_task_result` still wins). `_last_result_is_error` is
per-result and folded into `_is_error_result` at process exit, so a
turn that recovered from an intermediate error ships COMPLETED while
forced errors (circuit breaker, rc≠0) stay FAILED.

**Backstops** (both strictly tighter than the pre-migration design):
- hard idle — total stream silence for `VIBE_TURN_HARD_IDLE_S` closes
  regardless of gate/async holds (a live *subagent* streams events
  through the parent, so silence that long means the work is dead);
- post-close kill escalation — a process still alive ~120s after any
  close is `_force_kill()`ed and its stall-reaper heartbeat stops
  (bounds the GLM stall-after-result wedge).

**Runaway background command backstop.** The hard-idle "silence = dead"
premise holds for subagents (they stream) but NOT for background shell
commands (a `sleep`/`find` emits nothing to the stream, so its liveness
is invisible). A background command that never completes is therefore
force-closed by hard idle *while still running* and killed with no
completion record. Resuming that session on the next follow-up would
make the CLI inject a reconciliation `<task-notification>` that collides
with the follow-up and aborts the turn's tool calls (`CANCEL_MESSAGE`) —
a confirm-gated `generate_image` then dies before its gate. So a
follow-up starts a **fresh session** (compacted history, not `--resume`)
when the prior session has an orphaned background task
(`workspace/manager.py::session_has_orphaned_bg_task`, wired in
`routers/tasks_conversation.py::send_task_message`). Two layers: the
turn-level wait prevents orphans for commands that finish; this contains
the orphan a genuine runaway still leaves.

**Follow-up delivery**: `send_user_message` returns a delivery bool
and opens a new turn; on a missed write (the message raced the turn
terminator) the conversation router falls through to the
resume/fresh-session spawn, so the message is re-routed, never
silently dropped.

| Variable | Default | Description |
|----------|---------|-------------|
| `VIBE_TURN_LINGER_S` | `60` | Soft-linger window (s) when async work (subagents or background shell commands) was launched this process. `0` = close at the result event (legacy behavior) |
| `VIBE_TURN_LINGER_QUIET_S` | `5` | Soft-linger window (s) for processes with no async work |
| `VIBE_TURN_HARD_IDLE_S` | `600` | Close after this much total stream silence regardless of holds. `0` = disabled |

Browser lifecycle (see docs/browser.md § Browser Lifecycle):

| Variable | Default | Description |
|----------|---------|-------------|
| `VIBE_BROWSER_IDLE_S` | `300` | Idle window before a browser with no live task is terminated. `0` = never |
| `VIBE_TAB_CAP` | `12` | Max tabs one task may keep open; oldest closed beyond it. `0` = unbounded |
| `VIBE_BROWSER_RELAUNCH_MAX` | `3` | Dead-mux full-env relaunches allowed per store per window before `start_session` refuses. `0` = unbounded |
| `VIBE_BROWSER_RELAUNCH_WINDOW_S` | `600` | Sliding window for the relaunch budget above. `0` = unbounded |
| `VIBE_BROWSER_START_TIMEOUT_S` | `180` | Ceiling on one store's `backend.start()`. It runs under a global lock, so an unbounded per-store retry loop starves every other store. `0` = unbounded |

Logging (`app/logging_setup.py`) — the backend log rolls on size **or**
age, whichever comes first, keeping `LOG_BACKUP_COUNT` numbered backups
(`backend_<port>.log.1`, …). On-disk ceiling is
`LOG_MAX_BYTES × (LOG_BACKUP_COUNT + 1)`; defaults give 10 GiB / 2 weeks.
`--dev` (`LOG_LEVEL=DEBUG`) is what makes this matter — DEBUG chatter from
`aiosqlite`/`httpcore`/`websockets` once grew an unrotated log to 5 GB.
`start.sh` applies the same policy to `backend_stdout_<port>.log`
(uvicorn's stdout/stderr, captured by a shell redirect the handler
can't reach). Both names are per-port — the app's own
`backend_<port>.log` and the shell capture — so two servers started
from one checkout never write or truncate the same file; a default-port
server's logs are `logs/backend_7777.log` and
`logs/backend_stdout_7777.log`.

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | `--dev` sets `DEBUG` |
| `LOG_MAX_BYTES` | `5368709120` (5 GiB) | Roll when the log would exceed this. `0` = no size trigger |
| `LOG_ROTATE_DAYS` | `7` | Roll when the log is this old. `0` = no age trigger |
| `LOG_BACKUP_COUNT` | `1` | Old logs kept; the oldest is deleted as backups shift down. `0` = keep none |

Setting both `LOG_MAX_BYTES=0` and `LOG_ROTATE_DAYS=0` disables rotation
(plain unbounded `FileHandler`).

## Configuration

All config in `config.py`. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///$HOME/.vibe-seller/data/vibe_seller.db` | Database connection (computed at runtime via `Path.home()`, not settable via env) |
| `SECRET_KEY` | Auto-generated | JWT signing key (env: `SECRET_KEY`) |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Use `./start.sh --dev` to set `DEBUG` + `AGENT_DEBUG=1` |
| `CORS_ORIGINS` | frontend dev/prod + localhost URLs | The single source of the browser origins this server answers as CORS-allowed — used by `CORSMiddleware` in `main.py` AND by the hosted MCP endpoint's cross-site guard (`app/agent_mcp/router.py`), so the two can never disagree. |

## Database

SQLite with async access via `aiosqlite`. Tables auto-created on startup via `Base.metadata.create_all`. No Alembic migrations — schema managed directly by SQLAlchemy model definitions.

Auth: JWT httpOnly cookie (24h rolling expiry — rolled forward on activity; the 7-day TTL is the service tokens, not browser sessions). Login is **optional** (default off). When `auth_required` is `false` (DB setting), all endpoints return the default admin user without authentication. When enabled, JWT validation applies as usual. The `auth_required` setting is seeded from `VIBE_AUTH_REQUIRED` env var on first boot only — once toggled in the UI, the env var is ignored. Admin credentials (`ADMIN_EMAIL`/`ADMIN_PASSWORD`) are also first-boot-only; once changed in the UI, env vars are ignored unless `FORCE_ADMIN_RESET=true` is set.

### Locked out: `vibe-seller reset-auth`

The seeded admin is `admin` / `admin@vibe-seller.local` / `admin` (`app/env_options.py`), valid only until someone changes the password in the UI — after that the env defaults are ignored and bcrypt keeps no copy of the old password. `FORCE_ADMIN_RESET=true` resets it, but the flag is applied by the **boot seed**, so it needs a server restart, and the agent tasks run as children of that server — the restart kills whatever was running.

`vibe-seller reset-auth` (implemented in `app/auth_reset.py`) is the third way in. It edits the app database directly, so the server stays up and running tasks are not interrupted:

| Command | Effect |
|---|---|
| `vibe-seller reset-auth status` | Whether a password is required, whether the seeded default still works, which accounts can log in |
| `vibe-seller reset-auth password` | Reset the default admin account's password (prompts for it) |
| `vibe-seller reset-auth password --user admin --rename` | Rename the default admin account back to `admin` and reset its password |
| `vibe-seller reset-auth disable` | Turn the login screen off — every request acts as the default admin |
| `vibe-seller reset-auth enable` | Turn the login screen back on |

`--db PATH` points at a database other than the app's runtime one. The action reads `app_settings` per request, so `disable` / `enable` take effect immediately (the frontend caches the flag — the browser needs a reload).

`--user` follows the login screen's own rule: an identifier containing `@` is an email, anything else is a username. A username is validated with the app's own rules (`app/schemas/user.py:validate_username`) before it is written, because the login screen routes an `@` to the email lookup — a username that broke either rule could never log in, and the recovery tool must not write one. An email identifies an account but cannot be renamed to.

A reset of the seeded admin also pins the `admin_credentials_set` setting: while that flag is unset, the boot seed re-applies `ADMIN_USERNAME` / `ADMIN_PASSWORD` on every start, which would undo the reset at the next restart. That guarantee has one exception the tool cannot fix from the outside: with `FORCE_ADMIN_RESET=true` in the server's environment, the boot seed overwrites the credentials **and deletes the pin** on every start, so the reset does not survive a restart. The command detects that and says so instead of promising otherwise; `status` reports it too. It reads the environment of the shell it runs in, which is not necessarily the server's — a launchd/systemd service can have its own, and there is no portable way to read it from outside.

A reset does **not** end existing sessions: the session is a stateless JWT in an httpOnly cookie with a 24h rolling expiry (the frontend keepalive rolls it forward on activity; 7 days is the service-token TTL, not the browser session), and nothing in it is tied to the password. To force every session to log in again, stop the server, delete `~/.vibe-seller/data/jwt_secret` (the next start regenerates it) and start it again — that also invalidates the tokens the agent's MCP client holds, which the tasks re-issue as they run. Deleting the file only helps when the secret comes from the file: an install that pins `JWT_SECRET` in the environment never reads it (`app/config.py:_resolve_jwt_secret`), so change or drop that variable instead.

Database file: `~/.vibe-seller/data/vibe_seller.db`. Delete to reset.

### Conventions

- All PKs are UUIDs generated via `uuid.uuid4()`, stored as TEXT
- Timestamps are ISO 8601 strings stored as TEXT
- JSON data stored as TEXT columns — serialized with `json.dumps()`, deserialized with `json.loads()`
- All models registered via `app/models/__init__.py` to ensure table creation on startup
