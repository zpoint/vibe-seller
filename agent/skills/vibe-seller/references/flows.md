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
| `planned` | Plan ready — review_plan approves (executes) or rejects with feedback (revises). An unreviewed planned task holds one of the server's agent slots until it is approved, rejected or stopped — never abandon one. Sending it a message (send_message) is itself a REJECTION: the message becomes the re-plan feedback — use review_plan. | `review_plan, stop_task, delete_task` |
| `running` | The agent is executing the task; it may ask a question. | `wait_task, stop_task` |
| `waiting` | Parked on child tasks or a wake condition; send_message wakes it. | `send_message, stop_task, delete_task` |
| `completed` | Done — the result is available. send_message continues it; retry_task starts over. | `send_message, retry_task, delete_task` |
| `failed` | Errored or stopped. retry_task starts over from scratch; send_message continues it. | `send_message, retry_task, delete_task` |

A plan-only task (one authoring a schedule's plan) reads
differently at the two statuses that matter:

- `planned` — Momentary: this task only writes its schedule's plan, which is being handed to the schedule for the owner's approval. The decision is on the schedule (get_attention, then review_schedule_plan). If it stays here, it holds one of the server's agent slots — never abandon one: review_plan hands the plan over, stop_task frees the slot.
- `completed` — Done — it wrote its schedule's plan; nothing was executed. The plan now waits for approval on the schedule (review_schedule_plan); asking for changes there starts a new planning run.

A task FIRED by its schedule (schedule_id set) runs the
frozen plan without a review:

- `planned` — Running its schedule's APPROVED plan — it starts on its own; wait_task follows it. The plan was approved on the schedule, so review_plan is not offered — rejecting or messaging the task would throw that approved plan away. Do not.

### Schedule plan statuses

Shown for an active schedule; a paused one fires in no plan
status.

| Plan status | Meaning | Fires? | Next tools |
| --- | --- | --- | --- |
| `none` | Auto schedule (no plan review) — every fire runs its prompt directly, on its cron unless paused. | yes | `run_schedule` |
| `planning` | Plan review is on: a plan-only task is writing the plan (wait_task on plan_task_id follows it). It cannot fire until a human approves a plan; if an earlier plan was approved, that one keeps firing meanwhile. | no (plan gate) | `wait_task, get_task` |
| `pending_approval` | The plan is written and WAITS FOR APPROVAL (proposed_plan); it cannot fire until approved. Show the plan to your user, then review_schedule_plan approves it or sends it back with their requested changes. The server never approves on its own and the wait never expires. | no (plan gate) | `review_schedule_plan` |
| `ready` | Plan approved by a human — the schedule WILL fire it on its cron (unless it is paused). review_schedule_plan with feedback revises it; the approved plan keeps firing meanwhile. | yes | `run_schedule` |
| `stale` | Legacy: the prompt changed after the plan was written. Re-plan (update_schedule replan=true); it cannot fire. | no (plan gate) | `update_schedule` |
| `failed` | The planning run failed or its agent died (plan_error says why); re-plan to try again (update_schedule replan=true). It cannot fire unless an earlier plan was approved, which keeps firing. | no (plan gate) | `update_schedule` |

Review rows assume no earlier approved plan; a schedule that
has one keeps firing it while a revision is planned, waits
for approval or fails. A paused schedule resumes
(`update_schedule`) once it can fire — resuming a review
schedule with no approved plan is refused.

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
  schedule_type="weekly", schedule_day=1, schedule_time="08:00")
                                 → AUTO: plan_status: none, fires: true,
                                    next_run: next Monday 08:00
```

Tell the user it runs every Monday at 08:00 and what it will do. If they
want to see one run now:

```
run_schedule(schedule_id=…)       → task_id — follow it with wait_task
```

The user wants to read the plan before anything runs:

```
create_schedule(…, review_plan=true)
                                 → plan_status: planning, fires: false,
                                    plan_task_id: …
wait_task(task_id=plan_task_id)   → completes on its own; the schedule is
                                    plan_status: pending_approval
get_attention()                   → schedule_plan_approvals[0] carries
                                    proposed_plan and plan_version
```

**Show the plan to the user** and get their decision — this is the
checkpoint; the server never approves on its own and the wait never
expires.

```
review_schedule_plan(schedule_id=…, approve=true, plan_version=…)
                                 → plan_status: ready, fires: true
# or, with their changes:
review_schedule_plan(schedule_id=…, approve=false,
                     feedback="Only campaigns over 50/day")
                                 → plan_status: planning (a new plan is
                                    written, then pending_approval again)
```

Then the prompt of the review schedule changes later:

```
update_schedule(schedule_id=…, description="…the new prompt…")
                                 → plan_status: planning — the approved
                                   plan is dropped; it will NOT fire
run_schedule(schedule_id=…)       → refused, with the reason
… wait_task, show, review_schedule_plan(approve=true) → ready again
```

A schedule with `fires: false` is never "scheduled". `run_schedule`
without an approved plan is refused with the route's reason — relay
that reason, do not paper over it. Every schedule result carries
`fires` — the answer to "will this run?" — and `next_actions`.

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

## Flow 8 — importing a skill the user has locally

"I have a codex skill that adds an Amazon listing — find it and add it
to vibe-seller." The skill lives on THIS machine; the server needs a
copy its task agents can load. Import it as an ARCHIVE — the bytes go
from your shell straight to the server and never pass through you.

```
manage_skill(action="begin_upload")        → {upload_id, put_url, curl}
<run the returned curl in your shell>      → tar/zip the skill dir, PUT it
manage_skill(action="import", upload_id=…, origin_url=…)
manage_skill(action="get", slug=…)         → confirm what landed
```

- The `put_url` is signed, single-use and short-lived — the signature
  is the authorization, so no API token belongs in the shell command.
  A `PUT` body is the default; a `multipart/form-data` POST with one
  `file` field works too.
- Pack the skill directory itself: `SKILL.md` at its root (one
  wrapping top-level folder is fine — the server strips one). Keep
  scripts executable; the archive carries the bits. Formats: `.tar.gz`,
  `.tar`, `.zip` (magic bytes decide).
- `slug` is optional: the server derives it from the SKILL.md
  frontmatter `name`, so do not invent one. Pass it only to place the
  skill under a different name.
- The install is ONE call — there is no create-then-update dance, and
  a refused call (bad path, symlink in the archive, oversized file, a
  built-in slug) leaves nothing behind. `import` REPLACES the skill
  wholesale (stale files are deleted); `update` MERGES (files it does
  not carry stay). If the user already has a skill under that slug,
  the import is refused until they confirm and you re-call with
  `overwrite: true` — never pass it unasked.
- Inline `files` / `files_bin` are for tiny text-only edits only. Do
  not push a real skill (or any binary) through tool arguments.
- Afterwards report the `files` list back to the user. If the server
  refused something, relay the reason and re-mint/re-try as needed —
  never import a silently incomplete skill.
