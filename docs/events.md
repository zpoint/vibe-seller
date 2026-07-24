# Events

## SSE Event Bus (`app/events/`)

Real-time event streaming to connected clients using Server-Sent Events (SSE).

### Architecture

```
AI Agent ──emit()──→ EventBus ──queue──→ SSE Endpoint (/api/sse) ──→ Browser (EventSource)
BrowserManager ─────→ (broadcast)──→ Multiple clients
TaskRouter ─────────→
```

### `bus.py` — EventBus

Simple asyncio broadcast pattern:

```python
class EventBus:
    _subscribers: list[asyncio.Queue]

    subscribe() -> asyncio.Queue    # Client connects, gets a queue
    unsubscribe(queue)              # Client disconnects
    emit(event_type, data)          # Broadcast to all subscribers
```

Each subscriber gets its own `asyncio.Queue`. Events are JSON-serialized with `type` field merged into the data payload.

### SSE Event Types

| Event | Fields | Emitted By |
|-------|--------|------------|
| `task_created` | `task_id`, `store_id`, `task` (full `TaskResponse`) | `tasks.py` |
| `task_update` | `task_id`, `status`, `error?` | `tasks.py`, `claude_backend.py` |
| `task_steps_created` | `task_id`, `steps[]` | `runner.py` |
| `step_update` | `task_id`, `step_id`, `step_index`, `status`, `screenshot_id?`, `screenshot_b64?`, `error?` | `runner.py` |
| `task_log` | `task_id`, `log_type`, `content`, `timestamp_ms` | `runner.py` |
| `task_message` | `task_id`, `role`, `content` | `claude_backend.py` |
| `task_todos` | `task_id`, `todos[]` | `claude_backend.py` |
| `task_questions` | `task_id`, `request_id`, `questions[]` | `claude_backend.py` |
| `task_question_interrupted` | `task_id`, `request_id` | `claude_backend_manager.py` |
| `image_request` | `task_id`, `request_id`, `prompt`, `model`, `models[]`, `reference_images[]`, `output_name`, `kind` | `routers/vision.py` |
| `image_generating` | `task_id`, `request_id`, `model`, `kind` | `routers/vision.py` |
| `image_generated` | `task_id`, `request_id`, `path`, `url`, `prompt`, `model`, `kind` | `routers/vision.py` |
| `image_request_expired` | `task_id`, `request_id` | `routers/vision.py` |
| `image_request_interrupted` | `task_id`, `request_id` | `vision.py` |
| `agent_done` | `task_id`, `return_code`, `mode?` | `claude_backend.py` |
| `event_created` | `event_id`, `title`, `status` | `events.py`, `channels.py` |
| `event_updated` | `event_id`, `status`, `old_status?` | `events.py` |
| `event_activity` | `event_id`, `activity_id`, `actor_type`, `action`, `content` | `events.py` |
| `schedule_triggered` | `schedule_id`, `task_id`, `title` | `cron.py`, `fanout.py`, `schedules.py` |
| `fanout_triggered` | `schedule_id`, `batch_id`, `store_count`, `title` | `fanout.py` |
| `schedule_skipped` | `schedule_id`, `reason`, `plan_status` | `cron.py`, `fanout.py` |
| `schedule_waiting_cancelled` | `schedule_id`, `task_id`, `store_id` | `fanout.py` |
| `schedule_plan_ready` | `schedule_id`, `plan_version` | `claude_backend_hooks.py` |
| `schedule_plan_timeout` | `schedule_id`, `task_id` | `plan_reaper.py` |
| `ws_assistant_message` | `role`, `content` | `workspace_assistant.py` |
| `ws_assistant_done` | (empty) | `workspace_assistant.py` |
| `ping` | (empty) | SSE keepalive |

### `task_created` — live task list

Emitted from `POST /api/tasks` after the DB commit and **before** the
task is dispatched. The event carries the full `TaskResponse` so any
subscribed tab can render the row without an extra GET, plus a
top-level `store_id` for cheap filtering against the active view.

