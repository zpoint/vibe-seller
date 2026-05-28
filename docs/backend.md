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
| `utils/` | Shared utilities (crypto, etc.) |

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
