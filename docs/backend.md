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
| `ai/` | AI agent abstraction (Claude Code CLI backend) |
| `executor/` | Legacy browser task runner (deprecated — browser automation now via browser-use CLI) |
| `events/` | SSE event bus for real-time streaming |
| `events_system/` | Business event extraction + sync backends |
| `channels/` | Message channel integrations (IMAP, WeCom) |
| `scheduler/` | Task scheduling (cron, fan-out, concurrent per-store queue) |
| `workspace/` | Workspace management (~/.vibe-seller/) |
| `knowledge/` | Project knowledge files (synced to workspace) |
| `skills/` | Built-in skills (synced to workspace) |
| `prompts/` | LLM system prompts (loaded at import time) |
| `plugins.py` | Plugin framework (IoC registry) — core reads gates/guards/backends/skills/services from here instead of hardcoding them. See [Plugin Framework](#plugin-framework). |
| `builtin_plugin.py` | The OSS "builtin plugin" — registers every core contribution through the plugin API. |
| `utils/` | Shared utilities (crypto, etc.) |
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
| `User` | `users` | Team members with JWT auth. `username` (unique, required), `email` (unique, nullable, validated). Login by username or email. Admin seeds from env (`ADMIN_USERNAME`, `ADMIN_EMAIL`). |
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
| `EmailAccount` | `email_accounts` | Email accounts with Fernet-encrypted IMAP passwords |
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

`PROVIDER_PRESETS` holds the per-provider env template (base URL, timeouts, per-tier model aliases). `PROVIDER_MODELS` holds the config UI's model dropdown per provider — each option is `{id, label, context?, vision?}`; the first entry is the default and must equal the preset's `ANTHROPIC_MODEL` (pinned by a test). `get_provider_presets()` merges the two for the UI. Both are convenience shortlists (the UI also offers free-text "Custom"), and every choice is endpoint-validated on save, so a stale entry is caught rather than silently breaking a run.

A preset may also carry `group` + `variant`: presets sharing a `group` (e.g. `Alibaba Cloud` → Pay-as-you-go China/International, Coding Plan, Token Plan; `GLM` → China/International) collapse into one top-level button in the picker that reveals a variant sub-row, instead of cluttering the flat list.

`vision` is **live-verified, not web-sourced**: each id (for providers with an account key) was probed by sending a 64×64 solid-red **and** solid-blue image and requiring the model to name both correctly, three times. Results as of this writing: `qwen3.7-plus` / `qwen3.6-flash` / the Qwen-VL series read images → vision; `qwen3.x-max` (400s on images), DeepSeek (returns empty), and MiniMax (replies "I cannot see images" / wrong color) → text-only. Kimi and GLM have **no account key to probe**, so their `vision` is deliberately **omitted** (no badge) rather than guessed. `context` is doc-sourced (a 1M window isn't practical to probe). If you add a key for an unverified provider, re-run the probe and set the flag.

### Test surface

- `tests/unit/test_profile_validation.py` — the full verdict matrix via `httpx.MockTransport` (success/auth/not-found/stale-model/unreachable/wrong-protocol/non-JSON/missing-key/missing-model, trailing-slash join, header mapping).
- `tests/workflow/test_wf_profiles.py` — the `/validate` endpoint contract (200, no persistence) and the model-options contract.
- `tests/ai/test_profile_validation_live.py` — `ai`-marked, against real vendor endpoints using CI's `{PROVIDER}_API_KEY` secrets: good key/model → ok, bad model → rejected or reports-a-different-model, bad key → rejected (each provider self-skips without a key).

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

## Configuration

All config in `config.py`. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///$HOME/.vibe-seller/data/vibe_seller.db` | Database connection (computed at runtime via `Path.home()`, not settable via env) |
| `SECRET_KEY` | Auto-generated | JWT signing key (env: `SECRET_KEY`) |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Use `./start.sh --dev` to set `DEBUG` + `AGENT_DEBUG=1` |

## Database

SQLite with async access via `aiosqlite`. Tables auto-created on startup via `Base.metadata.create_all`. No Alembic migrations — schema managed directly by SQLAlchemy model definitions.

Auth: JWT httpOnly cookie (7-day expiry). Login is **optional** (default off). When `auth_required` is `false` (DB setting), all endpoints return the default admin user without authentication. When enabled, JWT validation applies as usual. The `auth_required` setting is seeded from `VIBE_AUTH_REQUIRED` env var on first boot only — once toggled in the UI, the env var is ignored. Admin credentials (`ADMIN_EMAIL`/`ADMIN_PASSWORD`) are also first-boot-only; once changed in the UI, env vars are ignored unless `FORCE_ADMIN_RESET=true` is set.

Database file: `~/.vibe-seller/data/vibe_seller.db`. Delete to reset.

### Conventions

- All PKs are UUIDs generated via `uuid.uuid4()`, stored as TEXT
- Timestamps are ISO 8601 strings stored as TEXT
- JSON data stored as TEXT columns — serialized with `json.dumps()`, deserialized with `json.loads()`
- All models registered via `app/models/__init__.py` to ensure table creation on startup
