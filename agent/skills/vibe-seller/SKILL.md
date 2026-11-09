---
name: vibe-seller
description: "Drive a Vibe Seller server from this agent over its hosted MCP endpoint: create tasks against a store, follow a running task to its result, answer the task agent's questions, review a plan before it runs, send follow-ups, stop or retry, and read the store's knowledge files. Use when the user wants browser-automation work done on an e-commerce store (Amazon, noon, ...) through a Vibe Seller server - e.g. 'audit my listings', 'check my ad campaigns', 'pull last week's orders', 'run a task on my store', 'review the plan the task made'. The server-side agent does the browsing; this skill only creates tasks and follows them."
---

# Vibe Seller (external agent)

You are talking to a **Vibe Seller** server: a self-hosted agent platform that
drives a real browser against a real store. You do **not** drive the browser
yourself. You create tasks, and you watch them run.

## The one rule that outranks everything else

**Never invent a result.** The task's result is produced by the agent running
inside the Vibe Seller server. Your job is to start it, keep it moving, and
relay exactly what the server reports.

- Do not summarise a report the server never returned.
- Do not "helpfully" fill in figures, ASINs, campaign ids, rankings or
  conclusions from your own knowledge of e-commerce.
- If `get_task` shows `failed`, report the failure and its error. Do not
  rephrase it as a partial success.
- If a step's output is truncated, say it is truncated.

A plausible-sounding fabrication is worse than an honest gap: the user
cannot tell a wrong number from a right one in a report they did not watch
being produced.

## What you can and cannot do

| You can | You cannot |
| --- | --- |
| Create tasks (with attachments), list tasks, read a task's status/plan/result/output files | Open a browser, run scripts on a store page, take a screenshot |
| Answer the task agent's questions | See the task's live browser session |
| Approve or reject a plan before it runs | Speed up, batch, or parallelise one task's work |
| Send follow-up messages to a running task | Write into the store yourself (the task agent does that) |
| Read and edit the workspace knowledge the web UI edits; manage skills; drive the workspace assistant | Manage users or API keys |
| Manage the Settings pages: stores, browsers, Ziniao accounts, AI profiles, app settings, email, chat bots, TickTick | Read back a secret (they are write-only) |

The Settings-page tools (`manage_store`, `manage_ziniao_account`,
`manage_profile`, `app_settings`, `manage_email_account`,
`manage_chat_bot`, `manage_ticktick`, `browser`) act as the user who
minted the token, with that user's role — the same routes and permission
checks the web UI runs. `browser` starts a session for tasks to use; it
still does not let you drive a browser. The store-facing agent
inside Vibe Seller is the only thing that touches the store, and it runs
behind the server's own gates (ad-scope declarations, plan approval,
completeness review). Anything you could do directly in a browser would walk
around those gates.

## Choosing plan mode

`create_task` takes `plan_mode`. It decides whether the user sees the plan
before the task touches the store.

**Omit it and the owner's default applies**, exactly like the web UI —
and the result **echoes the resolved `plan_mode`**, so check it: it tells
you whether to expect a plan checkpoint or a straight run.

**Use plan mode (`plan_mode: true`) when the task:**

- changes anything in the store — bids, budgets, campaign state, prices,
  stock, listings, coupons, order handling;
