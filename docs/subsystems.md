# Subsystem Details

> Deep-dive architecture for cross-cutting subsystems. See also [DESIGN.md](https://github.com/zpoint/vibe-seller/blob/main/DESIGN.md#subsystem-details).

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
- **Finalize / parent-reduce step** (the *join*, mirror of two_phase's prerequisite): an all-stores fanout schedule may set `Schedule.finalize_description`. When set, `app/scheduler/finalize_reaper.py` (a periodic single-writer reaper, mirrors `plan_reaper`/`stall_reaper`) fires **one** no-store task (`Task.is_finalize=True`) after **every** per-store child of a batch is terminal — handing it a `batch_results.json` (per child: status, result, error, store_slug, task_dir). The framework decides nothing past fire-once-after-all-terminal; the prompt (`finalize_description`) drives retry/summarize/publish. It's agent-authored at plan time via the `vibe_seller_register_finalize` MCP tool and shown in the plan-review panel. `finalize_description` is only valid for all-stores fanout schedules (create/update + the MCP endpoint reject it otherwise). `is_finalize` (not "store_id IS NULL") disambiguates the finalize task from a two_phase L2 prereq task, which is also no-store.
- **One store at a time** (`Schedule.fanout_serial`, opt-in, all-stores fanout only): a batch's per-store children run one after another, in creation (store) order, instead of all at once. A cron time cannot stagger a fanout, and every store shares one anti-detect client, so two stores driving the same portal in the same instant starve each other at the launch lock. `TaskQueueScheduler._serial_sibling_ahead` holds a child while **any** sibling is RUNNING/DESIGNING (or dispatched and not yet RUNNING) — earlier or later, so a child that comes back after a wake or a retry waits for the one running — or while an **earlier** sibling is still PENDING/QUEUED/PLANNED. FAILED and WAITING release the line (a child asking the user a question may wait a month and holds no agent). The gate is derived from the batch's rows on every tick, so a restart needs no recovery. Create/update reject it with 400 on store-bound and single-phase schedules, where it would silently do nothing; the create and edit modals show the checkbox only on an all-stores fanout.
- **Mode resolution on create**: store-bound → forced `single`; else client-supplied value (rejects `two_phase`); else `AppSettings.default_schedule_phase_mode`; else `fanout`. `phase_mode` and `store_id` are immutable after creation — `ScheduleUpdate` uses `extra='forbid'` so mutation attempts return 422.
- **No-store tasks**: `store_id=None`. They go through `TaskQueueScheduler` but bypass the *per-store* browser config, session tracking, and store CDP proxy. L2 catalog tasks are pure file operations; general orchestrator tasks additionally get the store-less `web` browser wrapper (`bin/_web`, lazy-started) for neutral public web work — see [docs/browser.md](browser.md#store-less-web-browser).
- **Task grouping**: `Task.batch_id` UUID groups tasks from same fan-out trigger
- **Plan authoring** (plan-mode schedules): Plan is authored **at schedule creation** via a dedicated `is_plan_only` Task, stored on `Schedule.plan`, and gated by `plan_status`. Every fire copies the frozen plan into its child task and skips the design phase. See [Plan-at-creation lifecycle](#plan-at-creation-lifecycle) below.
- **No new task status**: fan-out tasks use standard lifecycle (pending → queued → designing → planned → running → completed/failed)
- **Session reuse**: existing per-store task queue serializes fan-out tasks; browser session persists between queued tasks
- **Frontend**: all-stores schedules shown with "All Stores" badge; child tasks grouped by store (collapsed by default)
- **Task archiving**: terminal tasks (completed/failed) >7 days hidden by default, toggle to show
- **Timezone resolution**: every schedule stores an IANA `timezone` column. When a client omits `timezone` on create, the router resolves it in order: (1) explicit `body.timezone`, (2) `AppSettings['default_schedule_timezone']`, (3) `get_server_timezone()` in `app/scheduler/cron.py` (wraps `tzlocal.get_localzone_name()`, UTC on failure). `build_trigger()` feeds the stored string into `ZoneInfo` so APScheduler fires in that zone. PUT `/api/schedules/:id` re-registers the APScheduler job so timezone edits take effect immediately.

## Schedule AI profile resolution

Every schedule carries `Schedule.ai_profile_id` with two meanings: `'default'`/NULL = **inherit** (follow the owner's current `User.default_profile_id`), any concrete value = **explicit pin** (always that profile). `resolve_schedule_profile()` (`app/ai/profiles.py`) runs **live at every fire** — cron (`scheduler/cron.py`), all-stores fan-out (`scheduler/fanout.py`), and finalize (`scheduler/finalize_reaper.py`) — and the resolved id is stamped onto the fired `Task.ai_profile_id` as the run's receipt. Switching the default profile therefore moves every unpinned schedule on its next fire with no schedule edits; pinned schedules are immune by design.

**Opt-in sync on default change**: the per-user pref `User.sync_profile_to_schedules` (default off, toggle in Settings → AI profiles) makes `PATCH /api/profiles/{id}/set-default` additionally re-pin the caller's own schedules that hold a *concrete* `ai_profile_id` to the new default (`UPDATE ... WHERE created_by = me AND ai_profile_id NOT IN (NULL, 'default')`). Inherit rows and other users' schedules are untouched; the endpoint response reports how many rows moved in `schedules_synced`.

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

## Skill Prerequisite Hints

Skills can declare `requires: [<prereq-skill-name>]` in their YAML frontmatter. It is a hint, not a gate: when `Skill(<dependent>)` is called before `<prereq>` was loaded in the session, the PreToolUse hook allows the call and attaches `additionalContext` telling the agent to load the prerequisite with the Skill tool too.

### Why

Several skills are layered: e.g. `noon-ads` needs `noon-shared`'s login flow and URL map before any ad work makes sense. The frontmatter names the layering once; the hint reminds an agent that skipped the prerequisite, without blocking the skill it asked for.

Each e-commerce platform ships as one such family: `amazon-shared` + `amazon-{ads,listing}`, `noon-shared` + `noon-{ads,…}`, and — for Taobao/Tmall — `qianniu-shared` + `qianniu-{listing,ads}`. Qianniu differs from the Amazon/Noon (Ziniao) families in its browser layer: it runs on the **chrome** backend with a **human-in-the-loop login** (QR scan or slider-captcha + SMS — no auto-captcha), a wrong-account hard gate, and a promo/公告 popup-close loop; its exports are GB18030-encoded CSV. All writes (Excel `批量导入`, ad-plan changes) are surfaced for human review, never auto-executed.

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
check_skill_prereqs("noon-ads")  (claude_backend_utils.py):
   • find_skill_md(workspace, "noon-ads") → task_dir/.claude/skills/noon-ads/SKILL.md
   • parse_skill_requires() → ["noon-shared"]
   • missing = [r for r in requires if r not in loaded_skills]
        ↓
allow, with additionalContext "Skill 'noon-ads' builds on noon-shared; load it
with the Skill tool too." when anything is missing; then
self._loaded_skills.add("noon-ads")
```

### One load path: the Skill tool

A skill is loaded — added to `_loaded_skills`, durably bound via `record_skill_load`, and its `gates:` armed — **only** by a `Skill(<name>)` call, the same single path Claude Code itself uses (`addInvokedSkill` / `registerSkillHooks`). Reading a SKILL.md as a file (`Read`, `cat`, `sed -n`, …) loads nothing; the hook allows it and, if the skill isn't loaded, attaches `additionalContext` saying that a lookup can carry on but following the procedure needs `Skill(<name>)` (`skill_gate_utils.unloaded_skill_read_hint`). Reads used to bind too, and that guess bound the wrong gates: an ad audit `sed`-ed 22 lines of review-collect for a metadata shape, review-collect's gate refused the ad report, and the run went collecting product reviews.

### Per-session state

`self._loaded_skills: set[str]` lives on `AgentSession`. It resets when the session resets (e.g. a fresh restart of a failed task) — which is correct, because the restarted agent's context window doesn't have the prior skill body. The hint then fires again against the fresh session.

### Key files

| Concern | File |
|---|---|
| Frontmatter `requires:` parser | `app/ai/claude_backend_utils.py:parse_skill_requires` |
| SKILL.md path resolver (task workspace → global fallback) | `app/ai/claude_backend_utils.py:find_skill_md` |
| Hint builder | `app/ai/claude_backend_utils.py:check_skill_prereqs` (called from `claude_backend_hooks.py:_check_skill_prereqs`) |
| Per-session loaded set | `app/ai/claude_backend.py:AgentSession._loaded_skills` |
| E2E: the hint leads the agent to load the prerequisite | `tests/e2e/test_skill_prereq_hook.py` |

## Email System

Persistent per-account email storage with background sync and SMTP send capability.

- **EmailAccount model**: Stores IMAP + SMTP credentials with Fernet-encrypted passwords (key from `JWT_SECRET` via SHA-256). SMTP columns: `smtp_host`, `smtp_port`, `smtp_use_tls`
- **StoreEmailLink**: Many-to-many junction linking email accounts to stores
- **Per-account SQLite DBs**: Each email account gets its own SQLite database at `~/.vibe-seller/data/email_dbs/email_{account_id}.db` (managed by `app/email/db.py`). Stores messages with full headers, body, and metadata
- **Background sync**: `app/scheduler/email_sync.py` syncs all accounts every 5 minutes (concurrent with semaphore to limit load). Replaces the old channel-based polling
- **Auto-discovery**: `app/channels/email_discovery.py` provides both IMAP and SMTP auto-discovery for top providers (163, Gmail, QQ, Outlook, Yahoo), with heuristic fallback
- **SMTP send**: `app/email/sender.py` — send emails via SMTP through any configured account. Exposed via `POST /api/email-accounts/{id}/send`
- **Agent access**: Agents query emails via `sqlite3` CLI against the local DB files. Store context (`_build_store_context`) injects DB paths and schema info. MCP tools `vibe_seller_email_info` (DB paths + schema) and `vibe_seller_send_email` (send via SMTP — refused for accounts whose `allow_send` the user turned off; on by default; enforced in `app/email/sender.py`) replace the old `vibe_seller_poll_emails`
- **Waiting task checker**: Queries the local SQLite DB for keyword matches instead of live IMAP polling

## Chrome Persistent Profiles & Bookmarks

- **Persistent profiles**: Managed by the browser-use (0.13 `browser_harness`) daemon per session. Chrome stores use the `BU_NAME={slug}` env var (0.13; was `--session {slug}`) for profile isolation. Cookies, localStorage, and login sessions survive across tasks.
- **Ziniao aux sessions**: Ziniao stores also get an auxiliary Chrome session (`{slug}-aux`) for non-seller-center URLs (Google, logistics sites, etc.). The aux session starts lazily on first use — zero overhead if unused.
- **Proxy config**: Only relevant for Chrome stores in UI.
- **Bookmarks**: `read_bookmarks(slug)` reads `Default/Bookmarks` JSON from the profile dir. Auto-injected into agent context when knowledge files are sparse.

## Browser & Task Concurrency

**Concurrent per-store execution**: Multiple tasks for the same store can run concurrently (up to the proxy's `max_clients` connection limit, default 16 per CDPMuxProxy instance) when they share the same platform/country. The `CDPMuxProxy` provides per-task isolation within a single browser session via request ID rewriting and session-based event routing. Tasks that share the same platform but target different countries are queued since Ziniao needs to switch country; all other combinations run concurrently.

**Dispatch**: All store-task launch paths (create, retry, continue, execute-plan) route through `_schedule_or_run()` which submits to `TaskQueueScheduler` when it's running.  No-store tasks launch directly.  The scheduler uses `can_schedule()` to gate by platform/country compatibility (`RUN`, `RUN_IN_NEW_TAB`, or `QUEUE`).

**Per-task daemon sessions**: Each task gets its own browser-use daemon session, named via the `BU_NAME` env var (0.13): `{slug}-{VIBE_TASK_ID[:8]}`. CDPMuxProxy is the primary isolation mechanism — each task connects with a unique client ID (`/client-{task_id}`, injected as `BU_CDP_WS`) and receives isolated CDP sessions via request ID rewriting and session-based event routing. The wrapper validates the session name via regex (`^{slug}(-aux|(-{8hex})?(-w{N})?)$`).

**Parallel workers within one task**: a task's *subagents* share its process and therefore its `VIBE_TASK_ID`, so two of them driving the browser at once would land on one daemon and one tab. `browser-use --worker N` gives each concurrent driver its own daemon (`{slug}-{task[:8]}-wN`) and its own mux client (`client-{task}-wN`) on the same logged-in browser. The bound is `VIBE_BROWSER_WORKER_SLOTS` (default 3, `0` disables); the parent agent assigns the numbers. See [browser.md § Parallel workers](browser.md#parallel-workers--browser-use---worker-n).

> **These bounds multiply.** One store's proxy serves
> `MAX_AGENT_CONCURRENCY × (1 + VIBE_BROWSER_WORKER_SLOTS) + 1 aux` clients
> — 9 at the defaults, against a cap of 16. `max_agent_concurrency` is
> user-settable up to 10 from Settings, and nothing recomputes the cap, so
> a high setting combined with worker slots can exhaust it on a single
> store and get clients refused with close code 4029.

**Ziniao guard (account conflict)**: Only one Ziniao account can be active per machine (one Ziniao process). Multiple *profiles* (different `browserOauth`) on the same account work fine — each gets a unique `debuggingPort` and CDP proxy. But if store A uses Ziniao account #1 and store B uses account #2, store B's task will fail with a clear error: "Store(s) [Store A] are using a different Ziniao account. Stop the browser session first." Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

**Ziniao dual-browser**: Ziniao stores get dual-session support in their wrapper — a main session (`{slug}`, routed via CDP proxy to Ziniao) and an aux session (`{slug}-aux`, Chrome for non-seller-center URLs). The agent uses AI judgment to route: seller center → Ziniao session, everything else → Chrome aux session. Routing rules defined in `app/prompts/dual_browser.md`. Users can override via `stores/{slug}/browser-routing.md`.

## Central sync scheduler (app/sync)

Every periodic "ask the network / re-read the world" job runs through
one object: skills, knowledge, the ads skill bundle, the provider-preset
catalog, the image-model catalog (`vision_catalog` — runs only when a
kie.ai key is configured; see [vision.md](vision.md#kept-current-by-the-vision_catalog-sync-job)),
the per-token price catalog (`model_pricing` — see below) (email and the browser daemon reaper are on their way in). It
exists because the same question used to be answered four different
ways — a cooldown file per module, a `while True: sleep()` loop, an extra
APScheduler job, fire-and-forget tasks at boot and at every task launch —
and two of those forgot to record a probe that found nothing, so after
24 h every task launch re-polled GitHub.

- **One registration list** — `app/sync/jobs.py`. Plugins add jobs
  through `plugin_ctx.register_sync_job`.
- **One state table** — `sync_state` (`last_run_at`, `last_ok_at`,
  `version`, `etag`, `status`, `error`, `needs_restart`, `detail`).
  `updated` AND `unchanged` move `last_ok_at`; `skipped`/`failed` do not.
  A boot-time import (`app/sync/migrate.py`) brings an older install's
  per-module bookkeeping across, and refuses to read a legacy FAILED run
  as success — its commit never landed.
- **Timing comes from the database**, not from process start:
  `next = last_ok_at + cadence + a deterministic per-install offset`
  (SHA-256 of the install id + job name, a 1 h window for DAILY and 2 h
  for BIDAILY; `VIBE_SYNC_JITTER=0` for tests). A job that has never run
  fires at once.
- **One in-flight run per job**, shared by the scheduled tick, the
  manual `trigger()` and `ready()`. A tick joins what is running; a
  manual/forced call waits it out and gets one fresh run, which
  concurrent callers share.
- **One greppable line per run**:
  `sync:<name> reason=<tick|manual|event|boot|stale> status=<updated|unchanged|skipped|failed> ...`
  — the e2e tests assert on these. `stale` is a run the *other side*
  caused without being asked: an integration noticed, from an answer it
  was already getting, that what it ships has changed since we
  installed it (the ads skill's `X-Skill-Version` header,
  `app/ads_skill.py`). `event` remains a thing that happened — a person
  authorizing a store.
- Remote writes are download-then-apply in ONE synchronous block, each
  skill dir built in `.claude/.sync-staging` and renamed into place, so a
  task workspace copy can never catch a half-written skill.

API: `GET /api/sync/status`, `POST /api/sync/{name}/run`. The skills
toggle (`skills_auto_sync_enabled`, Settings → General) gates the
scheduled `skills` job only; knowledge and provider presets have no
toggle.

## Image-model catalog sync (`vision_catalog`)

A DAILY job gated by `when` (a kie.ai key is configured and
`VISION_FAKE` is off) — not a user toggle: without a key no image can be
generated, so nothing is fetched. Saving a key in Settings triggers a
run at once (only while sync timers are armed, so tests stay offline).
Three fail-soft steps against the served state in
`app/vision_catalog.py`: `app/vision_models.py` from `main` (literal
parse, schema-validated, cached RAW and re-validated on every load),
per-tier live prices from kie.ai's public pricing table, and kie.ai's
model list as a deny-list of delisted slugs. The run line carries one
status per step (`catalog=… prices=… available=…`, plus
`prices_priced` / `available_hidden`). Full design:
[vision.md § Kept current](vision.md#kept-current-by-the-vision_catalog-sync-job).

## Task usage meter + price catalog (`model_pricing`)

The task footer shows, live, how full the agent's context window is and
what the task has cost so far (`app/ai/usage_meter.py`,
`app/task_usage.py`, `frontend/src/components/TaskUsageMeter.tsx`).

- **Metered per API call, never per `result`.** The CLI's `result`
  event carries `total_cost_usd` / `modelUsage` that are cumulative for
  the PROCESS (a follow-up in the same process re-reports earlier
  turns) and price every model as Claude. The meter reads the partial
  messages instead (`stream_event` `message_start` for the prompt side,
  `message_delta` for the final output count) plus each `assistant`
  event, deduplicated by message id, every field taking `max` (MiniMax
  reports zeros in `message_start` and the real counts only in the
  delta). Each call update is one DB write onto the task row and one
  `task_usage` SSE event.
- **Context bar** = the main agent's latest call: input + cache read +
  cache write + output. Subagent calls cost money but do not move it.
  The window comes from the catalog; on api.anthropic.com the CLI's own
  `modelUsage[…].contextWindow` overrides it (it knows 200K vs 1M).
- **Price** = each bucket × its own rate, the way opencode / pi / Roo
  price third-party models. Rates are a slice of
  [models.dev](https://models.dev/api.json): `app/ai/model_pricing.py`
  maps each provider preset to ONE models.dev provider
  (`PRESET_PROVIDERS`) — plans (Kimi Code, Coding/Token Plans) are
  their own providers with zero rates, shown as **included**; a model
  the catalog lacks (custom profiles) shows tokens only (**unpriced**).
  Long-context tiers apply to the whole call. Claude Code caches with
  a one-hour TTL, billed at 2× input — models.dev's `cache_write` is
  the five-minute rate, so one-hour writes are priced separately
  (verified to match the CLI's own figure on api.anthropic.com).
- **Display**: the footer chip shows two decimals (no `≈`); the click-open
  panel shows four. The Chinese UI shows RMB at a fixed
  `USD_TO_CNY = 6.7` (`frontend/src/lib/usageFormat.ts`), English
  shows USD. The panel's note is one line: estimated from models.dev.
- **Kept current** by the DAILY `model_pricing` sync job
  (`app/ai/model_pricing_sync.py`; env `MODEL_PRICING_URL`), cache
  `~/.vibe-seller/cache/model_pricing.json`, served cache-or-bundled:
  `app/ai/model_pricing_snapshot.py` is regenerated with
  `scripts/gen_model_pricing_snapshot.py`. A body that does not look
  like models.dev (fewer than half the tracked providers) is rejected
  and the served catalog kept. A new preset needs a `PRESET_PROVIDERS`
  entry — `tests/unit/test_model_pricing.py` fails until it has one.

## Provider-preset catalog sync

The AI provider presets (Settings → AI) auto-sync from `main` every 48 h: fetch `app/ai/provider_presets.py` → literal-only `ast` parse (never executed) → per-preset validation (env-key allowlist, https-only base URL, `load_global_mcp` forced off; a rejected preset falls back to the bundled copy, which is also the floor for omitted ids) → sha256-keyed atomic cache → served cache-first, bundled on a miss. Presets only seed newly created profiles. Full design, env vars and trust model: [backend.md § Auto-synced preset catalog](backend.md#auto-synced-preset-catalog-aiprovider_presets_syncpy).

## System Scheduler Jobs

`start_scheduler()` in `app/scheduler/cron.py` registers a fixed set of system-only APScheduler jobs (none of these correspond to a row in the `schedules` table, so they don't appear in the UI):

| Job id | Trigger | Source | Purpose |
|--------|---------|--------|---------|
| `waiting_checker` | every 15 min | `app/scheduler/waiting.py` | Check `WAITING` tasks for email/condition triggers; time out stale waits |
| `email_sync` | every 5 min | `app/scheduler/email_sync.py` | Sync configured email accounts into per-account SQLite DBs |
| `plan_reaper` | every 5 min | `app/scheduler/plan_reaper.py` | Fail schedules stuck in `plan_status='planning'` |
| `stall_reaper` | every 1 min | `app/scheduler/stall_reaper.py` | Terminalize RUNNING tasks whose agent can no longer progress |
| `task_cleanup` | daily 03:30 server-tz | `app/scheduler/task_cleanup.py` | Auto-delete terminal leaf tasks past `AppSettings.task_retention_days` (see [docs/tasks.md § Task deletion + auto-cleanup](tasks.md#task-deletion--auto-cleanup)) |

**stall_reaper's authority split** — who may declare "this request is dead": while the agent **process is alive and input-capable**, the CLI owns the in-flight request, and every spawn env arms the CLI's own dead-stream machinery (`ProfileManager.get_env_for_profile` → `ensure_stream_liveness_env`): `CLAUDE_ENABLE_STREAM_WATCHDOG=1` + `CLAUDE_STREAM_IDLE_TIMEOUT_MS=420000` (7 min) make the CLI abort a stream with no chunk for 7 min (TTFB counts, every chunk resets it) and fall into its non-streaming fallback, with each failed attempt announced on stdout as `system/api_retry`; `CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES=1` forwards every raw SSE part (message_start / deltas / pings) as a stdout line, so a healthy compose bumps `updated_at` per part instead of going silent. The reaper only fires after **true stdout silence** has outlasted that watchdog bound + recovery headroom (~200 s retry backoff + 60 s poll + 60 s heartbeat throttle) — **13 min with presets** — instead of the earlier `API_TIMEOUT_MS + 2 min` (~52 min), which only bounded the headers phase. A slow first token is structurally inside the bound: a healthy agent composing a long report after a large `tool_result` can be silent for minutes (observed 3–4 min live), and `updated_at` staleness alone can't distinguish it from a dropped stream. When there is **no session, the process has exited, or the process is past its turn terminator** (stdin closed — the post-close kill escalation in `app/ai/claude_backend_turns.py` disposes of it), nothing can bump `updated_at` any more and the flat 3-min silence rule applies. Pending `AskUserQuestion`s are always skipped (the operator may take hours).

## Ziniao on WSL

WSL **cannot** auto-launch Ziniao because Electron's Node.js V8 rejects unknown `--` flags before the app code runs. The workflow is:

1. User downloads `ziniao_webdriver.bat` from `GET /api/ziniao/launcher` (or uses the copy in the repo root)
2. User double-clicks the `.bat` on Windows — it auto-finds the exe, kills any existing Ziniao, launches in WebDriver mode, and verifies the HTTP API
3. Once Ziniao is running, WSL connects via the gateway IP (auto-detected by `_get_ziniao_host()` from `ip route`)

If Ziniao is already running with the correct port, everything works automatically. The backend only raises errors when Ziniao is unreachable, guiding the user to the launcher script.

## CDP Proxy Architecture

Each Ziniao store gets a stable CDP multiplexing proxy (`app/browser/cdp_mux_proxy.py`) that listens on `127.0.0.1:{proxy_port}` and connects upstream (WebSocket) to the actual Ziniao `debuggingPort` (which changes on each `startBrowser` call). On WSL, the proxy connects to the Windows gateway IP instead of localhost. The browser-use wrapper's `BU_CDP_WS` env var (0.13; was `--cdp-url`) always points to the stable proxy port with a per-task client ID.

**Multi-client support**: Multiple browser-use CLI processes connect simultaneously, each via `ws://127.0.0.1:{proxy_port}/client-{task_id}`. The proxy multiplexes all clients onto a single upstream WebSocket to the browser, using:
- **Request ID rewriting**: Client request IDs are remapped to globally unique IDs; responses are routed back to the originating client
- **Session-based event routing**: CDP `sessionId` → owning client mapping ensures page events only reach the tab owner
- **Target filtering**: `Target.getTargets()` responses filtered per-client; cross-client `attachToTarget`/`closeTarget` blocked
- **Pending event cache**: Handles the race where `Target.attachedToTarget` arrives before `Target.createTarget` response (common with browser-use's `setAutoAttach`)

**Lifecycle**: The proxy stays alive while any task uses it. Client disconnect (clean or crash) triggers cleanup: close the client's tabs, remove routing entries. Startup cleanup closes orphan tabs from prior server crashes.

A legacy TCP relay (`CDPTcpProxy` in `cdp_proxy.py`) is kept as fallback for single-client scenarios.

## Ad task declarations

An ad-task phase declares what it is for **before** working
(`vibe_seller_declare_ad_task` → `POST /api/tasks/{id}/ad-declaration`):

- **kind** ∈ `audit` / `change` / `create` / `execute` / `investigate` —
  there is no `edit`. `change` is a change the **user** named, row and
  value ("lower widget-006 to 1.00"): nobody is judging anything, so the
  phase owes its rows and nothing else. "Change these three bids" —
  where the value is yours to pick — is a one-campaign `audit`.
- **scope** = `combos` (platform × country), `campaigns`, `products`.
  Absent/empty `combos` means the **whole store** — declaring nothing
  declares everything, so an unqualified "audit the ads" owes every
  marketplace the store sells on. `products` is a human-facing anchor
  ("the widget-006 family") that gates nothing on its own. A scope entry
  NAMES what is in scope; exclusion text ("except X", "not Y") is refused
  at the tool boundary, because exclusions are implied by omission.

Rows are **append-only** (`ad_task_declarations`, one per phase), and the
accepted declaration is mirrored to `AD_TASK.json` in the task workspace
so the stop gates can read it without a DB session — the row stays the
authority, never the file. What a phase owes is read from the
declaration, never from the shape of its output: only `audit` owes
marketplace coverage (`ad_declaration.owed_combos`), a `change` the user
named owes no coverage and no store-rule judgement on its rows (their
number beats a default — see `ad_changes.rule_violations`'
`store_rules=False`), `investigate`
proposes nothing, and no declaration means no inferred obligation. What
`change` does NOT shed is the confirmation: the turn ends showing the
rows, only a user reply opens `execute`, and the frozen approval still
judges the execution.

Like every check, one round: a same-turn re-declaration that widens reach
or sheds an obligation, and `execute` without a user reply to a declared
`audit` or `change`, are answered once (409, naming what that normally
needs); the same call again is recorded as the agent declared it. Narrowing at the same
kind and the `investigate` → `audit` correction are always accepted
(`ad_declaration.supersedes` — a re-declaration may never reduce what the
phase owes nor widen its reach).

## Report-run declarations

The monthly report check takes its trigger from the same place. A store
task declares the run it is doing —
`vibe_seller_declare_report_run` → `POST /api/tasks/{id}/report-declaration`
— with **one argument, the month** (`YYYY-MM`), and only when the task is
the month-end download rather than a lookup. That declaration is the whole
trigger: `app/deliverables/gate.py` returns immediately for a task with no
declaration, whatever its workspace holds.

It used to engage on a folder scan — any `reports_*` directory in the task
workspace made the run a report run — and that inferred an obligation from
how the agent had named its own files. An ad-hoc "show me current Amazon
sales" filed its CSVs under `reports_sales_2026-11/` and was refused for a
missing monthly transaction report. Whether a task is a monthly run is
exactly the fact no server-side heuristic can recover from output shape,
and exactly the fact the agent alone can state.

What is NOT declared is the file list: `derive_manifest` builds it from the
store row (platform countries, capability flags) and the calendar, and the
declare route answers with that list (`expected_files`) so the agent works
against the same set the gate grades. The month may be the derived
reporting month (the calendar month before the task was created) or any
earlier complete month, which is how an explicit back-fill gets checked at
all — the folder scan could only ever test last month. The current month
and any future one are refused, naming the expected month: a month that has
not ended cannot be delivered complete, and letting the agent pick the
month would let it pick the set of files it happened to have collected.
Rows are append-only (`report_run_declarations`) and every declared month
is owed, so re-declaring can only ADD an obligation — which is why there is
no same-turn ratchet here, unlike the ad kinds. The check is one round like
every other: gaps refuse the first submit and ride the next as caveats.

A declaration describes the **run** that made it, so `POST
/api/tasks/{id}/retry` deletes these rows exactly where it deletes the
run's messages and ad declarations: retry wipes the task workspace, and a
surviving row would hold the fresh run to a month whose files can no longer
exist. Nothing is mirrored to the workspace (unlike `AD_TASK.json`) —
`check_deliverables` has a DB session, so the row is the only authority and
there is no second surface to keep honest.

Declaring `execute` freezes the `CHANGES.tsv` the phase starts from (its
own, or its nearest ancestor's) into `data/approved_changes/` — the
approval its execution is checked against — and posts that frozen list to
the conversation, headed "approved" or "NOT confirmed" according to
whether the user replied to the `audit` or `change` it came from. Earlier
freezes are kept, so changes made under a previous approval stay
approved.

Store-rule thresholds (`acos_no_lower`, `cpc_floor_multiplier`,
`scale_roas`, `change_cooldown_days`, `reconcile_tolerance`,
`noon_reconcile_floor`) are defaults, not laws: one `key: value` line in
`stores/<slug>/notes.md` overrides one (`ad_rules.resolve_rules`).

## Result Gates (`set_task_result`)

When the agent calls `vibe_seller_set_task_result`, the server retains the
submission and then runs a short chain of checks before accepting it
(`app/routers/tasks.py::set_task_result`, code under `app/ai/stop_gates/`).
Each check returns `None` or a `GateDeny` (`reason` for the agent, plus
structured `gaps` / `contradictions`). A refusal is a 400 the agent reads
and acts on. The full order is in
[api.md § what `set_task_result` checks](api.md#post-apitaskstask_idresult--what-set_task_result-checks).

Only checks that protect money or data remain; nothing grades the report's
prose, format or language.

Every check gets one round and a submit is refused at most once, listing
everything found; the next submit ships with the findings as caveats
(`review_gaps`); contradictions also get a banner at the top of the
result and of the retained submission. The per-task round counters reset
when a result is accepted and when the task ends, so the next turn gets
its own round.

| Check | Concern |
|---|---|
| `listing_upload_gate.py` (`amazon-listing` skill) | A batch this turn's upload attempt produced has a clean verdict. Armed by the upload helper's own `UPLOAD_ATTEMPT.json` — a template download is reading, not uploading — and it runs only for sessions that loaded that skill. |
| Deliverables (`app/deliverables/gate.py`) | A run that declared itself a monthly report download (`vibe_seller_declare_report_run`) delivered every file that month's derived manifest names. No declaration → not checked: the trigger is the agent's word, never the workspace's folder names. |
| `ad_changes.py` | `CHANGES.tsv` obeys store rules (`ad_rules.py`, overridable per store via `key: value` lines in `stores/<slug>/notes.md`) — judged on an `audit`'s recommendations, not on a value the user named (`change`); `investigate` proposes nothing; `EXECUTION_LOG.tsv` matches the frozen approvals in `data/approved_changes/` (contradiction). |
| `ad_coverage.py` | An `audit` covered every owed marketplace (`ad_declaration.owed_combos`), drilled live campaigns, reconciled search terms with targeting; impossible spend is a contradiction. |
| `review_completeness_review` / `review_output_gate` (`review-collect` skill) | Every enumerated product was collected into well-formed `reviews/v1` JSON — the downstream sync upserts these files, so a half-finished dump would silently drop ratings. |

The ad checks read fixed-header TSVs (`app/ai/stop_gates/ad_tables.py`),
not the report; the agent-facing contract is
`app/skills_v2/amazon-ads/references/data-contract.md` (`noon-ads` ships
a byte-identical copy). When a phase
declares `execute` (normally after the user replied to an audit), the `CHANGES.tsv` it
starts from (its own, or its parent audit's) is frozen to
`data/approved_changes/<task_id>.tsv`, and the execution is checked
against that copy: the agent first brings `CHANGES.tsv` in line with
what the user approved, amended values included; the frozen list is
posted to the conversation, and editing `CHANGES.tsv` afterwards does
not move it.

**What refuses outright.** Submission checks never do — they are one
round each. Hard stops are only three, and none of them grades a report:
a Bash command whose kill scope is broader than the calling task's own
subprocess tree (`app/ai/bash_safety.py`, a PreToolUse deny); a catalog
write whose table is unparseable or that carries rows outside its own
tier (`app/workspace/catalog_validation.py` at the `PUT
/api/workspace/file` boundary — the remaining catalog content rules are
told once, like every check); and a knowledge-cleanup write that adds or
rewords a line instead of deleting one
([workspace.md § One-time knowledge cleanup](workspace.md#one-time-knowledge-cleanup-after-an-upgrade)).

### Why at the MCP tool call, not the Stop hook

Some backends (notably DeepSeek under certain compaction conditions) never emit a `Stop` event, so a Stop-hook-only gate would be silently skipped. `set_task_result` is the one tool every successful task path calls, so the gate fires there. The Stop hook (every session) only refuses to end a turn while async subagents are still running.

### Adding a gate

Domain gates run only when a loaded skill names them in its `gates:` frontmatter. Either ship the module in the skill's own `gates/` directory (discovered by `app/ai/skill_gate_loader.py` at startup), or register it from a plugin with `ctx.register_gate(name, module)`. The module exposes `check(final_result, task_id=None, rules=None) -> GateDeny | None`. Add a unit test alongside. Which skills a task bound is recorded per task (never per session) under `data/gate_bindings/`, so a retry-resume session that edits the report without reloading the skill still faces the gates the original session bound.

## Plugin Architecture

The codebase is split into a **public OSS core** (`vibe-seller`, knows no
customer) and **private per-customer plugins** (external wheels, on top of
core). `app/plugins.py` is the inversion-of-control seam that makes this
possible: core reads its gates, PreToolUse guards, browser backends,
skill sources, prompt fragments, and background services from a registry
that plugins populate at startup, rather than importing customer code
directly. Removing a customer = not installing its wheel — zero core
edits, nothing to re-leak. (Design-of-record: `plugin_design_v2.md`;
module reference: [backend.md § Plugin Framework](backend.md#plugin-framework).)

### The contract

A plugin subclasses `Plugin` and implements `install(ctx)`, calling the
`ExtensionContext.register_*` methods. Registration is **declarative** —
`install` records contributions into `ctx` and never touches the live
FastAPI app, so the registry can be built lazily in app-less unit tests.
App-level effects are applied separately by reading the populated
context: `main._wire_plugins` mounts plugin routers / frontend-bundle
routes; the lifespan starts/stops background services.

| Contribution | Registry method | Read by |
|---|---|---|
| Result gate (`set_task_result`) | `register_gate` | `stop_gates.get_registered_gates` → `resolve_skill_gates` (see [Result Gates](#result-gates-set_task_result)) |
| PreToolUse Bash guard | `register_pretool_gate` | `bash_safety.first_bash_deny` |
| Browser backend | `register_browser_backend` | `BrowserManager._get_backend` |
| Skill directory | `register_skill_source` | `skills_sync` (core `app/skills` is always first; first-source-wins on a name collision so a plugin can't shadow a core skill) |
| System-prompt fragment | `register_prompt_fragment` | prompt assembly (forward-looking) |
| Background service | `register_service` | lifespan (started as a task, cancelled on shutdown) |
| (retained no-op) | `register_review_marker` | nothing — kept so plugins built against it still load |
| Backend router / frontend JS bundle | `register_router` / `register_frontend_bundle` | `main._wire_plugins` + `GET /api/plugins` |

### OSS builtin vs plugins

The OSS core registers itself as **the builtin plugin**
(`app/builtin_plugin.py:BuiltinPlugin`) through the exact same API a plugin uses — dogfood, and the registry is never empty. It is loaded by
direct import (always present, no entry point needed) and **fail-closed**:
if it can't load, startup aborts rather than running with no safety
gates. External plugins are discovered via the `vibe_seller.plugins`
entry-point group their wheels declare, loaded after the builtin in
deterministic (name-sorted) order, and **fail-open** (a bad wheel is
logged and skipped). Per-customer isolation is at **pack/install** time:
a customer's box installs only that customer's plugin wheels, so other
customers' code is absent at the wheel boundary.

### Frontend extension

A plugin can add dashboard UI without forking the OSS frontend: it
registers a compiled JS bundle (`register_frontend_bundle`), `GET /api/plugins`
lists the active bundles, and the OSS React shell dynamically loads each
one (`requires_early_init` bundles resolve before the app makes API
calls). Ships inert in PR-1 (empty list in an OSS-only install); the
React loader lands when the first plugin UI does.

## Amazon Ads API service

A store can do its advertising over a hosted ads service instead of the
browser. vibe-seller holds no Amazon credential and speaks no Amazon API:
the service holds the Amazon authorization and does the calls.

- **Binding** (`app/ads_client.py`): nothing to configure. The service
  address is the constant `SERVICE_URL`, and the installation registers
  itself (`POST /installations`) the first time Settings → Integrations
  syncs the stores — never at boot, so an installation that does not use
  ads never contacts the service. The key is stored encrypted in
  `AppSettings` and never returned by any route. The key alone reaches no
  store: each store is reached only after its owner consents on Amazon,
  and is revoked per store (`POST /api/ads/stores/{id}/unbind`, admin
  only).
- **One advertiser, several installations**: Amazon's profile id says two
  stores are the same advertiser (names and local ids are each machine's
  own). Each installation that the owner consented from reads the same
  history; revoking on one leaves the others working.
- **Write-back**: an authorized store's marketplaces are added to its
  `platforms` / `countries` / `platform_countries` — Amazon's word beats a
  guessed list. Nothing is removed, on sync or on revoke.
- **Key secrecy**: the agent's one tool, `vibe_seller_ads_call`, goes
  through `POST /api/ads/call`, which adds the key server-side. The key
  never enters the agent's context, and paths that belong to a person
  (`/me`, `/auth-url`, `/assignments`, `/ads-binding`, `/installations`)
  are refused.
- **Authorization**: the OAuth round trip happens on the service's domain,
  so a deployment needs no public address. `POST /api/ads/stores/sync`
  records which stores are authorized: `Store.ads_store_keys`
  (`{marketplace: store_key}`) and `Store.ads_authorized`.
- **store_key resolution**: the agent never supplies a store key. The call
  names a marketplace; `resolve_store_key()` maps it to the key, and
  refuses rather than guesses when a store has several marketplaces and
  none was named. A task bound to a store reaches only that store.
- **File answers**: the service answers a large list as CSV; it is saved
  to `tasks/<id>/ads-data/` and the agent gets the path and row count.
- **Skill routing**: an authorized store's task sees `amazon-ads-api`
  only, any other task the browser `amazon-ads`. See
  [workspace.md § Ads skill routing](workspace.md#ads-skill-routing),
  which also covers how the service-shipped skill is synced.

## Cross-platform support (native Windows)

Vibe Seller runs on macOS, Linux, **and natively on Windows** (no WSL
required). Every platform difference is centralised in `app/platform.py`
(see [backend.md](backend.md#modules)) so the rest of the code stays
platform-agnostic:

- **Process management** — `kill_process`, `is_process_alive`,
  `find_processes_by_pattern` use `psutil` instead of `pgrep`/`ps`/
  `os.kill` (Unix-only). The agent's `_force_kill` signals the POSIX
  process group via `os.killpg` where available and falls back to
  `terminate()`/`kill()` on Windows (where `send_signal(SIGINT)` raises).
- **Paths** — `venv_python`/`venv_executable`/`venv_bin_dir` resolve
  `Scripts/` on Windows vs `bin/` on POSIX; `prepend_to_path` joins with
  `os.pathsep` (`;` on Windows). `safe_chmod` is a no-op on Windows.
- **Agent shell** — the agent invokes `browser-use` through Claude
  Code's Bash tool, which uses **Git Bash** on Windows. So the existing
  **bash** per-store wrapper (`app/browser/wrapper.py`) works unchanged
  on every platform — there is no PowerShell wrapper.

### Upgrade restart

Invariant: **a server never keeps serving code the disk no longer has.**
An upgrade replaces files on disk; without a restart the old process
keeps the old code in memory, and its lazy imports start mixing in new
modules. The server owns the restart (`app/upgrade_restart.py`), so it
holds however the upgrade was run and however the server is supervised
(`vibe-seller start`, `--foreground`, launchd/systemd, `start.sh`):

1. **Detect** — every 10 s, `watch()` compares the version this process
   loaded (`app.version.get_version`, fixed per process) with the one
   installed on disk now.
2. **Drain** — when they differ, `task_queue_scheduler.pause_dispatch()`
   stops starting queued tasks, so live agents can only go down. Queued
   tasks stay `queued` and are re-queued on the next boot.
   `GET /api/version` reports `pending_version` meanwhile.
3. **Restart in place** — once `agent_manager.live_agent_count()` is 0
   (the exact set a shutdown's `stop_all` would kill, so "safe to
   restart?" and "what would it kill?" cannot disagree), it sends itself
   SIGTERM for uvicorn's graceful shutdown, and the lifespan's last step
   `os.execv`s the original command line (`sys.orig_argv`). Same PID —
   pid files and supervisors keep tracking it — same environment,
   host/port and log, new code. The check and the shutdown request run
   with no `await` between them, so no agent can start in that window.

`vibe-seller upgrade` (`app/cli.py`) only waits: for each server it
started (pid files) it polls `/api/version` until the new version
answers, reports a server still draining for running tasks, and exits
non-zero if a server that can restart itself never picks the upgrade
up. A server older than this mechanism has no `pending_version` and is
told to restart once by hand — the one upgrade *into* this version is
run by code that predates it.

**Windows** is excluded (no in-place exec; uvicorn `--reload` workers
are too). Upgrades there go through the installer: `PrepareToInstall`
stops the server and tray, interactive installs restart them from the
finish page ("Open now"), and a silent install relaunches the tray —
which starts the server — when one was running before
(`AppWasRunning`). The installer does stop running tasks; the tray's
update dialog is the place it is started from.

### In-app auto-update (background download, click to apply)

`app/auto_update.py` owns one state machine —
`idle → downloading → ready → restarting`, or `error(message)` — and
the lifespan starts its loop next to `upgrade_restart.watch`: first
check ~60 s after boot, then every 6 h. It reuses the existing check
and channel rules (`app/release_channel.py`) — a stable install is
never offered a nightly. The whole feature is a *fetcher*,
not an installer: it only ever stages bytes; applying happens when an
admin clicks "重启并更新" in the UI.

- **Support is install-shaped** (`supported()`): native Windows only
  from the installer bundle (`unins000.exe` beside the venv —
  `sys.prefix` is `{app}\.venv`), macOS/Linux only from a uv tool
  (`uv-receipt.toml` in `sys.prefix`, `uv` on PATH), channel `dev`
  never. pip / docker / source installs fall through to the old popup
  (copy command / download link) — with the same merged release notes.
  `VIBE_SELLER_AUTO_UPDATE` is the kill switch: default on, default
  **off under pytest/CI** unless explicitly `1` (telemetry's
  convention). No settings toggle by design.
- **Windows staging** (`app/windows_update.py`, shared with the
  tray): the offered asset's `size` + GitHub `digest` (`sha256:<hex>`;
  a mock may omit it → size-only) are verified against
  `<VIBE_HOME>/updates/<version>/VibeSeller-Setup.exe`, resumable
  through a `.part` with HTTP `Range`; `install-meta.json` beside it
  records what the file was verified under, so a boot scan marks a
  newer verified stage `ready` without touching the network and the
  tray's 检查更新 reuses it instead of downloading again. Only the
  newest version dir is kept.
- **macOS/Linux prefetch**: `uv tool run --from
  vibe-seller==<v> --python sys.executable vibe-seller --version`
  resolves + caches the target's wheels **without touching the
  installed tool** (exact pins admit pre-releases, so nightlies need
  no `--prerelease allow` — same rule as `app/cli.py`). Progress is
  indeterminate there; the proof of a warm cache is an offline rerun
  (`UV_OFFLINE=1`, `tests/releases/test_auto_update.sh` step 5).
- **Mock hooks for tests/CI**: `VIBE_SELLER_PYPI_JSON_URL` and
  `VIBE_SELLER_GITHUB_RELEASES_URL` (URL *or* local path) point the
  version check and the release-notes fetch at local fixtures — the
  same role `VIBE_SELLER_RELEASES_URL` already plays for the Windows
  installer source.
- **Apply** (`POST /api/system/update/restart`, admin, 409 unless
  `ready`): Windows launches the staged installer detached
  (`DETACHED_PROCESS` + `CREATE_NEW_PROCESS_GROUP`, trying
  `CREATE_BREAKAWAY_FROM_JOB` first so a CI job object can't take the
  installer down) with `/SILENT /SUPPRESSMSGBOXES /NORESTART` — a
  progress window, no wizard questions; the server must *not* stop
  itself (that's `PrepareToInstall`'s job). macOS/Linux spawn the
  real `vibe-seller upgrade` detached (`start_new_session=True`) and
  a watcher polls `upgrade_restart.installed_version()` — the moment
  the disk changes it calls `request_restart()` right away (no drain:
  the admin already accepted interrupting running tasks, and the UI
  warned first). A failed upgrade leaves the OLD version serving and
  moves the state to `error`.
- **Release notes** for the whole gap (`app/release_notes.py`): every
  release body between current and latest is parsed
  (`## Highlights` zh/en paired 1:1 across the `---` line, `## Notes`
  ending at `---`/`**Full changelog`/next `## `) and merged **by
  section**, newest version first; a body outside the format becomes
  one highlight item carrying the whole body. `GET
  /api/system/update-check` returns them as `highlights` / `notes`.
- Tests: unit/workflow (`test_auto_update.py`,
  `test_windows_update_staging.py`, `test_release_notes.py`,
  `test_wf_update_restart.py`); real end-to-end on Linux/macOS
  (`tests/releases/test_auto_update.sh`, wired into
  `cli-upgrade.yml`) and on the Windows host (auto-update gate in
  `windows-installer.yml`).

### Native Windows installer

`installer/windows/` builds a self-contained `VibeSeller-Setup.exe`
(Ollama-style: per-user install + a system-tray launcher that starts the
server on login). It bundles a relocatable CPython
(python-build-standalone), the app + deps as offline wheels, `uv`,
full **Git for Windows** (git + the `bash.exe` Claude Code's Bash tool
needs, plus `curl`/`perl` for the browser-use wrapper), and the `claude`
CLI. It
drives the user's installed Chrome — no bundled browser. Built on a
GitHub-hosted `windows-latest` runner
(`.github/workflows/windows-installer.yml`); see
`installer/windows/README.md`.
