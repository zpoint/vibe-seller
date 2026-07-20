# API Routes

All routes prefixed with `/api/`. When `auth_required` is enabled (see Settings), all routes except `/api/auth/status`, `/api/auth/login`, `/api/health`, and `/api/sse` require JWT auth via httpOnly cookie. When `auth_required` is disabled (default), all routes return the default admin user without authentication.

## `auth.py` — Authentication

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/status` | **Public.** Returns `{ auth_required: bool }` |
| POST | `/api/auth/login` | Login with `identifier` (username or email) + `password`; sets httpOnly cookie. If `identifier` contains `@`, matches by email; otherwise by username. |
| POST | `/api/auth/logout` | Clear auth cookie |
| GET | `/api/auth/me` | Return current user (includes `username`, nullable `email`) |
| PATCH | `/api/auth/me/password` | Change own password (`current_password` + `new_password`) |
| PATCH | `/api/auth/me/profile` | Update own profile: `username`, `name`, `email` (nullable, `EmailStr`), `plan_mode_default`. Admin can change email; send `null` to clear email. |

## `app_settings.py` — App Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Return app settings (public) |
| PUT | `/api/settings` | Update settings (admin only). Keys: `auth_required`, `max_agent_concurrency` (int, 1-10, live-updates semaphore), `default_schedule_phase_mode` (`fanout` \| `single` — default mode pre-selected when creating new all-stores schedules), `default_schedule_timezone` (IANA name, validated via `ZoneInfo`; GET seeds with `get_server_timezone()` when unset), `task_retention_days` (int, 0-3650, default 30; controls the daily auto-cleanup job — 0 disables it. See [docs/tasks.md § Task deletion + auto-cleanup](tasks.md#task-deletion--auto-cleanup)), `google_workspace_enabled` |
| GET | `/api/settings/google-workspace/status` | Report `gws` binary presence, auth status, version, and whether the umbrella bundle is enabled/installed |
| POST | `/api/settings/google-workspace/enable` | Admin only. Validates prereqs (400 if gws missing or unauthenticated), runs `gws generate-skills`, installs the 19-skill umbrella at `.claude/skills/gws/`, sets `google_workspace_enabled=true` |
| POST | `/api/settings/google-workspace/disable` | Admin only. Idempotent. Removes `.claude/skills/gws/` and clears the flag |

## `users.py` — User Management (admin only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/users` | List all users |
| POST | `/api/users` | Create user (`username` required, `name`, `email` optional/validated, `password`, `role`) |
| PUT | `/api/users/{id}` | Update user (supports `username`, `email`; send `email: null` to clear) |
| DELETE | `/api/users/{id}` | Soft-delete (set is_active=false) |

## `stores.py` — Store Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stores` | List all stores |
| POST | `/api/stores` | Create a store |
| GET | `/api/stores/{store_id}` | Get store by ID |
| DELETE | `/api/stores/{store_id}` | Delete a store |