- is ambiguous about scope, and getting it wrong is expensive;
- is one the user explicitly asked to see first ("draft it, then I'll
  approve");
- has **no store** at all. A no-store task is always plan mode; the server
  forces it, and asking for auto mode fails.

**Use auto mode (`plan_mode: false`) when the task only reads:**

- pull a report, export a file, read rankings or orders, summarise a page.
  It runs straight through — `pending` → `running` → `completed` — stopping
  only if the task agent asks a question.

Auto mode is not "unchecked": the store-facing agent still has to declare
what it is doing, and the server still enforces its review gates. Plan mode
only adds *your* approval step on top.

**Store-less tasks ALWAYS plan** — the server forces it, whatever you
asked for.

## The follow loop

A task is not instant. Follow it like this:

1. `create_task` → you get a `task_id` and the task starts on its own.
2. `wait_task` with the `task_id`. It blocks until the task needs you and
   returns a `reason` for why.
3. Look at the `reason` and act on it:

| `reason` | What you do |
| --- | --- |
| `question` | `get_pending_questions`, then `answer_question` |
| `needs_operator` | A plan is ready: `get_task` to read it, then `review_plan` |
| `waiting` | Parked on children or a condition: `send_message` to wake it, or wait again |
| `terminal` | `completed`: `get_task` to read the result, then relay it. `failed`: relay the error, then choose below. |
| `timeout` | Nothing happened yet — call `wait_task` again. It means "call again", never "it died". |

Every task result also carries `status_meaning` and `next_actions` — the
tools that make sense right now. Trust them: they come from the server's
own state machine, not from a guess.

`wait_task` is a long poll with a bounded wait (about 45 seconds by default,
capped near 120). Some clients time tool calls out — if a call fails on
timeout, call `wait_task` again rather than assuming the task died. Do not
spin in a tight loop: wait, look, act, wait again.

## Answering a question

The task agent can ask the user something mid-run — a choice between options,
a missing detail, a confirmation.

1. `get_pending_questions` → the question text and its options.
2. If the user's intent covers the answer, answer it. If it does not, ask
   the *user* (the person you are talking to) and then answer.
3. `answer_question` with an `answers` map keyed by the **full question
   text exactly as the server returned it** — not the short header, and
   not a paraphrase. The server maps the key onto the text the agent
   reads (the header or `q1` also work); with several questions pending,
   a key that matches none of them is an error naming the valid keys.

Answering a question is not the same as sending a message. Use
`answer_question` for the question card; use `send_message` for everything
else.

## Reviewing a plan

In plan mode the task stops at `planned` and produces a plan instead of
acting. Read it with `get_task` — the plan text is in the task's `plan`
field.

- **Approve** with `review_plan` (`approve: true`) when the plan does
  what the user asked. The task then executes.
- **Reject** with `review_plan` (`approve: false` plus `feedback`) when
  the plan is wrong, out of scope, or does something the user did not
  sanction. Say *what* is wrong — the feedback goes back to the task
  agent, which re-plans. "Bad plan" gets you a different bad plan.

**A plan is a human checkpoint.** Show the plan to *your* user and
approve only with their go-ahead — unless they already told you to
proceed. The server cannot enforce this (a token acts as its owner), so
the rule lives here: approving on your own authority is the one mistake
no gate will catch.

Do not approve a plan that acts on a store the user did not name, spends
money the user did not authorise, or touches a campaign scope the user did
not ask for. Approving is the user trusting the plan; your job is to make
sure the plan deserves it, and to escalate anything ambiguous to the user
rather than approving on their behalf.

**Never leave a task sitting at `planned`.** A task waiting for its plan
holds one of the server's agent slots until it is approved, rejected, or
stopped — it does not hand the slot back on its own, and two such tasks
can wedge the server: later tasks sit `pending`/`queued` and never start.
If you are done with a task you will not approve, `stop_task` it.

## Follow-ups, stop, retry, delete

- **`send_message` CONTINUES the same task.** It keeps the plan, the
  transcript and the progress. Use it for a mid-way or transient error,
  for a follow-up ("also include Canada", "now do the same for the other
  store"), and for continuing a `completed` task — on a finished task it
  resumes the work instead of starting over.
- **`retry_task` STARTS OVER.** It wipes the plan and the transcript and
  the on-disk workspace — anything the previous run downloaded is gone.
  Use it when the *approach or the inputs* were wrong, not when the run
  merely hit a bump.
- **`stop_task`** — halt a running task (it ends `failed`). Use it when
  the user changes their mind, or when a task is doing something you
  have stopped wanting.
- **`delete_task`** — remove a task outright, with its workspace. For
  cleanup of tasks nobody wants anymore; refuse-to-delete while a task
  is designing or running means `stop_task` first.

The rule of thumb: the run was fine but incomplete → `send_message`;
the run itself was wrong → `retry_task`.

`retry_task`, `send_message` and a plan rejection run under **your
default profile** when you pass none — the same thing the web UI sends.
Pass a `profile_id` to choose another.

## Schedules

A schedule runs a task on a timetable — and **every user-created
schedule is plan-mode**: it authors its plan once, through a plan-only
task, and the plan it writes is frozen onto the schedule.

**The one rule: a schedule whose `plan_status` is not `ready` NEVER
fires.** Not at its timetable time, not via `run_schedule`, not ever —
until its plan is ready again. When you create or edit a schedule, say
this to the user in plain words; "scheduled" is a lie until the status
is `ready`.

**A schedule's plan is a human checkpoint — and the server accepts it by
itself.** The plan-only task writes the plan and the server freezes it
onto the schedule with nobody asked (`plan_status: ready`,
`fires: true`). Nothing has run yet, but the next fire WILL run that
plan, with no second stop. So the review is YOUR job: show the plan to
your user before the schedule can run it. The plan text comes back with
every schedule result (`plan`).

To make that checkpoint real, create the schedule **paused**:

1. `create_schedule(…, paused=true)` → the schedule is created PAUSED,
   `plan_status: planning`, a `plan_task_id` — the plan-only task — and
   `fires: false`. Pass `paused: true` unless your user already told
   you to go ahead.
2. `wait_task` on the plan-only task → it **completes on its own**: the
   plan-only task writes the plan and finishes, and the plan is
   committed onto the schedule (`plan_status: ready`). It is still
   paused, so `fires` is still `false`. (The plan-only task is not a
   task you approve with `review_plan`.)
3. Show the plan (`plan` in the schedule result) to your user and get
   their go-ahead.
4. Happy → `update_schedule(schedule_id, paused=false)` → `fires: true`,
   armed. Object → `update_schedule` with a new `description` (the plan
   goes `stale`), or `replan: true` for a fresh plan, or
   `delete_schedule`.

Left unpaused (`paused` omitted — what the web UI does), the schedule
arms itself: `create_schedule` → `wait_task` → the plan is committed
(`plan_status: ready`, `fires: true`). Nothing is executed — the
schedule is armed, not run — but it is armed without anyone having read
the plan, so say what it will do when you report back. From then on,
every fire copies the frozen plan and **skips planning** — it runs
straight through.

Editing the schedule's prompt (`update_schedule` with a new
`description`) makes the plan `stale` — the schedule stops firing until
you `replan` (`update_schedule` with `replan: true`), which spawns a
fresh plan-only task; when it completes, the schedule is `ready` again.
`run_schedule` fires a schedule manually, now — and is refused, with the
reason, while the plan is not `ready`.

Every schedule result carries `plan_status`, `plan_status_meaning`,
`fires` and `next_actions` — `fires` is the answer to "will this run?",
and `next_actions` tells you what to do next. Trust them.

Pause and resume with `update_schedule` (`paused: true` / `false`);
delete with `delete_schedule`.

## Reading knowledge

`read_knowledge` lists and reads the files the store's agents keep under
`knowledge/` and `stores/`. It is read-only: use it to understand what a
store has already learned before you ask for another task, and to check
what a completed task produced.

It is not a way to write instructions for the store agent. Store agents
maintain their own knowledge as part of their work. To EDIT the
workspace the way the web UI's Workspace view does (with the same git
history), use `workspace_file` — see "Files, workspace and skills"
below.

