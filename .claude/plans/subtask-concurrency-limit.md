# Plan: Subtask Concurrency Limit & Storm Prevention

## Problem Statement

In auto mode, an agent can call `vibe_seller_create_task` via MCP in a tight loop, spawning subtasks that each start their own agent process + browser. With `MAX_AGENT_CONCURRENCY=9` in CI, the runner gets overwhelmed with orphaned Chromium processes, killing xdist workers and hanging pytest.

This was masked on `main` because plan mode restricts MCP access during the design phase, preventing the storm. Auto mode gives full MCP access immediately.

## Root Cause (Proven by CI Logs)

1. Parent task `87eff26d` (mode=auto) started at 13:55:59.
2. It called `vibe_seller_create_task(title=", ")` repeatedly from 13:58:52 to 13:59:55.
3. Each subtask used `profile=default` (no API key), spawned a Claude CLI + Chromium, then failed with auth error.
4. 16 Chromium processes were left orphaned when xdist workers died.
5. The circuit breaker (`VIBE_MAX_REPEAT_TOOL_CALLS=6`) triggered at 13:59:55 — too late.

## Design Fix: Per-Parent Subtask Concurrency Limit

### 1. Propagate Parent Task Context to MCP Server

**File:** `app/ai/claude_backend.py` (AgentSession._register_vibe_seller_mcp)

Add `--task-id {self.task_id}` to the MCP server args in `.mcp.json` so the MCP server knows which task invoked it.

### 2. Track Parent-Child Relationships

**File:** `app/mcp_server.py`

- Parse `--task-id` from CLI args.
- Store it in a global `PARENT_TASK_ID`.
- When handling `vibe_seller_create_task`, include `parent_task_id` in the `POST /api/tasks` body.

**File:** `app/schemas/task.py`

- Add `parent_task_id: str | None = None` to `TaskCreate`.

**File:** `app/routers/tasks.py` (create_task)

- Accept `parent_task_id` from the schema and set it on the new `Task` model.

### 3. Enforce Subtask Concurrency Limit

**File:** `app/env_options.py`

- Add `MAX_SUBTASK_CONCURRENCY = ('MAX_SUBTASK_CONCURRENCY', '2')`.
  This is independent of `MAX_AGENT_CONCURRENCY` and limits how many *active* subtasks a single parent may have.

**File:** `app/routers/tasks.py` (create_task)

Before creating the task, if `data.parent_task_id` is present:

```python
active_count = await db.scalar(
    select(func.count(Task.id)).where(
        Task.parent_task_id == data.parent_task_id,
        Task.status.in_([
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
            TaskStatus.DESIGNING,
            TaskStatus.RUNNING,
        ]),
    )
)
limit = Options.MAX_SUBTASK_CONCURRENCY.get_int()
if active_count >= limit:
    raise HTTPException(
        status_code=429,
        detail=f'Subtask concurrency limit reached ({limit} active subtasks for parent task).',
    )
```

The 429 response propagates through MCP back to the agent as a tool error, stopping the storm.

### 4. Revert Test Workarounds

**File:** `tests/e2e/test_all_store.py`

- Restore to its original `main` state (no agent-stop or polling cleanup).
- Once the subtask limit is enforced, `test_create_store_independent_task` will pass naturally because the agent can no longer spawn an unbounded number of subtasks.

## Test Coverage

### A. Unit/Workflow: `test_create_task_with_parent_task_id`

- Create a top-level task.
- POST `/api/tasks` with `parent_task_id` set to the top-level task.
- Assert response `parent_task_id` matches.

### B. Workflow: `test_subtask_concurrency_limit_blocks_new_subtasks`

- Create a parent task.
- Create `MAX_SUBTASK_CONCURRENCY` subtasks and manually set their status to `RUNNING`.
- POST a 3rd subtask → assert HTTP 429 and detail message.

### C. Workflow: `test_subtask_limit_allows_creation_after_terminal`

- Same setup as B, but mark one subtask `COMPLETED`.
- POST a new subtask → assert HTTP 200 (creation succeeds).

### D. Unit: `test_mcp_server_passes_parent_task_id`

- Patch `app.mcp_server.call_api`.
- Call `handle_tool_call('vibe_seller_create_task', {'title': 'x'})` with `PARENT_TASK_ID = 'abc'`.
- Assert `call_api` received `body['parent_task_id'] == 'abc'`.

## Files to Modify

- `app/ai/claude_backend.py`
- `app/mcp_server.py`
- `app/schemas/task.py`
- `app/routers/tasks.py`
- `app/env_options.py`
- `tests/e2e/test_all_store.py`
- `tests/workflow/test_wf_tasks.py` (add new tests)

## Out of Scope (Follow-up PRs)

- Replacing the 10s poll-loop DB check with an event-bus subscription.
- Reducing `VIBE_MAX_REPEAT_TOOL_CALLS` in CI (can be done in `.github/workflows/ci.yml` separately).
- Fast-failing tasks when `profile=default` has no valid API key.
