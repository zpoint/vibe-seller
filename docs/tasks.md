# Tasks

## Task Data Persistence

- **Plan** (`task.plan`): Saved by design agent, rendered as markdown in frontend
- **Result** (`task.result`): Saved by execute agent on completion, rendered as markdown
- **Todos** (`task.todos`): JSON-serialized TodoWrite state, persisted on each update
- **Messages** (`task_messages` table): Full agent chat history, reloaded on task select
- **Steps** (`task_steps` table): Only from simple executor; agent tasks use todos/messages
- On **retry**: all steps, messages, logs, todos, result, plan, and plan_history are cleared; per-task workspace is wiped (`prepare_task_workspace(clean=True)`) for a fresh start
- Plan/result language auto-detected from task title (Chinese → 中文, else English)

## Task Status Lifecycle

**Auto mode** (default): `pending` → `running` → `completed` / `failed` / `waiting`

**Plan mode** (`plan_mode=true`): `pending` → `designing` → `planned` → (user confirms via execute-plan) → `running` → `completed` / `failed` / `waiting`.

**Plan-only tasks** (`is_plan_only=true`): a special plan-mode variant that authors a reusable plan for a Schedule and terminates at `completed` without ever running. Flow: `pending` → `designing` → `planned` → (user approves via execute-plan) → `completed`. The approval hook writes the plan to `Schedule.plan`, bumps `Schedule.plan_version`, sets `plan_status='ready'`, and responds `deny` to the `ExitPlanMode` control request so the agent halts instead of entering bypass mode. See [Plan-at-creation lifecycle](subsystems.md#plan-at-creation-lifecycle).

**Scheduled fires** (children of a plan-mode Schedule): auto-approve the plan at execution since the human already reviewed it at creation time. They re-inject per-store context (L3 catalog, bookmarks, emails) and reuse `Schedule.plan` as-is.

Note: `queued` is used for store-task launches from `pending`/`waiting` states. `submit()` only transitions to `queued` from those states — `planned` tasks enqueued for execution stay `planned` in the DB so `_approve_plan_request` can transition them directly to `running`. The `execute_plan` endpoint explicitly sets `planned` → `queued` before enqueueing to prevent duplicate submissions. No-store tasks launch directly via `_auto_run_task()`.

- `waiting`: long-running tasks paused for external events (e.g., marketplace case response, listing approval). Triggered three ways: (1) agent signals via `wait-condition` block in result text, (2) agent exits with incomplete `TodoWrite` items (`_has_incomplete_todos` → `mark_waiting_for_input`, `check_strategy: manual`), or (3) agent exits while an `AskUserQuestion` is still outstanding (`park_waiting_for_pending_question` in `app/task_runner.py`) — the question payload is persisted into `wait_condition.pending_question` so the operator can answer later via the UI. System periodically checks email conditions or user manually wakes.

### WAITING with a pending question

When the agent subprocess exits while holding an unanswered `AskUserQuestion`, `AgentSession` snapshots `_pending_questions` into `_last_pending_questions` (both in `stop()` and the stream-finally path in `app/ai/claude_backend_stream.py`), and `auto_run_task` checks that snapshot before the FAILED transition. If a snapshot exists, the task is parked in `WAITING` with a `wait_condition` shaped like:

```json
{
  "reason": "Agent asked a question; awaiting operator input",
  "check_strategy": "manual",
  "waiting_since": "...",
  "max_wait_days": 7,
  "pending_question": {
    "request_id": "req-xxx",
    "questions": [{"question": "...", "options": [...]}]
  }
}
```

The operator answers via `POST /api/tasks/{id}/questions/answer` exactly as for a live session. The endpoint (`app/routers/tasks_conversation.py`) has two branches: if the agent is still running it forwards over IPC; if the session is dead and the task is WAITING with a matching `request_id`, it persists the answers into `wait_condition.answers`, flips WAITING → QUEUED, and re-queues. Store tasks go through `task_queue_scheduler`; no-store tasks are launched directly via `auto_run_task`.

On re-dispatch, `auto_run_task` calls `maybe_inject_pending_answers()` which composes a user-turn prefix from `wait_condition.answers` + the stored questions, clears `wait_condition`, and passes `resume=True` so the spawned session runs `claude --resume <task.session_id>` and picks up the transcript. The resumed agent sees the operator's answers as the next user message and continues.
- Transitions: `running` → `waiting` (agent signals wait OR incomplete todos), `waiting` → `queued` (woken) / `failed` (timeout)
- State machine defined in `app/task_states.py` (backend) and `frontend/src/taskStates.ts` (frontend UI config)
- Backend: `TaskStatus` enum, `TRANSITIONS` table, named groups (`STOPPABLE`, `ACTIVE`, `WAKEABLE`, etc.)
- Frontend: `TASK_UI` config table maps status → which buttons/tabs/panels are visible
- Contract test ensures both sides agree on status values
- Waiting checker: APScheduler interval job (15 min) in `app/scheduler/waiting.py` checks email conditions, times out stale waits
- Wake endpoint: `POST /api/tasks/:id/wake` with optional `{message}` body resumes waiting tasks

## Parent / Sub-Task Hierarchy

Orchestrator (non-store) tasks can create per-store sub-tasks via `parent_task_id`. This enables cross-store workflows where a single parent task fans out work to individual stores. The parent also has a store-less `web` browser (see [docs/browser.md](browser.md#store-less-web-browser)) for neutral public web work (search, tracking/logistics) it can do directly; anything touching a store's seller center still goes to a sub-task.

- **Parent task**: A non-store task (`store_id=None`) that coordinates the overall workflow
- **Sub-tasks**: Per-store tasks linked via `parent_task_id`. Each sub-task must have a `store_id`
- **Default fan-out**: One sub-task per store. Country switching happens within a single sub-task's browser session (not via separate sub-tasks per country)
- **Query**: `GET /api/tasks?parent_task_id=` returns all sub-tasks for a given parent
- **Wait-for-children**: When the parent agent finishes creating sub-tasks, the parent transitions to `WAITING` (with `wait_condition: {"strategy": "children"}`) instead of completing immediately. As each sub-task reaches a terminal state (completed/failed), `_check_parent_completion()` checks all siblings — when all are terminal, the parent auto-completes with an aggregated summary of child results

## Execution Pipeline

Tasks execute on creation via `_auto_run_task()`. The mode depends on `plan_mode`:

### Auto Mode (default, `plan_mode=false`)

```
POST /api/tasks → status: "pending"
  → _schedule_or_run(task_id, store): store tasks → TaskQueueScheduler.submit() → "queued"
    No-store tasks (store_id=None) → asyncio.create_task(_auto_run_task()) directly
    Fanout L2 tasks are the exception: submitted to queue with store_id=None, bypass browser/session
  → Queue dispatch infers launcher: plan exists → _execute_planned_task, woken → _execute_woken_task, else → _auto_run_task
  → _auto_run_task():
    1. browser_manager.write_browser_config_for_store() (store tasks) /
       write_web_browser_config() (no-store tasks → bin/_web wrapper)
    2. knowledge_sync.fetch()
    3. agent.run(mode="auto") — bypassPermissions from start:
       a. Agent starts with --permission-mode bypassPermissions
       b. SDK hooks: AskUserQuestion → forwarded to backend, all others auto-approved + circuit breaker
       c. No ExitPlanMode dance — agent has full tool access immediately
       d. status: "running" (set before agent starts)
    4. Done → "completed" / "failed" / "waiting"
    5. Guard: agent exits without result → "failed" (agent_empty_result)
```

### Plan Mode (opt-in, `plan_mode=true`)

```
POST /api/tasks → status: "pending"
  → _auto_run_task():
    1-2. Same setup steps
    3. agent.run(mode="plan_then_execute") — native plan mode:
       a. Agent starts with --permission-mode plan (read-only)
       b. Agent plans → calls ExitPlanMode → status: "designing"
       c. Backend intercepts via hooks, captures plan → status: "planned"
       d. Wait for user approve/reject
       e. Approve sends SetMode → bypassPermissions → status: "running"
       f. Agent continues executing in same session with full access
    4. Done → "completed" / "failed" / "waiting"
```

### System Prompt

Both modes use `DESIGN_SYSTEM_PROMPT` (knowledge recall, critical thinking, approach selection, subagent rules). Auto mode strips the `Phase 5 — Plan Output` and `ExitPlanMode Rules` sections via `<!-- PLAN_MODE_ONLY -->` delimiters. Post-task knowledge capture is delivered via the Stop hook (not embedded in the system prompt).

Each agent runs in a per-task isolated directory (`~/.vibe-seller/tasks/{task_id}/`) with symlinked shared resources — see [workspace.md](workspace.md#per-task-workspace-isolation).

No manual buttons needed. Users see: progress indicator + Stop button + Retry on failure or completion (RETRIABLE = `{FAILED, PENDING, COMPLETED}`). Toggle "Review Plan" in the task detail footer to switch between modes.

### Follow-up on Completed/Failed Tasks

Users can send follow-up messages to tasks that have already completed or failed — to continue incomplete work, ask questions about results, or give additional instructions. The chat input is enabled for both terminal states.

When a message is sent to a `completed` or `failed` task:

1. Backend clears stale `error`/`error_category` (if failed)
2. **Auto mode** (`plan_mode=false`): clears stale result/todos/wait_condition, transitions to `running`, starts agent with `mode='auto'`
3. **Plan mode** (`plan_mode=true`): transitions to `designing`, starts agent with `mode='plan_then_execute'` and `resume=True`
4. If agent fails to start, status reverts to the previous terminal state with error fields restored

This differs from the `waiting` → `queued` wake path: waiting tasks are explicitly paused by the agent for external events, while follow-ups are user-initiated continuation of finished work.

### Fresh-session context-rot guard

`resume=True` is a request, not a guarantee. When the task transcript
exceeds `VIBE_FRESH_SESSION_MSG_LIMIT` (default 400 `task_messages`
rows), the follow-up handler in `tasks_conversation.py` treats the
session as **non-resumable** and starts a **fresh CLI session** seeded
with the compacted context the non-resume branch already builds
(`app/ai/compaction.py`: full raw history → a JSON file under
`~/.vibe-seller/task_history/`, last 5 messages inline, plan inline,
plus an instruction to read the file). The task workspace — files on
disk, markers, reports — is unchanged.

Why fresh instead of resuming-and-summarizing: the observed failure
mode on long transcripts is context **rot**, not size — superseded
conclusions and stale artifact paths persist as "facts" and the agent
re-verifies dead state. A model-written summary *distills* that
narrative; a fresh session re-grounds in current reality (disk state,
live pages) and pulls raw history on demand. Division of labor:
**Claude Code's native auto-compaction handles intra-turn context
pressure inside a session; this guard handles inter-turn rot between
sessions.** The limit is a row-count heuristic — tune via the env var.

### Plan Feedback

Users send feedback via the unified chat input at the bottom of the conversation view — no separate design buttons or plan-editing UI. When a message is sent while the task is in `planned` status:

1. Backend calls `reject_plan()` on the running session — agent stays in plan mode, incorporates the feedback, and re-plans (PLANNED → DESIGNING → PLANNED cycle)
2. If the session has died, a fresh `plan_then_execute` session starts with the feedback injected as the opening prompt

## AI Agent Backend

The primary execution path routes through the `AIAgentBackend` abstraction in `app/ai/`:

- **`base.py`**: `AIAgentBackend` ABC — `run()`, `stop()`, `submit_answer()`, `send_message()`, `is_running()`
- **`claude_backend.py`**: Claude Code CLI subprocess implementation
  - `--add-dir ~/.vibe-seller` — loads workspace skills + knowledge
  - `--output-format stream-json` — structured streaming output
  - `--permission-mode plan|bypass` — native plan mode or full access
  - `--permission-prompt-tool stdio` — hook/control protocol for plan approval
  - `approve_plan()` / `reject_plan()` — plan approval within a running session
  - Post-task reflection (execution) — auto-updates knowledge/skills via REFLECTION_PROMPT
  - **Control protocol**: ExitPlanMode triggers `hook_callback` → respond `permissionDecision: 'ask'` → CLI sends `can_use_tool` → handler saves plan, sends `{behavior: 'allow', updatedPermissions: [SetMode]}` or `{behavior: 'deny', message: '...'}`
  - **stdin lifecycle**: stdin stays open during planning (multi-turn feedback); closed via `_executing` flag only after plan approved and execution result received
  - **`_emit_lock`**: serializes message persistence per session to prevent seq/timestamp races
  - **Session-end signalling**: each session exposes two `asyncio.Event`s — `done` (idempotent end-of-session signal, typically set from `_stream_output`'s `finally` but also from `stop()`'s defensive early-return path; `asyncio.Event.set()` is idempotent, so extra calls are harmless and waiters observe only the first) and `plan_saved_event` (set when `_save_design_plan` commits a plan, cleared on approve/reject so reuse doesn't short-circuit). `_wait_for_session_end` in `task_runner_auto.py` blocks on these instead of polling `is_running()`. No time-based backstop: `_stream_output`'s `finally` is a Python-level guarantee and `AgentSession.stop()` already caps subprocess-exit latency via signal escalation; `_recover_from_db` in `app/scheduler/task_queue.py` is the absolute restart-time backstop for any task stuck in RUNNING/DESIGNING. Shared by `auto_run_task`, `execute_planned_task`, `execute_woken_task`, and `finalize_followup_session`
  - **Resume-failure retry — owned by the orchestrator**, not the manager. Every lifecycle path (`auto_run_task`, `finalize_followup_session`, `execute_planned_task`'s fresh-session branch, `execute_woken_task`) calls one helper `wait_for_session_with_retry(task_id, session)` in `app/task_session_lifecycle.py` (extracted there so both `task_runner_auto.py` and `task_runner_exec.py` import from one place), which does `_wait_for_session_end → _maybe_retry_without_resume → _wait_for_session_end` in a single coroutine. `_maybe_retry_without_resume` checks for the resume-failure pattern (`session._proc.returncode != 0` AND `session.resume_session_id` set AND no `_result_text`); if matched, it clears stale `task.session_id` / `task.result` / `task.error` (so the post-retry finalizer sees only this attempt's outcome — without this, prior `task.result` from earlier rounds misclassifies the retry as success), then calls `agent_manager.retry_without_resume(task_id)` which creates a fresh `AgentSession` inheriting all args (`prompt`, `system_prompt_extra`, `mode`, `profile_id`, `message_history`, `store_slug`, `task_dir`, `auto_approve_plan`, `skip_reflection`, `no_store`) from the prior session. Single owner per task — eliminates the prior race where the manager's hidden `_release_on_done` retry path could either miss a finalize (orphaned retry) or let the orchestrator finalize on prior-run residue while a retry was still running. The detector is heuristic (any rc!=0 startup with resume + no result triggers retry, not just stderr-confirmed `No conversation found`); a future refinement is to surface a typed `session.resume_rejected` flag from `claude_backend_stream` instead of inferring

## Agent Context Injection

All agent prompts go through one function: `_build_system_extra()` in `app/task_runner.py`. Callers pass a `TaskHeader` enum value and get back a `PromptBundle(prompt, system_extra, mode)`.

| `TaskHeader` | Caller | prompt | mode |
|---|---|---|---|
| `DESIGN` | `_auto_run_task` (plan), `design_task` | "Design an execution plan for this task: {title}" | `plan_then_execute` |
| `AUTO` | `_auto_run_task` (auto) | "{title}\n\nDetails: {desc}" | `auto` |
| `EXECUTE` | `_execute_planned_task` | task description | `execute` or `auto` |
| `WOKEN` | `_execute_woken_task` | task description | `execute` or `auto` |
| `CHAT` | `send_task_message` | task description | varies |

Assembly order (fixed for all task types):
1. Base prompt (`design_system.md` with `{workspace_guidance}` filled)
2. Language hint → 3. Waiting instruction → 4. Store/all-stores context → 5. TickTick → 6. System context → 7. Reflection (skipped for catalog sync) → 8. Header extra (plan text) → 9. Caller extra (conversation history)

`start_agent()` (ad-hoc) intentionally skips full context — it's for raw interaction.

**Task dispatch**: All task launch paths (create, retry, continue, execute-plan, scheduled) route through `TaskQueueScheduler`. Store tasks are gated by platform/country compatibility (`RUN`, `RUN_IN_NEW_TAB`, `QUEUE`). No-store tasks (`store_id=None`) always dispatch immediately — they bypass the *per-store* browser config, session tracking, and store CDP proxy. They still get the store-less `web` browser wrapper (`bin/_web`), which lazy-starts its own Chrome + CDP proxy on first use via `POST /api/browser/web/start` (so a no-store task that never browses pays nothing). Per-store tasks can run concurrently when sharing the same platform/country, with CDP-level isolation provided by `CDPMuxProxy`.

## Event Flow During Execution

Auto mode:
```
  ├── emit("task_update", status="running")  — agent starts with full access
  ├── emit("task_message", role/content)
  ├── emit("task_todos", todos)
  └── emit("task_update", status="completed")
```

Plan mode (plan_mode=true):
```
  ├── emit("task_update", status="designing")  — agent enters plan mode
  ├── agent plans (read-only tools)
  ├── agent calls ExitPlanMode
  ├── emit("task_update", status="planned", plan=...)  — plan captured via SSE
  │   └── wait for approve_plan/reject_plan
  ├── approve → SetMode(bypassPermissions)
  ├── emit("task_update", status="running")  — agent executes with full access
  ├── emit("task_message", role/content)
  ├── emit("task_todos", todos)
  └── emit("task_update", status="completed")
```

## Scheduled Tasks

Recurring tasks use the `Schedule` model + APScheduler with MemoryJobStore. The Schedule DB table is the sole source of truth — APScheduler jobs are rebuilt on startup via `rebuild_schedule_jobs()`.

- **Lifecycle** (plan-mode schedules): On `POST /api/schedules` with `plan_mode=true`, the handler spawns an `is_plan_only` Task and sets `Schedule.plan_status='planning'`. The user reviews + approves the plan; the hook commits it to `Schedule.plan` and flips `plan_status='ready'`. Every subsequent cron fire (or manual `/trigger`) copies `Schedule.plan` into a new child Task (status `planned`) so the agent skips the design phase. Editing the schedule's prompt invalidates the plan to `stale` and aborts any in-flight planner; the user calls `/replan` to author a new plan. The fire-gate in `scheduler/cron.py` + `fanout.py` refuses to fire plan-mode schedules unless `plan_status='ready'` (`is_system` and `plan_mode=false` schedules are exempt). Full state machine in `app/plan_states.py` and [docs/subsystems.md](subsystems.md#plan-at-creation-lifecycle).
- **Lifecycle** (non-plan-mode schedules): `plan_status` stays `none`, fire-gate passes through, child task runs in `auto` mode from `pending`.
- **API**: `/api/schedules` CRUD with pause/resume/trigger/replan endpoints. `PUT` requires `plan_version` as an `If-Match` optimistic-lock token (412 on stale); `plan_mode` is immutable after creation (400 on attempted change).
- **Frontend**: TasksView sub-tabs (One-time / Scheduled), schedule detail panel with plan-status badge, Re-plan button, and stale/failed banner surfacing `plan_error`.
- **Cron builder**: `build_cron_kwargs()` converts schedule_type + time + day → APScheduler trigger kwargs
- **Stuck-planning reaper**: `app/scheduler/plan_reaper.py` runs every 5 min, fails schedules stuck in `plan_status='planning'` whose planner task has been idle >30 min. Skips tasks in `waiting` (legitimate user-input wait — handled by `waiting.py` with its 30-day timeout).
- **SSE**: `schedule_triggered`, `fanout_triggered`, `schedule_skipped`, `schedule_waiting_cancelled`, `schedule_plan_ready`, `schedule_plan_timeout` — see [docs/events.md](events.md#sse-event-types)

### Cross-run state (watermark / cursor)

Each run of a schedule is a fresh task — sibling runs share only `schedule_id`, not prompt history. To let cursor-style workflows ("read emails since last run", "process orders newer than last id") resume cleanly, scheduled tasks get two MCP tools backed by the `schedule_state` table:

- `vibe_seller_get_schedule_state(key)` — read the value a prior run persisted under `key`; returns `null` on the first run.
- `vibe_seller_set_schedule_state(key, value)` — upsert the cursor for the next run.

The agent never sees or passes `schedule_id`. The server resolves scope from the calling task (`task.schedule_id`) and returns 400 on non-scheduled tasks. Routes: `GET`/`PUT /api/tasks/{task_id}/schedule-state/{key}`. The PUT uses SQLite `INSERT ON CONFLICT DO UPDATE` so concurrent manual triggers don't race the PK insert.

**Typed keys.** Most keys are opaque strings, but a few are typed to protect agents from their own SQL-formatting mistakes. Today the only typed key is `email_watermark`, which must be a unix epoch seconds integer as a string (regex `^[0-9]{1,15}$`) — **ISO timestamps are rejected**. This avoids a real failure mode: agents wrote ISO watermarks like `'2026-04-17T13:50:57.009101+00:00'`, then truncated them to `'2026-04-17T13:50:57'` when pasting into a `WHERE date > ...` clause; under SQLite's lex comparison the original email date still counted as "greater than" the truncated watermark and re-appeared on the next run. Agents using `email_watermark` are told to query with `WHERE CAST(strftime('%s', date) AS INTEGER) > <value>`. The typed-value map lives in `_TYPED_VALUE_PATTERNS` in `app/routers/tasks.py`.

Two prompt injections glue this to the LLM — both gated on `task.schedule_id` and skipped for catalog-sync runs:

| When | File | Builder | Purpose |
|------|------|---------|---------|
| Pre-task | `app/prompts/scheduled_pretask.md` | `_build_system_extra()` in `app/task_runner.py` | Tells the agent it's scheduled and to call `get_schedule_state` early before doing work. |
| Post-task | `app/prompts/scheduled_watermark.md` | Stop-hook `stop_reflection` callback in `app/ai/claude_backend.py` | Appended to the reflection prompt; nudges the agent to `set_schedule_state` with the highest cursor it fully processed. |

Independent from (and complementary to) the coarse `Previous run completed at …` line in `_build_system_context` — that's an automatic default, the watermark tools are precise and agent-controlled.

## Task Queue Scheduler

Concurrent per-store execution with session-aware scheduling in `app/scheduler/task_queue.py`. Tasks run concurrently by default; only queued when they share the same platform but target different countries (Ziniao country-switch constraint).

| Situation | Decision |
|-----------|----------|
| No running tasks for store | `RUN` — start browser, dispatch |
| Session running, same platform+country | `RUN_IN_NEW_TAB` — parallel in existing session |
| Different platform/country | `QUEUE` — wait for current session to finish |

- Concurrent by default: tasks only queue when same platform + different country (Ziniao country-switch); once blocked, subsequent same-platform tasks for that store wait
- Background `_tick()` loop checks queues every time a task completes or a new task is submitted
- Thread-safe via `asyncio.Lock`
- Recovers queued tasks on restart by scanning DB for pending/queued tasks

## Task deletion + auto-cleanup

Two paths share one helper, `app/task_delete.py`:

- **Manual**: `DELETE /api/tasks/{id}` (Delete button in the task detail header). Cascade-deletes the entire subtree — children, grandchildren, dependent rows in `task_steps` / `task_messages` / `task_attachments` / `task_logs` / `screenshots`, and the on-disk workspace at `~/.vibe-seller/tasks/{id}/`. Best-effort stops any agent first. Returns 409 if any task in the subtree is `designing` / `running` (caller must Stop first). The frontend confirm warns when sub-tasks exist (`tasks.deleteConfirmCascade`). The Delete button is hidden for `is_plan_only` tasks (the frozen plan author of a Schedule — its plan lives on `Schedule.plan` and would orphan the schedule's history).
- **Automatic**: APScheduler cron job `task_cleanup` registered in `app/scheduler/cron.py`, fires daily at 03:30 server-local. Reads `AppSettings.task_retention_days` (default 30; 0 disables). Deletes tasks where:
  - status is `completed` or `failed`,
  - `updated_at` is older than the retention window,
  - `is_plan_only` is `false`, **and**
  - the task is a leaf (no other task points at it via `parent_task_id`).
  Parent rows of fanout / scheduled runs stay around so the user can still see the run summary; only leaves get reaped. The job is hidden — it never inserts a `Schedule` row, so it doesn't appear in the Schedules UI.

The retention setting is configured in **Settings → AI Agent → Auto-delete old tasks**. UI input is clamped to `[0, 3650]`; `PUT /api/settings` enforces the same range (out-of-range values are clamped, non-integers are silently dropped).

Tests: `tests/unit/test_routers/test_tasks_delete.py` covers the endpoint + cascade behavior; `tests/unit/test_task_cleanup.py` covers the scheduler job (retention, leaf-only, `is_plan_only` skip, workspace dir removal); `tests/workflow/test_wf_task_retention.py` covers the settings CRUD.

## Frontend Task Detail UI

The task detail panel uses a conversation-first layout rendered by `ConversationStream`:

- **PlanCard**: Renders the captured execution plan as markdown (`react-markdown` + `@tailwindcss/typography`) at the top of the stream
- **ExecutionSeparator**: Visual divider marking the boundary between the planning phase and execution messages
- **MessageBubble**: Each agent or user message rendered in sequence below the separator
- **Chat input**: Unified input at the bottom — used to send feedback during `planned` status (triggers plan revision) and to send messages to the running agent
- **Progress**: Todo items with progress bar (persisted, survives page refresh)
- **Profile selector**: Compact inline dropdown with icon
- Translations: use "AI" not "智能体" for agent references