## Attachments and task output files

`create_task` and `send_message` take `attachments` —
`{filename, mime_type, content_base64}`, up to 10 MB each (png, jpeg,
gif, webp, pdf). They reach the task agent the same way an upload in
the web UI does: create-time files are in its workspace before it
starts, chat files arrive with the message. Send the user's file
through rather than describing it.

`task_files` reads what a run PRODUCED: `list` the files, `read` one
(text comes back inline; a binary or a big file answers with a
download URL — fetch it with the same credentials — and small
binaries also ride along as base64), or `zip_url` for everything at
once. Report file contents from what you read, never from what you
expect them to say. When `get_task` is not enough to explain a run
(`get_task_log` pages the full step log and the conversation) — use
it before guessing at what happened.

## The image confirm card

Mid-run, a task agent may propose generating an image (a product
main image, a banner) — the generation is GATED on a human decision.
`wait_task` returns `reason=needs_operator` with
`pending.kind=image_confirm` while the task itself stays `running`:

1. `image_request(action=get)` — the card: the proposed `prompt`, the
   `model`, every model with its per-image price, and the reference
   images themselves (returned as images — look at them).
2. Show it to your user. They may edit the prompt, pick another model
   (use an id from the card), or add reference images.
3. `image_request(action=confirm, prompt?, model?, add_references?,
   reference_uploads?)` with their go-ahead (or their edits), or
   `action=cancel` to refuse.