## `tasks.py` — Task Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks?store_id=&include_archived=&parent_task_id=` | List tasks (filter by store, archived, or parent task) |
| POST | `/api/tasks` | Create task (auto mode by default; `plan_mode=true` for plan mode; optional `parent_task_id` to create a sub-task — sub-tasks must have a `store_id`) |
| GET | `/api/tasks/{task_id}` | Get task by ID |
| DELETE | `/api/tasks/{task_id}` | Cascade-delete a task and its full subtree (children, grandchildren, …). Removes dependent rows (`task_steps`, `task_messages`, `task_attachments`, `task_logs`, `screenshots`), drops the on-disk workspace at `~/.vibe-seller/tasks/{task_id}/`, and best-effort stops any running agent. Refuses with 409 if any task in the subtree is in `designing` / `running` — caller must Stop first. Idempotent: returns `{ok: true}` even when the task does not exist. Cleanup logic lives in `app/task_delete.py` and is shared with the auto-cleanup job. |
| POST | `/api/tasks/{task_id}/retry` | Retry failed/completed task (clears plan_history, wipes workspace) |
| POST | `/api/tasks/{task_id}/start` | Queue task via scheduler |
| GET | `/api/tasks/{task_id}/steps` | Get task steps |
| POST | `/api/tasks/{task_id}/agent/start` | Start Claude Code agent |
| POST | `/api/tasks/{task_id}/agent/stop` | Stop agent |
| POST | `/api/tasks/{task_id}/design` | Start design agent |
| POST | `/api/tasks/{task_id}/execute-plan` | Execute planned task (plan mode only) |
| POST | `/api/tasks/{task_id}/questions/answer` | Answer agent question (batch) |
| POST | `/api/tasks/{task_id}/result` | Record task result summary (agent-only via MCP `vibe_seller_set_task_result`). Does NOT transition status; `_auto_run_task` handles that. |
| POST | `/api/tasks/{task_id}/error` | Record unrecoverable task error (agent-only via MCP `vibe_seller_set_task_error`). Does NOT transition status; `_auto_run_task` sees `task.error` during cleanup and transitions to FAILED. |
| GET | `/api/tasks/{task_id}/schedule-state/{key}` | Read a cross-run cursor persisted by a prior run of the same schedule (agent-only via MCP `vibe_seller_get_schedule_state`). Scope resolved server-side from `task.schedule_id`; non-scheduled tasks → 400. Response omits `schedule_id` so the MCP tool output cannot leak it to the agent. Keys must match `[A-Za-z0-9_.-]{1,64}`. |
| PUT | `/api/tasks/{task_id}/schedule-state/{key}` | Upsert a cross-run cursor (agent-only via MCP `vibe_seller_set_schedule_state`). Body: `{value: string}` (non-empty). Uses SQLite `INSERT ON CONFLICT DO UPDATE` so concurrent manual triggers can't race the PK insert. Same scoping / key rules as GET. Typed keys: `email_watermark` must be a unix epoch seconds integer string (regex `^[0-9]{1,15}$`) — ISO timestamps are rejected because lex compare with microseconds / timezone variants is unsafe. |
| POST | `/api/tasks/{task_id}/wake` | Wake a waiting task (optional message) |
| GET | `/api/tasks/{task_id}/messages` | Get agent chat history |
| POST | `/api/tasks/{task_id}/messages` | Send chat message to agent; if task is in `waiting` status, also wakes the task (response includes `woken: true`) |
| GET | `/api/tasks/{task_id}/files` | List agent-generated files in task workspace (non-symlink, non-dot files in task root) |
| GET | `/api/tasks/{task_id}/files/{filename}` | Download a task output file (path traversal protected) |

`TaskResponse` includes `created_by_name: str | null` — the creator's display name resolved from the users table (defaults to `"admin"` if not found).

