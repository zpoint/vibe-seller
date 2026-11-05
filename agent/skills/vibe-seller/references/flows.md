# Flows

Worked end-to-end examples. They use `acme` as a placeholder store and
invented task ids — the shapes matter, the values do not.

<!-- lifecycle:begin -->

## Status reference

Generated from `app/agent_mcp/lifecycle.py` — do not edit
between the `lifecycle` markers; run
`python -m app.agent_mcp.lifecycle` to regenerate.

What each status means, and which tools make sense in it. A
pending question overrides everything: `wait_task` returns
`reason=question`, then `get_pending_questions` →
`answer_question` — it is a flag on an active state, not a
status. A pending image proposal is the sibling flag:
the agent proposed an image and its generate call is parked
on a confirm card; `wait_task` returns
`reason=needs_operator` with `pending.kind=image_confirm`,
then `image_request` (get, then confirm or cancel).

The table shows the tools for a task with nothing running
under it. `delete_task` is also a property of the SUBTREE:
the route refuses it while any task in the tree is running
("stop them first"), so a parent with a live child is not
offered it.

### Task statuses

| Status | Meaning | Next tools |
| --- | --- | --- |
| `pending` | Created, not started yet; it launches on its own. | `wait_task, retry_task, delete_task` |
| `queued` | Waiting for a free run slot (the store's browser session is busy); it starts by itself. | `wait_task, delete_task` |
| `designing` | The agent is writing a plan (read-only); it may ask a question. | `wait_task, stop_task` |
| `planned` | Plan ready — review_plan approves (executes) or rejects with feedback (revises). An unreviewed planned task holds one of the server's agent slots until it is approved, rejected or stopped — never abandon one. | `review_plan, stop_task, delete_task` |
| `running` | The agent is executing the task; it may ask a question. | `wait_task, stop_task` |
| `waiting` | Parked on child tasks or a wake condition; send_message wakes it. | `send_message, stop_task, delete_task` |
| `completed` | Done — the result is available. send_message continues it; retry_task starts over. | `send_message, retry_task, delete_task` |
| `failed` | Errored or stopped. retry_task starts over from scratch; send_message continues it. | `send_message, retry_task, delete_task` |

A plan-only task (one authoring a schedule's plan) reads
differently at the two statuses that matter:

- `planned` — Plan ready — this task only authors its schedule's plan: approving (review_plan) freezes the plan onto the schedule and nothing executes. An unreviewed planned task holds one of the server's agent slots — approve, reject or stop it, never abandon one.
- `completed` — Done — it authored its schedule's plan; nothing was executed. send_message revises it.

### Schedule plan statuses

Shown for an active schedule; a paused one fires in no plan
status.

| Plan status | Meaning | Fires? | Next tools |
| --- | --- | --- | --- |
| `none` | No plan lifecycle (system schedule) — fires its prompt as-is. | yes | `run_schedule` |
| `planning` | A plan-only task is authoring the plan; the schedule cannot fire until the plan is frozen, and the plan is then accepted automatically. Pause the schedule now (update_schedule paused=true) if your user has not seen the plan. | no (plan gate) | `update_schedule, wait_task, get_task` |
| `ready` | Plan written and accepted automatically — the schedule WILL fire on its cron (unless it is paused). Show the plan to your user; pause or replan it if they object. | yes | `run_schedule` |
| `stale` | The prompt changed after the plan was frozen; the stored plan no longer matches. Re-plan; it cannot fire. | no (plan gate) | `update_schedule` |
| `failed` | The planning task failed; re-plan to try again. It cannot fire. | no (plan gate) | `update_schedule` |

A paused schedule resumes (`update_schedule`) once the plan
gate is open — resuming mid-`planning` is refused.

<!-- lifecycle:end -->

## Flow 1 — a read-only report (auto mode)

The user asks for last week's orders.

```
list_stores                  → pick the store id the user means
list_tasks(store_id=…)       → is something like this already running?
create_task(
  store_id="<id>",
  description="Pull the order report for the last 7 days for this store
               and summarise which ASINs sold most.",
  plan_mode=false)           → reads only; nothing to approve
wait_task(task_id)           → blocks until it moves
get_task(task_id)            → read status + result
```

Then report what `result` contains. If the task asked a question instead,
switch to Flow 3.

## Flow 2 — a store-changing task (plan mode)

The user asks to pause a campaign. This spends money and changes state, so
it gets a plan first.

```
create_task(
  store_id="<id>",
  description="Pause the under-performing Sponsored Products campaign and
               report what was paused and why.",
  plan_mode=true)            → the user approves before anything changes
wait_task(task_id)           → returns when it reaches `planned`
get_task(task_id)            → read the plan text
```

Now decide:

```
review_plan(task_id, approve=true)
```

or, when the plan is wrong:

```
review_plan(task_id, approve=false,
            feedback="The plan pauses three campaigns; only pause the one
                      whose ACOS is above 40, and leave the others running.")
```

Then keep following:

```
wait_task(task_id)
get_task(task_id)
```

**Never leave a task sitting at `planned`.** A task waiting for its plan
holds one of the server's agent slots until it is approved, rejected, or
stopped — it does not give the slot back on its own, and two such tasks
can wedge the server (later tasks sit `pending`/`queued` and never
start). If you will not approve a plan, `stop_task` it.

## Flow 3 — the task asks a question

```
wait_task(task_id)           → returns with a pending question
get_pending_questions(task_id)
    → {pending: true,
       request_id: "…",
       questions: [{question: "Which marketplace should I cover: US only,
                                 or US and CA?", header: "Marketplace",
                    options: [...]}]}
```

Answer with the **full question text** as the key:

```
answer_question(
  task_id="…",
  answers={"Which marketplace should I cover: US only, or US and CA?": "US only"})
# request_id is optional — the server uses the question it is holding.
```

If the user has not told you which marketplace to pick, ask them first.
Guessing an answer to a question you could have asked is the same failure
as inventing a result.

## Flow 4 — the task failed

```
get_task(task_id)            → read `error` and the last steps
```

Decide from the error, not from optimism:

- **Transient** (timeout, page glitch, a rate limit):
  `send_message(task_id, message="carry on from where you stopped")`.
  The task resumes with its plan, transcript and downloads intact.
- **The task was wrong** (wrong store, wrong scope, a bad instruction):
  `send_message(task_id, message=…)` to correct it. A corrected task
  keeps everything the first run learned.
- **Start over only when continuing would compound the mistake** — the
  approach or the inputs were wrong, or the user asked for a clean run:
  `retry_task(task_id)`. A retry wipes the plan, the transcript and the
  workspace, so anything the failed run downloaded is gone; ask first if
  that matters.

Then report the outcome honestly, including the failure. A retried task
that finally succeeds does not erase the first failure from the story.

## Flow 5 — two stores

Store-facing work on two stores means **two tasks**, one after the other.
The store's browser session is shared and logged in; two tasks at once on
the same store interfere with each other. Finish the first (or stop it)
before starting the second.

## Flow 6 — a schedule

The user asks for the same report every Monday.

```
create_schedule(
  title="Monday ad report",
  description="Pull last week's campaign report and summarise the movers.",
  schedule_type="weekly",
  paused=true)                   → created PAUSED: plan_status: planning,
                                    fires: false, plan_task_id: …
```

**Pass `paused: true` unless your user already told you to go ahead.** A
schedule's plan is accepted by the server without anyone reading it, so
pausing is what keeps it a checkpoint. The plan is still being written,
so the schedule could not have fired anyway — pausing at creation
closes that window completely.

```
wait_task(task_id=plan_task_id)   → the plan-only task completes on its own;
                                    the plan it wrote is frozen onto the
                                    schedule: plan_status=ready — still
                                    paused, so fires is still false
```

The schedule result carries the plan text in `plan`. **Show it to the
user** and get their go-ahead — this is the checkpoint; nobody else will
make it for you.

```
update_schedule(schedule_id=…, paused=false)
                                 → fires: true — armed, and only now
```

Nothing ran in any of this — the schedule is now ARMED. Say that to the
user: every Monday's fire copies the frozen plan and runs straight
through, with no second planning stop.

If they object instead, do NOT resume: change the prompt
(`update_schedule` with a new `description` → the plan goes `stale` and
the schedule stops firing), re-plan it (`replan: true` → a new
plan_task_id; show that plan too), or `delete_schedule` it. A schedule
that is paused and stale is not armed — nothing runs.

Then the prompt changes later:

```
update_schedule(schedule_id=…, description="…the new prompt…")
                                 → plan_status: stale — it will NOT fire
run_schedule(schedule_id=…)       → refused, with the reason
update_schedule(schedule_id=…, replan=true)
                                 → a new plan_task_id; when it completes
                                   the schedule is ready again
```

A not-ready schedule is never "scheduled". `run_schedule` while stale is
refused with the route's reason — relay that reason, do not paper over
it. Every schedule result carries `fires` — the answer to "will this
run?" — and `next_actions`.

## Flow 7 — reporting the result

Relay, do not re-author:

- Report the task's status and its `result` field, in the user's language.
- If the result is long, summarise it — but every number in the summary
  must come from the result. Do not round a spend into a wrong total, do
  not convert currencies, do not add a conclusion the task did not draw.
- If the result is truncated in what the server returned, say so.
- Give the user the task id so they can open it in the web UI, where the
  full step log lives.

When the task failed, say it failed and give the error. When the task is
still running, say it is running and keep waiting — do not answer the
user's question with an estimate.
