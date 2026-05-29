# Subsystem Details

> Deep-dive architecture for cross-cutting subsystems. See also [DESIGN.md](/DESIGN.md#subsystem-details).

## All-Stores Scheduling

Cross-store scheduling. Three recurring task patterns:

| Pattern | Store binding | `phase_mode` | Tab | Example |
|---------|--------------|--------------|-----|---------|
| **Store-specific** | One store (`store_id` set) | forced `single` | Scheduled | "Check Store A listings daily" |
| **All-stores (fan-out)** | All stores (`store_id=NULL`) | `fanout` | Scheduled | "Check inactive listings for every store" |
| **All-stores (single)** | All stores (`store_id=NULL`) | `single` | Scheduled | "Poll shared IMAP mailbox every 5 min" |

- **Fan-out** / **single** / **two-phase runners**: `app/scheduler/fanout.py` — the `Schedule.phase_mode` column controls orchestration for all-stores schedules. The choice is made at APScheduler registration time in `cron.add_schedule_job()`:
  - `phase_mode='fanout'` (back-compat default): `_run_fanout_job` creates one per-store task fired in parallel, no prerequisite.
  - `phase_mode='single'`: `_run_single_job` creates exactly one `store_id=None` task per tick. Intended for shared work (IMAP sweeps, account health checks, housekeeping) where per-store fanout is wasteful or semantically wrong.
  - `phase_mode='two_phase'` (system-only, currently only the catalog-sync schedule; never client-selectable): Phase 1 creates a single no-store prerequisite task (e.g. global L2 catalog) and awaits completion, then Phase 2 fans out one per-store task (e.g. L3 catalogs).
- **Mode resolution on create**: store-bound → forced `single`; else client-supplied value (rejects `two_phase`); else `AppSettings.default_schedule_phase_mode`; else `fanout`. `phase_mode` and `store_id` are immutable after creation — `ScheduleUpdate` uses `extra='forbid'` so mutation attempts return 422.
- **No-store tasks**: L2 catalog tasks have `store_id=None`. They go through `TaskQueueScheduler` but bypass browser config, session tracking, and CDP proxy — pure file operations.
- **Task grouping**: `Task.batch_id` UUID groups tasks from same fan-out trigger
- **Plan authoring** (plan-mode schedules): Plan is authored **at schedule creation** via a dedicated `is_plan_only` Task, stored on `Schedule.plan`, and gated by `plan_status`. Every fire copies the frozen plan into its child task and skips the design phase. See [Plan-at-creation lifecycle](#plan-at-creation-lifecycle) below.
- **No new task status**: fan-out tasks use standard lifecycle (pending → queued → designing → planned → running → completed/failed)
- **Session reuse**: existing per-store task queue serializes fan-out tasks; browser session persists between queued tasks
- **Frontend**: all-stores schedules shown with "All Stores" badge; child tasks grouped by store (collapsed by default)
- **Task archiving**: terminal tasks (completed/failed) >7 days hidden by default, toggle to show
- **Timezone resolution**: every schedule stores an IANA `timezone` column. When a client omits `timezone` on create, the router resolves it in order: (1) explicit `body.timezone`, (2) `AppSettings['default_schedule_timezone']`, (3) `get_server_timezone()` in `app/scheduler/cron.py` (wraps `tzlocal.get_localzone_name()`, UTC on failure). `build_trigger()` feeds the stored string into `ZoneInfo` so APScheduler fires in that zone. PUT `/api/schedules/:id` re-registers the APScheduler job so timezone edits take effect immediately.

## Plan-at-creation lifecycle

Plan-mode schedules (`Schedule.plan_mode=True`) author their plan
**once at creation**, not on every fire. The plan lives on
`Schedule.plan` and is governed by a lifecycle column `plan_status`
(`PlanStatus` enum in `app/plan_states.py`):

```
           (no plan_mode)
 create ─────────────────── ready
    │
    │ (plan_mode=True)
    ▼
  planning ──(user /replan)──┐
    │       ▲                │
    │ ok    │ fail           │
    ▼       │                │
  ready ◄───┴── failed       │
    │                        │
    │ (prompt edited)        │
    ▼                        │
  stale ───(user /replan)────┘
```

| Status | Meaning | Fire-gate |
|---|---|---|
| `none` | No plan authored (plan_mode=False, or never planned) | pass (plan_mode=False only) |
| `planning` | An `is_plan_only` Task is authoring the plan | block |
| `ready` | Plan committed; schedule eligible to fire | pass |
| `stale` | Prompt was edited; stored plan is no longer valid | block |
| `failed` | Planner task failed or timed out | block |

### Fire-gate

Enforced inside both `app/scheduler/cron.py:_run_task_job` and
`app/scheduler/fanout.py:run_fanout_job` / `run_single_job`:

```
fire eligible iff
    is_system == True
 OR plan_mode == False
 OR plan_status == 'ready'
```

Non-ready plan-mode schedules are skipped with a `schedule_skipped`
SSE event. Manual `POST /api/schedules/{id}/trigger` and `/resume`
mirror the gate with a 409.

### Cancel-forward on next fire

If a prior run for `(schedule, store)` is still in `waiting` when the
next fire lands, the scheduler **cancels** the waiter (FAILED with
`error_category='superseded_by_next_fire'`) and emits
`schedule_waiting_cancelled` before creating the new run. Silently
skipping would stall a daily schedule for up to 30 days on one
forgotten user question.

### Plan-only Task flow

A plan-only Task (`Task.is_plan_only=True`) is a one-shot planner
that terminates at `completed` without executing. `auto_approve_plan`
is forced to `False` for these tasks (narrowed gate in
`app/task_runner_auto.py`), so the agent pauses at `ExitPlanMode`
and waits for human review via the standard review-plan UI.

On approval, `_commit_plan_only_approval` in
`app/ai/claude_backend_hooks.py` runs inside a single DB transaction:

1. Task DESIGNING/PLANNED → COMPLETED.
2. `Schedule.plan = <plan_text>`, `plan_status='ready'`,
   `plan_version += 1`, `plan_error=None`,
   `current_planning_task_id=None`.
3. Sends a `deny` ControlResponse so the agent halts instead of
   entering bypass mode.
4. Emits `task_update` + `schedule_plan_ready` only **after** commit.

### Invalidation on prompt edit

`PUT /api/schedules/{id}`:

- `plan_mode` is immutable after creation — attempts return 400.
- `plan_version` acts as an `If-Match` optimistic-lock token — stale
  values return 412. Bumped on every successful plan commit.
- `description` changes are normalized (strip + collapse whitespace)
  before comparison so cosmetic edits don't spuriously invalidate.
- Prompt change → abort any in-flight planner (stop session, FAIL
  the Task with `error_category='superseded_by_edit'`) and set
  `plan_status='stale'`. The old plan text is kept so the UI can
  show a diff until the user re-plans.
- Cron/timezone/store changes do not invalidate.

### `/replan` (idempotent)

`POST /api/schedules/{id}/replan` spawns a new plan-only Task. If a
non-terminal planning Task already exists for the schedule, the
endpoint returns the current response without spawning a duplicate.
Non-plan-mode schedules return 400.

### Stuck-planning reaper

`app/scheduler/plan_reaper.py:reap_stuck_planning_tasks` runs every
5 minutes (registered in `cron.py:_register_plan_reaper`). For each
Schedule with `plan_status='planning'` whose planner Task is neither
WAITING nor younger than 30 min, it:

1. Calls `agent_manager.stop(task.id)`.
2. Transitions the Task to FAILED with
   `error_category='planning_timeout'`.
3. Sets `plan_status='failed'`, `plan_error='Planning timed out'`,
   and clears `current_planning_task_id`.
4. After commit, emits `schedule_plan_timeout`.

WAITING is explicitly preserved — it is legitimate user-input wait,
handled by `waiting.py`'s 30-day timeout, not this reaper.

### Key file map

| Component | File |
|---|---|
| Plan state enum | `app/plan_states.py` |
| Schedule model (plan columns) | `app/models/schedule.py` |
| Task model (`is_plan_only`, `plan_version`) | `app/models/task.py` |
| Router endpoints (create/update/replan/trigger/resume) | `app/routers/schedules.py` |
| Fire-gate + cancel-forward | `app/scheduler/cron.py`, `app/scheduler/fanout.py` |
| Approval hook + plan commit | `app/ai/claude_backend_hooks.py:_commit_plan_only_approval` |
| Reaper | `app/scheduler/plan_reaper.py` |
| `auto_approve_plan` narrowing | `app/task_runner_auto.py` |
| Prompt block for plan-only tasks | `app/task_runner.py:build_system_extra` |

## Skill Prerequisite Enforcement

Skills can declare `requires: [<prereq-skill-name>]` in their YAML frontmatter. The PreToolUse hook enforces the order: a `Skill(<dependent>)` call is denied until `<prereq>` has been loaded in the same session — same mechanism shape as Claude Code's built-in Read-before-Write rule.

### Why

Several skills are layered: e.g. `noon-ads` needs `noon-shared`'s login flow and URL map before any ad work makes sense. A prose `> **PREREQUISITE:** Read ../noon-shared/SKILL.md` line in the dependent's SKILL.md is the *spec*, but non-Claude models (and Claude under load) sometimes skip it and run with a partial context. The hook turns that prose into a mechanism.

### Frontmatter format

```yaml
---
name: noon-ads
description: "Noon Ad Manager — campaigns, tuning audits, ..."
requires: [noon-shared]
---
```

Only the inline-list form is parsed (`parse_skill_requires` in `app/ai/claude_backend_utils.py`). Multiple prereqs are allowed: `requires: [a, b]`. Skills with no `requires:` (and unknown skills not shipped by us) pass through unchanged.

### Hook flow

```
Skill(noon-ads) tool_use
        ↓
Claude Code → control_request hook_callback (PreToolUse, tool_name="Skill")
        ↓
_handle_hook_callback (claude_backend_hooks.py)
        ↓
_check_skill_prereqs("noon-ads"):
   • find_skill_md(workspace, "noon-ads") → task_dir/.claude/skills/noon-ads/SKILL.md
   • parse_skill_requires() → ["noon-shared"]
   • missing = [r for r in requires if r not in self._loaded_skills]
        ↓
missing? → control_response {permissionDecision: "deny",
                             permissionDecisionReason: "Skill 'noon-ads' requires 'noon-shared'
                                                        to be loaded first. Call ..."}
not missing? → allow, then self._loaded_skills.add("noon-ads")
```

The deny lands back at Claude Code as an `is_error: true` tool_result, which the agent reads and reacts to in the same turn — typically by calling `Skill(noon-shared)` next, then retrying `Skill(noon-ads)`.

### Per-session state

`self._loaded_skills: set[str]` lives on `AgentSession`. It resets when the session resets (e.g. a fresh restart of a failed task) — which is correct, because the restarted agent's context window doesn't have the prior skill body. The hook then re-enforces the order against the fresh session.

### Key files

| Concern | File |
|---|---|
| Frontmatter `requires:` parser | `app/ai/claude_backend_utils.py:parse_skill_requires` |
| SKILL.md path resolver (task workspace → global fallback) | `app/ai/claude_backend_utils.py:find_skill_md` |
| Hook deny path | `app/ai/claude_backend_hooks.py:_check_skill_prereqs` |
| Per-session loaded set | `app/ai/claude_backend.py:AgentSession._loaded_skills` |
| E2E proof of full deny → retry → load cycle | `tests/e2e/test_skill_prereq_hook.py` |

## Email System

Persistent per-account email storage with background sync and SMTP send capability.

- **EmailAccount model**: Stores IMAP + SMTP credentials with Fernet-encrypted passwords (key from `JWT_SECRET` via SHA-256). SMTP columns: `smtp_host`, `smtp_port`, `smtp_use_tls`
- **StoreEmailLink**: Many-to-many junction linking email accounts to stores
- **Per-account SQLite DBs**: Each email account gets its own SQLite database at `~/.vibe-seller/data/email_dbs/email_{account_id}.db` (managed by `app/email/db.py`). Stores messages with full headers, body, and metadata
- **Background sync**: `app/scheduler/email_sync.py` syncs all accounts every 5 minutes (concurrent with semaphore to limit load). Replaces the old channel-based polling
- **Auto-discovery**: `app/channels/email_discovery.py` provides both IMAP and SMTP auto-discovery for top providers (163, Gmail, QQ, Outlook, Yahoo), with heuristic fallback
- **SMTP send**: `app/email/sender.py` — send emails via SMTP through any configured account. Exposed via `POST /api/email-accounts/{id}/send`
- **Agent access**: Agents query emails via `sqlite3` CLI against the local DB files. Store context (`_build_store_context`) injects DB paths and schema info. MCP tools `vibe_seller_email_info` (DB paths + schema) and `vibe_seller_send_email` (send via SMTP) replace the old `vibe_seller_poll_emails`
- **Waiting task checker**: Queries the local SQLite DB for keyword matches instead of live IMAP polling

## Chrome Persistent Profiles & Bookmarks

- **Persistent profiles**: Managed by browser-use daemon per session. Chrome stores use `--session {slug}` for profile isolation. Cookies, localStorage, and login sessions survive across tasks.
- **Ziniao aux sessions**: Ziniao stores also get an auxiliary Chrome session (`{slug}-aux`) for non-seller-center URLs (Google, logistics sites, etc.). The aux session starts lazily on first use — zero overhead if unused.
- **Proxy config**: Only relevant for Chrome stores in UI.
- **Bookmarks**: `read_bookmarks(slug)` reads `Default/Bookmarks` JSON from the profile dir. Auto-injected into agent context when knowledge files are sparse.

## Browser & Task Concurrency

**Concurrent per-store execution**: Multiple tasks for the same store can run concurrently (up to the proxy's `max_clients` connection limit, default 5 per CDPMuxProxy instance) when they share the same platform/country. The `CDPMuxProxy` provides per-task isolation within a single browser session via request ID rewriting and session-based event routing. Tasks that share the same platform but target different countries are queued since Ziniao needs to switch country; all other combinations run concurrently.

**Dispatch**: All store-task launch paths (create, retry, continue, execute-plan) route through `_schedule_or_run()` which submits to `TaskQueueScheduler` when it's running.  No-store tasks launch directly.  The scheduler uses `can_schedule()` to gate by platform/country compatibility (`RUN`, `RUN_IN_NEW_TAB`, or `QUEUE`).

**Per-task daemon sessions**: Each task gets its own browser-use daemon session: `{slug}-{VIBE_TASK_ID[:8]}`. CDPMuxProxy is the primary isolation mechanism — each task connects with a unique client ID (`/client-{task_id}`) and receives isolated CDP sessions via request ID rewriting and session-based event routing. The wrapper validates sessions via prefix check (`{slug}|{slug}-*`).

**Ziniao guard (account conflict)**: Only one Ziniao account can be active per machine (one Ziniao process). Multiple *profiles* (different `browserOauth`) on the same account work fine — each gets a unique `debuggingPort` and CDP proxy. But if store A uses Ziniao account #1 and store B uses account #2, store B's task will fail with a clear error: "Store(s) [Store A] are using a different Ziniao account. Stop the browser session first." Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

**Ziniao dual-browser**: Ziniao stores get dual-session support in their wrapper — a main session (`{slug}`, routed via CDP proxy to Ziniao) and an aux session (`{slug}-aux`, Chrome for non-seller-center URLs). The agent uses AI judgment to route: seller center → Ziniao session, everything else → Chrome aux session. Routing rules defined in `app/prompts/dual_browser.md`. Users can override via `stores/{slug}/browser-routing.md`.

## System Scheduler Jobs

`start_scheduler()` in `app/scheduler/cron.py` registers a fixed set of system-only APScheduler jobs (none of these correspond to a row in the `schedules` table, so they don't appear in the UI):

| Job id | Trigger | Source | Purpose |
|--------|---------|--------|---------|
| `waiting_checker` | every 15 min | `app/scheduler/waiting.py` | Check `WAITING` tasks for email/condition triggers; time out stale waits |
| `email_sync` | every 5 min | `app/scheduler/email_sync.py` | Sync configured email accounts into per-account SQLite DBs |
| `plan_reaper` | every 5 min | `app/scheduler/plan_reaper.py` | Fail schedules stuck in `plan_status='planning'` |
| `stall_reaper` | every 2 min | `app/scheduler/stall_reaper.py` | Fail RUNNING tasks whose agent stream has gone silent |
| `task_cleanup` | daily 03:30 server-tz | `app/scheduler/task_cleanup.py` | Auto-delete terminal leaf tasks past `AppSettings.task_retention_days` (see [docs/tasks.md § Task deletion + auto-cleanup](tasks.md#task-deletion--auto-cleanup)) |

## Ziniao on WSL

WSL **cannot** auto-launch Ziniao because Electron's Node.js V8 rejects unknown `--` flags before the app code runs. The workflow is:

1. User downloads `ziniao_webdriver.bat` from `GET /api/ziniao/launcher` (or uses the copy in the repo root)
2. User double-clicks the `.bat` on Windows — it auto-finds the exe, kills any existing Ziniao, launches in WebDriver mode, and verifies the HTTP API
3. Once Ziniao is running, WSL connects via the gateway IP (auto-detected by `_get_ziniao_host()` from `ip route`)

If Ziniao is already running with the correct port, everything works automatically. The backend only raises errors when Ziniao is unreachable, guiding the user to the launcher script.

## CDP Proxy Architecture

Each Ziniao store gets a stable CDP multiplexing proxy (`app/browser/cdp_mux_proxy.py`) that listens on `127.0.0.1:{proxy_port}` and connects upstream (WebSocket) to the actual Ziniao `debuggingPort` (which changes on each `startBrowser` call). On WSL, the proxy connects to the Windows gateway IP instead of localhost. The browser-use wrapper's `--cdp-url` always points to the stable proxy port with a per-task client ID.

**Multi-client support**: Multiple browser-use CLI processes connect simultaneously, each via `ws://127.0.0.1:{proxy_port}/client-{task_id}`. The proxy multiplexes all clients onto a single upstream WebSocket to the browser, using:
- **Request ID rewriting**: Client request IDs are remapped to globally unique IDs; responses are routed back to the originating client
- **Session-based event routing**: CDP `sessionId` → owning client mapping ensures page events only reach the tab owner
- **Target filtering**: `Target.getTargets()` responses filtered per-client; cross-client `attachToTarget`/`closeTarget` blocked
- **Pending event cache**: Handles the race where `Target.attachedToTarget` arrives before `Target.createTarget` response (common with browser-use's `setAutoAttach`)

**Lifecycle**: The proxy stays alive while any task uses it. Client disconnect (clean or crash) triggers cleanup: close the client's tabs, remove routing entries. Startup cleanup closes orphan tabs from prior server crashes.

A legacy TCP relay (`CDPTcpProxy` in `cdp_proxy.py`) is kept as fallback for single-client scenarios.

## Soft Stop-Gates

When the agent calls `vibe_seller_set_task_result`, the resolved result text runs through a small chain of **soft gates** before the task is allowed to settle. Each gate is a function that returns either `None` (allow) or a `GateDeny` with a short, agent-readable `reason` and a `gate` identifier. Code lives under `app/ai/stop_gates/`; the dispatch is in `app/routers/tasks.py::set_task_result`.

### Why soft, not hard

Gates can be wrong, the model can be stubborn, and trapping the agent forever in a deny loop wastes tokens. Each gate is allowed at most `SOFT_GATE_MAX_DENIALS` denials per task (the agent sees the deny reason and can retry); past the cap the result is allowed through with the original text and a `logger.warning(...)`. Deny counts are tracked in-memory by task id (`record_attempt` in `app/ai/stop_gates/__init__.py`); the current cap value lives there alongside the rationale.

### Why at the MCP tool call, not the Stop hook

Some backends (notably DeepSeek under certain compaction conditions) never emit a `Stop` event, so a Stop-hook-only gate would be silently skipped. `set_task_result` is the one tool every successful task path calls, so the gate fires there.

### Bundled gates

| Module | Concern | Body of the check |
|---|---|---|
| `app/ai/stop_gates/markdown_format.py` | Final result is well-formed Markdown — no leftover XML-style closing tags, no truncated fences | Regex scan on the resolved text |
| `app/ai/stop_gates/result_language.py` | Reply language matches the user's task language (the agent shouldn't answer a Chinese task in English) | Character-set heuristic over the prompt + result, with carve-outs for technical identifiers |

### Adding a gate

1. Drop a module under `app/ai/stop_gates/` exporting a `check(text, …) -> GateDeny | None` callable. Free to take extra inputs (e.g. `result_language.check` also takes the task title + description).
2. Wire it into the dispatch loop in `set_task_result` (`for gate_module, gate_args in (…)`).
3. Add a unit test under `tests/unit/test_stop_gates.py`.

The dispatch loop deliberately runs gates in declaration order and short-circuits on the first deny — so put cheaper / more important gates first.