## `schedules.py` — Schedule Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/schedules?store_id=` | List schedules (filter by store) |
| POST | `/api/schedules` | Create a recurring schedule. Optional `phase_mode` (`fanout` \| `single`) only honored when `store_id` is null; store-bound schedules always resolve to `single`. When omitted, server uses `AppSettings.default_schedule_phase_mode`, falling back to `fanout`. `two_phase` is system-only and rejected with 400. When `plan_mode=true`, the response is returned with `plan_status='planning'` and a fresh `is_plan_only` Task is spawned; the schedule is not eligible to fire until the user approves the plan (see [Plan-at-creation lifecycle](subsystems.md#plan-at-creation-lifecycle)). |
| GET | `/api/schedules/{id}` | Get schedule detail |
| PUT | `/api/schedules/{id}` | Update schedule. Mutable: `title`, `description`, `schedule_type`, `schedule_time`, `schedule_day`, `interval_value`, `timezone`, `ai_profile_id`. `plan_mode`, `phase_mode`, and `store_id` are immutable (400 / 422 respectively). `plan_version` acts as an `If-Match` optimistic-lock token — stale values return 412. On a plan-mode schedule, a `description` change (normalized whitespace) aborts any in-flight planner and flips `plan_status='stale'`; call `/replan` to author a new plan. |
| DELETE | `/api/schedules/{id}` | Delete schedule |
| POST | `/api/schedules/{id}/pause` | Pause schedule |
| POST | `/api/schedules/{id}/resume` | Resume schedule. Returns 409 if `plan_mode=true` and `plan_status != 'ready'` (gate with the cron fire-gate so APScheduler doesn't fire a blocked schedule). |
| GET | `/api/schedules/{id}/tasks` | List child tasks for schedule |
| POST | `/api/schedules/{id}/trigger` | Manually trigger schedule. Returns 409 if `plan_mode=true` and `plan_status != 'ready'`. All-stores schedules route by `phase_mode`: `fanout`/`two_phase` → one task per active store; `single` → one no-store task. Store-bound schedules always create one store-scoped task. Before creating a per-store child task, any prior `waiting` task for the same `(schedule, store)` is cancel-forwarded (FAILED with `error_category='superseded_by_next_fire'`) and a `schedule_waiting_cancelled` SSE event is emitted. |
| POST | `/api/schedules/{id}/replan` | **Idempotent.** Spawn a new `is_plan_only` Task to author the schedule's plan. If a non-terminal planning Task already exists, returns the current response without spawning a duplicate. Returns 400 for non-plan-mode schedules. |

### `ScheduleResponse` fields

All endpoints returning a schedule serialize these fields:

| Field | Type | Notes |
|---|---|---|
| core CRUD fields | — | `id`, `store_id`, `title`, `description`, `plan`, `schedule_type`, `schedule_time`, `schedule_day`, `interval_value`, `timezone`, `is_active`, `is_system`, `phase_mode`, `plan_mode`, `ai_profile_id`, `created_by`, `created_at`, `updated_at` |
| `plan_status` | `'none' \| 'planning' \| 'ready' \| 'stale' \| 'failed'` | Lifecycle of the frozen plan — see [`PlanStatus`](subsystems.md#plan-at-creation-lifecycle). |
| `plan_version` | int | Monotonic counter, bumped on every successful plan commit. Use as `If-Match` when sending PUT. |
| `plan_error` | string \| null | Last planner failure reason (shown in the UI banner on `failed`). Cleared on successful plan commit or on prompt-edit invalidation. |
| `current_planning_task_id` | string \| null | Task ID currently authoring the plan, if any. |
| `next_run` | string \| null | Next APScheduler fire time (ISO). |
| `child_task_count` | int | Count of non-plan-only child tasks. |
| `last_run_status` | string \| null | Status of the most recent non-plan-only child task. |
| `pending_questions_count` | int | Count of child tasks in `waiting` (frontend badges this). |

### `ScheduleUpdate` body

PUT accepts the mutable fields listed in the table above plus
optional `plan_version` (for the If-Match check). Unknown fields
return 422 (`model_config = extra='forbid'`).

On create, if the request body omits `timezone` (or sends `null`), the router resolves it in order: `AppSettings['default_schedule_timezone']` → `get_server_timezone()` (via `tzlocal`, UTC fallback). PUT accepts `timezone` and re-registers the APScheduler job with the new zone. Invalid IANA strings → 400.

## `events.py` — Event Tracking

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/events?status=&store_id=` | List events (multi-status filter, comma-separated) |
| POST | `/api/events` | Create event manually |
| GET | `/api/events/{id}` | Get event |
| PUT | `/api/events/{id}` | Update event fields |
| POST | `/api/events/{id}/status` | Change status with transition validation |
| POST | `/api/events/{id}/confirm` | Confirm draft → open |
| POST | `/api/events/{id}/dismiss` | Dismiss event |
| POST | `/api/events/{id}/sync` | Sync to external backend |
| DELETE | `/api/events/{id}` | Delete event |
| GET | `/api/events/{id}/activities` | List activity timeline |
| POST | `/api/events/{id}/activities` | Add note to timeline |
| GET | `/api/events/backends` | List sync backends |
| POST | `/api/events/backends/configure` | Configure sync backend |

## `email_accounts.py` — Email Account Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/email-accounts` | List email accounts |
| POST | `/api/email-accounts` | Create email account (password encrypted) |
| GET | `/api/email-accounts/discover` | Auto-discover IMAP settings |
| GET | `/api/email-accounts/discover-smtp` | Auto-discover SMTP settings |
| POST | `/api/email-accounts/{id}/test` | Test IMAP connection |
| POST | `/api/email-accounts/{id}/test-smtp` | Test SMTP connection |
| POST | `/api/email-accounts/{id}/send` | Send email via SMTP |
| DELETE | `/api/email-accounts/{id}` | Delete email account |
| GET | `/api/email-accounts/info-by-store/{store_id}` | DB paths + schema for agent MCP tools |
| GET | `/api/stores/{id}/emails` | List linked emails for store |
| POST | `/api/stores/{id}/emails` | Link email account to store |
| DELETE | `/api/stores/{id}/emails/{link_id}` | Unlink email from store |
| POST | `/api/stores/{id}/emails/poll` | Poll emails (deprecated, use background sync) |

## `profiles.py` — AI Agent Profiles

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/profiles` | List AI profiles |
| POST | `/api/profiles` | Create AI profile |
| PUT | `/api/profiles/{id}` | Update AI profile |
| DELETE | `/api/profiles/{id}` | Delete AI profile |
| PATCH | `/api/profiles/{id}/set-default` | Set a profile as the user's default |
| POST | `/api/profiles/validate` | Probe a profile's endpoint config (no persistence) |
| GET | `/api/profiles/presets` | Provider presets + per-provider model options |

`POST /api/profiles/validate` takes `{"env": {...}}` and makes one minimal Anthropic `/v1/messages` request against the config's own `ANTHROPIC_BASE_URL` (mirroring Claude Code's auth). It **always returns HTTP 200** — the verdict is in the body: `{"ok": bool, "code": str, "error": str, "reported_model": str | null}`. The Settings UI hits it on save so an unreachable base URL, a wrong/expired key, a wrong protocol, or a retired model id is caught on the config page rather than on the next agent run. See [backend.md § Profile Endpoint Validation](backend.md#profile-endpoint-validation-aiprofile_validationpy).

## `workspace.py` — Workspace & Knowledge & Skills

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/workspace/knowledge/sync` | Fetch remote knowledge updates |
| GET | `/api/workspace/knowledge/sync-meta` | Get sync metadata |
| POST | `/api/workspace/skills/sync` | Sync built-in skills (local + remote) |
| GET | `/api/workspace/skills/sync-meta` | Get skills sync metadata |
| POST | `/api/workspace/skill` | Create a new user skill |
| GET | `/api/workspace/structured` | Workspace grouped for the UI. One entry per store joining `stores/<slug>/` knowledge `files` with `store-data/<slug>/` run-data `data_files`/`data_path` (run-data sorted newest-first) |
| GET | `/api/workspace/file` | Read a text file (`{path, content}`); decodes utf-8 with GB18030 fallback; 400 with a pointer to `/file/raw` for binary |
| GET | `/api/workspace/file/raw` | Raw bytes with guessed content type. `Content-Disposition: inline` only for non-scriptable types (pdf, png/jpeg/gif/webp); everything else `attachment` (stored-XSS guard) |
| GET | `/api/stores/{id}/bookmarks` | Read Chrome profile bookmarks |

## `workspace_assistant.py` — Workspace AI Assistant

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/workspace/assistant/message` | Send message to workspace AI assistant |
| POST | `/api/workspace/assistant/stop` | Stop workspace assistant session |
| GET | `/api/workspace/assistant/status` | Check if assistant is running |

## `dida365_oauth.py` — TickTick/Dida365 Integration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dida365/status` | TickTick/Dida365 connection status |
| POST | `/api/dida365/authorize` | Start OAuth2 flow |
| GET | `/api/dida365/callback` | OAuth2 redirect callback |
| GET | `/api/dida365/projects` | List TickTick projects |
| POST | `/api/dida365/configure` | Save project + MCP path |
| POST | `/api/dida365/setup-mcp` | Auto-install ticktick-mcp |
| DELETE | `/api/dida365/disconnect` | Disconnect integration |

## `wecom_bots.py` — WeChat Work (企业微信) Bot Webhooks

Workspace-level webhook configs for WeChat Work group bots. Not
bound to any store. List responses mask `webhook_url` (the URL
carries a secret `?key=...`) — use the single-item GET to retrieve
the full URL for the edit form.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/wecom-bots` | List bots — returns `webhook_url_masked` (e.g. `https://host/...?key=****abcd`) |
| GET | `/api/wecom-bots/{id}` | Get one bot with the full `webhook_url` (for edit) |
| POST | `/api/wecom-bots` | Create `{name, webhook_url}` |
| PUT | `/api/wecom-bots/{id}` | Update `name` or `webhook_url`; `null`/blank required fields → 400 |
| DELETE | `/api/wecom-bots/{id}` | Delete bot |
| POST | `/api/wecom-bots/{id}/test` | Post a test message to the webhook; optional `{content}` (blank/whitespace → default) |
| POST | `/api/wecom-bots/{id}/send` | Post a real message: `{content, msgtype?}`. `msgtype` is `text` (default) or `markdown`. Blank `content` → 400; unknown `msgtype` → 400. Used by agents via the MCP bridge (see `docs/workspace.md`). |
| POST | `/api/wecom-bots/{id}/send-file` | Upload a local file and post it as a `file` message: `{path}`. `path` is expanded (`~`) and must be absolute under an allowed root (`/tmp`, `/private/tmp`, or `~/.vibe-seller/downloads`) — anything else → 400 (blocks exfiltrating the DB/secrets/`~/.ssh`). File must be 6 B–20 MB (WeCom limit). Returns `{ok, message}`. Used by agents via the MCP tool `vibe_seller_send_wecom_file`. |

Backend senders: `app/notifiers/wecom.py::send_webhook(url, content, msgtype='text'|'markdown')` and `send_file_webhook(url, file_path)` (two-step `upload_media` → `media_id` → `msgtype=file`). Errors are logged server-side; the response message never echoes the URL back to the client.

## `sse.py` — Server-Sent Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sse` | SSE stream of all real-time events |

Note: SSE endpoint was renamed from `/api/events` to `/api/sse` to free up `/api/events` for event tracking.

## `browser.py` — Browser / Ziniao Utilities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ziniao/launcher` | Download ziniao_webdriver.bat |
| POST | `/api/browser/web/start` | Start (or reuse) the store-less orchestrator `web` browser. Lazy-called by the `bin/_web` wrapper on first use; parallels `POST /api/stores/{id}/browser/start`. Accepts `force` for parity (no-op). |

## `system.py` — Server Runtime Metadata

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/system/info` | Return `{ platform: 'mac' \| 'windows' \| 'wsl' \| 'linux', version: str, commit: str \| null }`. `platform` is the host OS; `version` is the package version from `importlib.metadata` (`'0.0.0+dev'` for editable / dev installs); `commit` is the short git SHA when the source tree is a git checkout (useful when `version` is the dev fallback). Frontend fetches this once on mount and uses it as the single source of truth for platform-conditional UI (Ziniao launcher download, install hints, etc.) and the about/footer build stamp. |
| GET | `/api/system/update-check` | Check PyPI for a release newer than the one running here. Dev/local checkouts (`app.update_check.is_dev_version` — setuptools_scm `.devN+g<sha>` builds or the `+dev` fallback) always get `{ dev: true }`, since there's no PyPI release an arbitrary in-between commit corresponds to. Otherwise returns `{ dev: false, update_available: bool, current_version, latest_version?, platform?, upgrade_command?: str \| null, download_url?: str \| null, releases_page_url?, releases?: [{ version, name, body, url, published_at }] }` — `upgrade_command` (`vibe-seller upgrade`) on macOS/Linux/WSL vs. `download_url` (GitHub releases page) on native Windows, matching the two install paths in the README. `releases` lists GitHub release notes for every version newer than the installed one (capped at 5), used for the frontend's "what's new" popup shown once per login (see `useUpdateCheck` / `UpdateAvailableModal`). |

## `screenshots.py` — Screenshot Serving

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/screenshots/{screenshot_id}` | Serve screenshot PNG file |

## `attachments.py` — File Attachments

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/attachments/{task_id}` | Upload attachment |
| GET | `/api/attachments/{task_id}` | List attachments |
| GET | `/api/attachments/file/{attachment_id}` | Download attachment |

## `channels.py` — Message Channels

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels/types` | List channel types |
| POST | `/api/channels/configure` | Configure channel |
| GET | `/api/channels/active` | List active channels |
| POST | `/api/channels/{id}/poll` | Poll for messages + extract events |
| POST | `/api/channels/{id}/send` | Send message (read-write channels) |
| DELETE | `/api/channels/{id}` | Remove channel |

## `cron.py` — Cron Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cron/jobs` | List cron jobs |
| POST | `/api/cron/jobs` | Create cron job |
| DELETE | `/api/cron/jobs/{id}` | Delete cron job |
| POST | `/api/cron/jobs/{id}/pause` | Pause job |
| POST | `/api/cron/jobs/{id}/resume` | Resume job |

## `ziniao_accounts.py` — Ziniao Accounts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ziniao-accounts` | List accounts |
| POST | `/api/ziniao-accounts` | Create account |
| PUT | `/api/ziniao-accounts/{id}` | Update account |
| DELETE | `/api/ziniao-accounts/{id}` | Delete account |
| GET | `/api/ziniao-accounts/{id}/browsers` | List browser profiles (returns structured status JSON on error) |
| POST | `/api/ziniao-accounts/{id}/restart` | Kill + relaunch Ziniao in WebDriver mode (Mac only, requires `running_normal` state) |

## `main.py` — App-level Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Liveness probe (`{"status": "ok"}`) |
| GET | `/api/plugins` | List active plugins' frontend bundles for the dashboard loader: `[{js_extension_path, requires_early_init}]`. Returns `[]` in an OSS-only install (no frontend plugins). See [backend.md § Plugin Framework](backend.md#plugin-framework). |