The dispatch order matters: `task_created` must precede any
`task_update` for the same task, otherwise a status patch from the
background runner hits a list that doesn't yet contain the row and
gets silently dropped (see `tests/workflow/test_wf_task_created_event.py`
and `frontend/src/__tests__/sseCreateTaskRace.test.tsx`).

The originating tab already adds the task from the POST response;
the SSE handler dedupes by id so it doesn't double-insert.

### Schedule plan lifecycle events

Emitted in addition to the standard `task_update` stream:

- **`schedule_skipped`** — a cron fire was refused by the plan-mode fire-gate. `reason='plan_not_ready'` (plan-mode schedules require `plan_status='ready'`). Frontend can surface a toast for unexpected skips.
- **`schedule_waiting_cancelled`** — a prior `(schedule, store)` task in `waiting` was cancelled so the next fire could create a fresh run. `task_id` is the cancelled task; `store_id` is null for single-mode schedules.
- **`schedule_plan_ready`** — user approved a plan-only Task; plan is persisted on the Schedule. `plan_version` matches the new bumped counter.
- **`schedule_plan_timeout`** — `plan_reaper.py` failed a schedule stuck in `plan_status='planning'` whose planner Task was idle past the threshold.

All four are emitted **after** the DB commit so consumers never observe an event for state that isn't yet persisted.

### Image confirm-gate lifecycle

The `vibe_seller_generate_image` MCP tool is confirm-gated: every call
emits `image_request` and blocks until the user acts, so the model can
never generate without confirmation. One task shows at most one live
card at a time.

- **`image_request`** — a card is proposed; the tool is parked on a
  per-request future waiting for the user to confirm/edit/cancel.
- **`image_generating`** → **`image_generated`** — the user confirmed;
  the kie.ai call runs (can take a minute) then the saved image lands.
- **`image_request_expired`** — a *newer* image request superseded this
  one (agent retry / task retry). The old card is retired; the newer
  request is the live one.
- **`image_request_interrupted`** — the user sent a chat message instead
  of confirming. The parked tool returns at once telling the agent to
  read that message rather than generate the un-confirmed proposal. This
  is distinct from `expired`: nothing replaced the card — the user
  redirected. `task_question_interrupted` is the exact analogue for a
  pending `AskUserQuestion` retired by a chat follow-up.

### Question Format

The `task_questions` event supports structured questions:

```typescript
{
  header?: string;        // Optional category/tag
  question: string;       // The question text
  options?: Array<{       // Available choices
    label: string;
    description?: string;
  }>;
  allow_custom?: boolean; // Allow free-text input (default: true)
}
```

### Design Notes

- The bus is a **singleton** (`event_bus`), shared across the entire application
- All subscribers receive all events (no filtering). Filtering is done client-side
- Events are fire-and-forget: if a subscriber's queue is full, the emitter still continues
- Keepalive pings every 30 seconds to prevent connection timeouts

## Business Event System (`app/events_system/`)

Extracts business events (deadlines, campaigns, cases) from channel messages via LLM, manages their lifecycle, and syncs to external backends.

### Architecture

```
Channel Poll → extractor.py → Event model → Activity Timeline
                                    ↓
                              syncer.py → backends/ → Dida365, Google Calendar
```

### Components

- **`extractor.py`**: Uses Claude Haiku to parse unstructured text (emails, WeChat messages) into structured events with `title`, `description`, `event_date`, `deadline`, `platform`
- **`syncer.py`**: Abstract backend pattern — `EventBackend` ABC with `sync_event()`, `update_event()`, `delete_event()`. Uses `@register_backend(name)` decorator + `EVENT_BACKEND_REGISTRY` dict
- **`backends/dida365.py`**: Dida365 (TickTick China) task sync via REST API
- **`backends/google_calendar.py`**: Google Calendar event sync via API

### Event Lifecycle

Statuses: `draft → open → in_progress → waiting → resolved → closed` (+ `dismissed`)

Each status change recorded as an `EventActivity` entry in the activity timeline.

### Activity Timeline

| Action | Actor | Description |
|--------|-------|-------------|
| `created` | user/channel/system | Event creation |
| `status_changed` | user | Status transitions |
| `note_added` | user/ai | Manual notes |
| `synced` | system | Sync to external backend |