4. When the task finishes, `task_files(action=read)` on the file in
   `generated_images/` SHOWS the image — look at it and show it to your
   user; `zip_url` downloads everything.

Confirming generates (and may bill) the image; cancelling is free and
safe — nothing was generated. A follow-up `send_message` instead of
answering the card interrupts it: the agent reads the message and
drops the proposal.

## Files, workspace and skills

`workspace_file` reads and edits the knowledge the web UI's Workspace
view edits (`knowledge/`, `stores/`, `.claude/skills/`): the tree,
file write/delete, per-file git `history` / `version` / `reset`, the
structured view, store-profile scaffolding and the knowledge sync.
Use it when the user asks you to fix or reorganize their store notes
— writes land in the workspace git history exactly like a UI edit,
and `history` shows who changed what and when.

`manage_skill` manages the server's agent skills (the instructions
its task agents load): `list`, `get`, `create`, `update` (a full
SKILL.md), `delete`, and the built-in `sync`. Changing a skill
changes how EVERY later task runs — treat it like editing production
configuration, and only when the user asked for it.

`workspace_assistant` is the server's own organizer agent (per-user):
`send` a message and the tool waits for its reply; a slow turn times
out — poll `action=status` for the `last_reply`. Use it for
knowledge-housekeeping jobs ("summarize the acme store notes") rather
than running a task.

## Server administration

The `manage_*`, `browser` and `app_settings` tools mirror the server's
Settings pages. Reach for them when the user asks you to *configure* the
server ("add a store", "switch the default model", "hook up a notifier"),
not as part of running a task — a task that needs a browser gets one on
its own.

- **Secrets are write-only.** Passwords, API keys, webhook URLs, signing
  secrets and the vision key are accepted on create/update and never
  handed back — reads show a mask or a `has_secret` flag. Do not promise
  the user you will "show them the key"; offer to re-set it instead.
  Echoing a mask you read is safe (the server keeps the stored secret);
  a mask when nothing is stored is an error, because you meant to send
  the real one.
- **Confirm what the UI confirms.** Deleting a store, disconnecting an
  integration, `force: true` on a browser start — the UI asks a human
  before these, so ask yours.
- **Real side effects are named in the tool descriptions** — sending an
  email, posting to a chat group, validating a profile (one tiny paid
  model call). Only do them on the user's behalf.
- **OAuth needs the human.** `manage_ticktick(authorize)` returns a URL
  the user must open in their own browser; you cannot finish it. Hand it
  over and poll `status`.

## Doing a whole job well

- **One task at a time.** A store's browser is a shared, logged-in
  session. Two of your tasks on the same store at once can interfere with
  each other.
- **Do not re-create a task that already exists.** Check `list_tasks`
  first; if a task with the same goal is running, follow it instead.
- **Tell the user the task id.** They can watch it in the web UI, which is
  where the rich view (live steps, plan, question cards) lives.
- **Give the task a description a colleague could act on.** The task agent
  reads it and nothing else. Say what to do, for which store, over which
  period, and what "done" looks like. Mechanics do not belong here.

## Reference

- `references/tools.md` — every tool, its arguments and what it returns.
- `references/flows.md` — worked end-to-end flows, including failure triage.

Argument names and result shapes can change; the tool descriptions you get
from the server are the authority. If a call fails on an argument, read the
tool schema the server advertised and adjust — do not guess a second
spelling.
