# Tool reference

Every tool the Vibe Seller MCP endpoint exposes, what it is for, and what it
returns.

**The server's own tool schema is the authority.** Argument names below
follow the REST API's field names; if the server advertises a different
name, use the server's. If a call is rejected for an argument, re-read the
schema the server gave you rather than trying a second spelling.

All tools act as the user who minted the token, with that user's role.
A token has no scopes: it can do what that user can do.

## Status vocabulary

Tasks move through `pending` → `queued` → `designing` → `planned` →
`running` → `waiting` → `completed` / `failed`. A schedule has its own
plan lifecycle: `none` → `planning` → `ready` → `stale` / `failed`.

**The server's table is the authority.** Every task and schedule result
carries `status_meaning` / `plan_status_meaning` and `next_actions` — the
tools that are valid right now. The full generated table is in
[flows.md](flows.md) § Status reference. Do not keep a second copy of it
here: read the meanings the server sends.

## list_stores

**Purpose.** The stores this token can act on. You need a store id before
you can create a store task.

**Arguments.** None.

**Returns.** `{"stores": [...]}` — one entry per store: `id`, `name`,
`browser_backend`, `platforms`, `countries`. Use the `id` verbatim in
`create_task`; do not guess it from the name.

## list_tasks

**Purpose.** What is already running, and what has already been done.
Call it before creating a task so you do not duplicate work.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `store_id` | string | Optional. Restrict to one store. |
| `status` | string | Optional. One of the statuses above. |
| `limit` | int | Optional. How many to return; newest first. |

**Returns.** Task summaries: `id`, `title`, `status`, `store_id`,
`created_at`, and a one-line preview of the request.

## get_task

**Purpose.** The full state of one task: status, plan, result, error, the
pending question, and the most recent steps. This is the call that gives
you the facts you report.

**Arguments.** `task_id` (string, required).

**Returns.**

- `status` — see above, plus `status_meaning` and `next_actions` (the
  tools that make sense in this state right now).
- `plan` — the plan text, when the task produced one.
- `result` — the final output, when the task completed. This is what you
  relay.
- `error` — why it failed, when it failed.
- `pending_question` — the question the agent is asking, when there is one.
- `steps` — the last few steps: what the agent did, in order. Useful for
  progress narration, not for building results.

## wait_task

**Purpose.** Block until the task changes state, asks a question, offers a
plan, or finishes. This is how you avoid polling `get_task` in a tight
loop.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `timeout_seconds` | int | Optional. Roughly 45 by default, capped near 120. |

**Returns.** `{"task_id", "status", "changed", "timed_out", "reason",
"client_disconnected", "pending_question"}`. `reason` says **why** it
returned:

| reason | Meaning |
| --- | --- |
| question | The agent asked something — call `get_pending_questions`, then `answer_question`. |
| needs_operator | A plan is ready — read it with `get_task`, then `review_plan`. |
| waiting | The task is parked on its children or a wait condition. `send_message` wakes it; otherwise wait again. |
| terminal | The task finished (`completed` / `failed`). Read the result with `get_task`. |
| timeout | Nothing happened in time. This just means "call again", not "it died". |

Loop on `wait_task`: stop the loop on `question`, `needs_operator` or
`terminal`; on `waiting` either wake the task (`send_message`) or wait
again; on `timeout` always call it again.

It wakes on **news, not motion**: a question, a plan, a parked task or a
terminal state. The transient statuses (`pending` → `queued` → `running`)
do not wake it, because returning on those would turn a long-poll into a
busy loop.

**It returns immediately when the task is parked.** A task that is still
`planned` a moment after an approval comes back at once, so read
`needs_operator` as "ask again", not as the answer.

`client_disconnected` is `true` when the caller's connection dropped
while the wait was running — the task is unaffected, it kept going.

**Timeouts.** Some clients cap how long a tool call may run (Codex has a
`tool_timeout_sec`). If the call fails on timeout, the task is still
running; wait again.

## create_task

**Purpose.** Start a new task on a store (or, with no store, a general
one). The task starts on its own — you do not need to "run" it.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `description` | string | Required. What to do, for which store, over which period, and what "done" looks like. This is the task agent's only instruction. |
| `store_id` | string | Optional. Omit for a general task with no store. |
| `title` | string | Optional. A short label for the task list. |
| `plan_mode` | bool | Optional. `false` runs straight through; `true` plans first and waits for `review_plan`. **Omit it and the owner's default applies, exactly like the web UI** — and the result echoes the resolved `plan_mode`, so you know which path you are on. See "Choosing plan mode" in SKILL.md. |
| `profile_id` | string | Optional. Which AI profile runs the task. Omit to use the user's default profile. |
| `platform` | string | Optional. Marketplace platform code, for the concurrency rules that keep one browser session per platform. |
| `country` | string | Optional. Marketplace country code, likewise. |
| `attachments` | array | Optional. Files for the agent to work on: `[{filename, mime_type, content_base64}]`, each up to 10 MB, types png / jpeg / gif / webp / pdf. They land in the task workspace BEFORE the agent starts — the same upload-then-start chain the web UI's create dialog runs. |

**Defaults (identical to the web UI).**

- No `store_id` → the task is a no-store task, and plan mode is **forced
  on**.
- No `plan_mode` on a store task → the user's own default plan mode.
- No `profile_id` → the user's default AI profile. Never pass a literal
  `"default"` hoping it means "whatever is configured" — it is a real
  profile id pointing at plain Claude.

**Returns.** The created task: `task_id`, `status`, `plan_mode`,
`store_id`, `ai_profile_id`, and the `attachments` it accepted. The id
you need for every later call is `task_id`.

## get_pending_questions

**Purpose.** What the task agent is asking, if anything.

**Arguments.** `task_id` (string, required).

**Returns.** `pending` (bool) and, when pending, a `request_id` and the
question(s): the **full question text**, its short header, and the
options.

## answer_question

**Purpose.** Answer the pending question so the task continues.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `answers` | object | Required, non-empty. Map from a question key to the answer. |
| `request_id` | string | Optional. From `get_pending_questions`. Omit to answer the question the task is holding right now. |

**The key should be the full question text exactly as the server returned
it.** The server maps the key onto the text the agent reads: the full
text always matches, and the short header, `q1` or `1` also work. With
several questions pending, a key that matches none of them is an error
naming the valid keys — never a guess. With exactly one question any
key is unambiguous and is accepted.

Answers for options are the option's label; free-text questions take the
text you would have typed.

**Returns.** `{"ok", "request_id", "answers", "resumed"}` — the `answers`
are the mapped ones, so you can see what the agent actually received.

## review_plan

**Purpose.** Approve or reject the plan a plan-mode task produced. A plan
task does nothing until you do this.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `approve` | bool | Required. `true` executes the plan; `false` sends it back. |
| `feedback` | string | Required when rejecting. What is wrong, so the agent can re-plan. |
| `profile_id` | string | Optional. Which AI profile runs the revision. Omitted → your default profile, as the web UI does. |

**Returns.** `ok` and the task's new status (`running` after an approval).
The call returns only once the task has actually left `planned`, so the
next `wait_task` will not report a plan that was already approved.

**A plan is a human checkpoint.** Show the plan to your user and approve
it only with their go-ahead — unless they already told you to proceed
without asking.

Rejecting with an empty feedback gets you a re-plan with the same mistake.
Say what should be different.

## send_message

**Purpose.** A follow-up on an existing task. The normal way to continue
work, change direction, or ask for more.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `message` | string | Required (unless `attachments` carries the whole message). Plain instruction, as if to a colleague. |
| `profile_id` | string | Optional. Which AI profile continues the work. Omitted → your default profile, as the web UI does. |
| `attachments` | array | Optional. Files that reach the agent WITH this message (same `{filename, mime_type, content_base64}` shape and limits as `create_task`) — the chat-bar upload flow: staged until the message is delivered. |

**Returns.** `ok`.

`send_message` **continues** the same task: the plan, the transcript and
the progress are kept. Use it for a mid-way or transient error, and for
follow-ups. `retry_task` **starts over** — it wipes the plan and the
transcript — so use it when the approach or the inputs were wrong.

A follow-up on a task that is waiting for an answer **redirects** it
rather than queueing behind it. If the task is parked on a question card,
answer the card (`answer_question`), not the composer.

**On a task parked at `planned` a message IS a plan rejection.** The
route treats any incoming message as the reject verdict — the text
becomes the re-plan feedback. Use `review_plan` there, never a
clarification. (On a task FIRED by its schedule, `planned` means the
frozen plan is auto-executing — never review or message it; follow it
with `wait_task`.)

## stop_task

**Purpose.** Stop the agent working on a task. The task ends as
`failed`.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |

**Returns.** The outcome of the stop.

Stopping does not delete the task: its steps and result stay readable, so
you can report what it got through.

## retry_task

**Purpose.** Re-run a task from the beginning.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `profile_id` | string | Optional. Which AI profile to use. Omitted → **your default profile**, as the web UI does; pass one to choose another. |

**A retry is a fresh start.** It clears the plan, the step log, the
messages, and the task's on-disk workspace and transcripts — anything the
previous run downloaded is gone. It is not a "resume where it broke".

Only `failed` (and a couple of other) tasks can be retried; the server
refuses otherwise.

**Profile semantics.** A retry runs under **your default profile**
unless you pass a different one — the same thing the web UI sends when
you click Retry.

## delete_task

**Purpose.** Delete a task and everything under it — its steps, its
messages, the files it produced and its on-disk workspace. This is the
same delete the web UI performs, so the same rules apply: a task that
is still designing or running must be stopped first (`stop_task`), and
the route refuses with 409 while it is active. The check covers the
WHOLE subtree — a task whose child is still running is refused too,
even when the task itself is finished. `next_actions` follows that
rule, so `delete_task` is not offered while anything under the task is
running.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |

**Returns.** `ok` (bool).

Deleting is not reversible. Stop the task first if you only want it to
end — `stop_task` keeps the steps and the result readable.

## read_knowledge

**Purpose.** Read the store's own notes and knowledge files — what the
store's agents have learned, and what a previous task left behind.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `path` | string | Optional. A path under `knowledge/` or `stores/`. Omit to list. |
| `store_id` | string | Optional. Scope the listing to one store. |
| `section` | string | Optional. Restrict the listing to `knowledge` or `stores`. |
| `limit` | int | Optional. How many files to list (default 50, max 200). |

**Returns.** A directory listing, or the contents of one file.

Read-only. This is not a channel for instructing the store's agent — its
knowledge is maintained by its own work.

## list_schedules

**Purpose.** What is scheduled on this server, and whether it can
actually fire.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `store_id` | string | Optional. Only schedules for this store. |

**Returns.** A list of schedules, each with `title`, the recurrence
fields (`schedule_type`, `schedule_time`, `schedule_day`,
`interval_value`, `timezone`), `next_run`, `paused`, `plan_mode`,
`plan_status`, `plan_task_id` (the plan-only task authoring the plan
while `plan_status=planning`), `last_run_status`,
`child_task_count`, plus the lifecycle block: `plan_status_meaning`,
`fires` and `next_actions`.

**Every entry also carries the plan itself** — `plan` (the frozen plan
text, trimmed only if it is longer than one field's budget),
`plan_truncated`, and `plan_full_text_where` (where to read it whole
when it was trimmed). The plan is what the schedule will run, so it is
what you show your user before the schedule can run it.

**`fires` is the answer to "will this run?"** It is `false` when the
plan is not ready or the schedule is paused. Never report a schedule
as "scheduled" when `fires` is `false` — say what is missing instead.

## create_schedule

**Purpose.** Create a scheduled task: a prompt that runs on a
recurrence.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `title` | string | Required. Short label. |
| `description` | string | Optional. The prompt every fire runs. |
| `schedule_type` | string | Required. `minutes`, `hours`, `days`, `weekly` or `monthly`. |
| `schedule_time` | string | Optional. `HH:MM` fire time (default `09:00`). Ignored for `minutes`/`hours`. |
| `schedule_day` | int | Optional. Day of week (1=Mon…7=Sun) for `weekly`, day of month for `monthly`. |
| `interval_value` | int | Optional. Every N units (default 1). |
| `timezone` | string | Optional. IANA zone for `schedule_time`. Omit for the server's default. |
| `store_id` | string | Optional. Bind to one store. Omit for an all-stores schedule. |
| `phase_mode` | string | Optional. All-stores only: `single` (one no-store task per fire) or `fanout` (one task per store). |
| `fanout_serial` | bool | Optional. Fanout only: run the batch's stores one at a time. |
| `finalize_description` | string | Optional. Fanout only: a reduce task run after every store of a batch finishes. |
| `ai_profile_id` | string | Optional. Pin an AI profile. Omit to inherit the owner's current default at fire time. |
| `paused` | bool | Optional. Omit (or `true`) to create the schedule PAUSED — it cannot fire until you resume it with `update_schedule(paused: false)`. Pass `false` only when your user already told you to go ahead (the web UI creates active schedules; paused is this tool's default). |

**Returns.** The schedule, plus `plan_status`, `plan_task_id`, `plan`
and the lifecycle block.

**Every schedule plans first.** A plan-only task (`plan_task_id`)
authors the plan; follow it with `wait_task`. When it finishes, the
plan is frozen onto the schedule (`plan_status=ready`) and the
planning task executes **nothing**. Until then the schedule does not
fire.

**The default is PAUSED — resume only with your user's go-ahead.**
The schedule is created paused, so it cannot fire until you resume
it — which is what keeps its plan a checkpoint your user can actually
see: the plan is accepted by the server without anyone reading it, and
an armed schedule fires on its own once the plan is ready. (The web UI
creates active schedules; the divergence is deliberate, like
`manage_profile` leaving `set_default` off.) If the pause step itself
fails, the tool deletes the schedule it just created — nothing
half-armed is left behind; retry the create.

**The plan is accepted by the server, not by a human.** `plan_status:
ready` means the server took the plan the planner wrote — nobody
approved it. Showing it to your user is your job.

## update_schedule

**Purpose.** Edit a schedule, pause or resume it, or re-plan it. One
call can do several: the edit applies first, then the re-plan, then
the pause/resume.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `schedule_id` | string | Required. |
| `title` | string | Optional. |
| `description` | string | Optional. The prompt every fire runs. Changing it makes the stored plan **stale**. |
| `schedule_type` | string | Optional. `minutes`, `hours`, `days`, `weekly` or `monthly`. |
| `schedule_time` | string | Optional. `HH:MM`. |
| `schedule_day` | int | Optional. Day of week or day of month. |
| `interval_value` | int | Optional. Every N units. |
| `timezone` | string | Optional. IANA zone. |
| `ai_profile_id` | string | Optional. Pin an AI profile. |
| `fanout_serial` | bool | Optional. Fanout only. |
| `finalize_description` | string | Optional. Fanout only. |
| `plan_version` | int | Optional. Optimistic lock: the `plan_version` you last saw. The server refuses (412) if it moved. |
| `paused` | bool | Optional. `true` pauses; `false` resumes. |
| `replan` | bool | Optional. `true` spawns a fresh plan-only task to author a new plan. |

**Returns.** The schedule after the change, with `plan_status`,
`plan_task_id` (the new planning task, when one was spawned), `plan`
(the frozen plan text), `updated_fields` and the lifecycle block.

**Editing the prompt invalidates the plan**: `plan_status` becomes
`stale` and the schedule will not fire again until it is re-planned
(`replan: true`). Resuming a paused schedule needs a ready plan — a
`stale` or `failed` one is refused with the reason.

## delete_schedule

**Purpose.** Delete a schedule. Its future fires stop; the fired
tasks are kept, and an in-flight planning task is stopped (ended as
`failed`), never left parked at `planned` holding an agent slot.

**Arguments.** `schedule_id` (string, required).

**Returns.** `ok` and the schedule id.

## run_schedule

**Purpose.** Fire a schedule now, outside its cron — the same task(s)
its next scheduled fire would create.

**Arguments.** `schedule_id` (string, required).

**Returns.** `ok`, the created `task_id` (a store-bound schedule
answers with one; an all-stores schedule fans out in the background
and answers with `mode: fanout` instead — find the tasks with
`list_tasks`), and the lifecycle block.

**The plan must be ready.** A schedule whose plan is not `ready` never
fires; the refusal says why, and it is not an error you can retry
your way out of — re-plan it first.

**It fires a PAUSED schedule too.** The web UI's Run-now button is not
disabled by the pause, and neither is this tool — get your user's
go-ahead before firing a paused one; resuming
(`update_schedule(paused: false)`) is the move when they have already
approved the plan.

**The fired task does not re-plan.** It runs the frozen plan, so the
work happens without another review stop.

## get_task_log

**Purpose.** The debug log for one task: the FULL step log, paginated,
plus the conversation. Reach for it when `get_task` is not enough — a
run that failed mid-way, a result you cannot explain, "what did it
actually do".

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `offset` | int | Optional. Skip this many steps (default 0). |
| `limit` | int | Optional. How many steps (default 100, max 500). |
| `messages_limit` | int | Optional. How many RECENT conversation messages (default 50, max 200). |

**Returns.** `steps` (each with `step_index`, `name`, `action_type`,
`status`, trimmed `description` / `result` / `error`, timestamps),
`steps_total` and `steps_truncated` for paging; `messages` (role +
trimmed content), `messages_total`, `messages_truncated`.

## task_files

**Purpose.** The files a task produced — reports, exports, generated
images. What the task's file browser shows in the web UI.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `action` | string | Required. `list`, `read` or `zip_url`. |
| `path` | string | Required for `read`. File inside the task workspace, relative. |

**Returns.** For `list`: the files (`name`, `size`, `type`,
`modified_at`). For `read`: a text file up to 256 KB comes back inline
(`encoding: "text"`); anything else answers with an absolute `url`
(plus a base64 `blob` for binaries up to 5 MB) — download it from
there with the same credentials. For `zip_url`: a `url` that serves
everything as one ZIP.

Paths are relative to the task workspace; `..`, absolute paths and
backslashes are refused.

## image_request

**Purpose.** See and resolve an image-generation confirm card. While
one is pending, the task agent's generate call is PARKED waiting for a
human: it proposed a prompt (and a model) and nothing happens until
the card is resolved.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Required. |
| `action` | string | Required. `get`, `confirm` or `cancel`. |
| `request_id` | string | Optional. From `get`. Omit to use the card the task holds now. |
| `prompt` | string | Optional, `confirm` only. An edited prompt — your user may change what was proposed. |
| `model` | string | Optional, `confirm` only. An edited model id (one of the card's models). |

**Returns.** For `get`: `pending` plus the card (`request_id`,
`prompt`, `model`, the model `models` catalog, `reference_images`,
`output_name`, `kind`). For `confirm`/`cancel`: `ok` — the decision
was delivered; follow the task with `wait_task`.

**A card is a checkpoint**: show the proposal to your user and confirm
with their go-ahead (or theirs edits), unless they already told you to
proceed. Cancelling is safe — nothing was generated yet. Confirming
may cost money (the image is generated after the confirm).

## workspace_file

**Purpose.** Read and edit the workspace knowledge the web UI's
Workspace view edits: shared notes under `knowledge/`, per-store notes
under `stores/`, skills under `.claude/skills/`.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `tree`, `read`, `write`, `delete`, `history`, `version`, `reset`, `structured`, `store_profile`, `knowledge_sync`, `knowledge_status`, `knowledge_meta`. |
| `path` | string | Required for file actions. Workspace-relative, under the roots above. |
| `content` | string | Required for `write`. The full new file content (text). |
| `commit` | string | Required for `version` / `reset`. A sha from `history`. |
| `max_count` | int | Optional for `history`. How many commits (max 200). |
| `prefix` | string | Optional for `tree`. Only paths under this prefix. |
| `limit` | int | Optional for `tree`. How many entries (max 500). |
| `slug` | string | Required for `store_profile`. The store slug. |
| `name` | string | Required for `store_profile`. The store name. |
| `platform` | string | Optional for `store_profile`. |
| `country` | string | Optional for `store_profile`. |
| `backend` | string | Optional for `store_profile`. `chrome` (default) or `ziniao`. |

**Returns.** Per action: a listing (`tree`, with `total` /
`truncated`), file content (`read`, trimmed past 8000 characters —
binary files answer with a `url` + `blob` like `task_files`),
`{path, status}` (`write`/`delete`/`reset`), the git log (`history`:
`sha`, `message`, `date`, `author`), a file at a commit (`version`),
the grouped view (`structured`), a scaffolded store folder
(`store_profile`), and the knowledge sync status / metadata / run.

Every write goes through the same route and the same workspace git
history as a UI write, and is **attributed**: your commits are
authored by the token's owner with a `Via: api_token` trailer (a
browser session's writes say `Via: session`; the task agents' own
knowledge commits keep the server's agent identity). `history` shows
the author, so report WHO changed a file from the log, not from
guesswork. `read_knowledge` stays the read-only tool over
the same knowledge; this one is the write-capable superset.

## manage_skill

**Purpose.** Manage the server's agent skills — the instructions its
task agents load. What the Workspace → Skills view manages. TO IMPORT
A LOCAL SKILL (a codex or Claude skill directory on this machine) use
the presigned archive upload: `begin_upload` → `curl` the archive from
your own shell → `import`. The skill's bytes travel as a plain HTTP
upload straight to the server — they never pass through you, so a
binary-carrying skill costs no tokens and cannot be corrupted in
transit.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `list`, `get`, `create`, `update`, `begin_upload`, `import`, `delete`, `sync`, `sync_meta`. |
| `upload_id` | string | `import` only: the id from `begin_upload` — the staged archive (`.tar.gz` / `.tar` / `.zip` of the skill directory) is extracted and installed. THE way to import a real skill; mutually exclusive with `files` / `files_bin`. |
| `slug` | string | Required for `get` / `update` / `delete`. Optional for `import` — derived from the SKILL.md frontmatter `name` when omitted. |
| `name` | string | Required for `create`. Letters, digits, hyphens; must not start with `_`. |
| `description` | string | Optional for `create`. One line. |
| `origin_url` | string | Optional for `create` / `import`. Where the skill came from (a URL or a local path). |
| `skill_md` | string | `update` / inline `import` only: the full SKILL.md (frontmatter included) — stored as written, whatever its frontmatter holds. |
| `files` | object | `update` / inline `import` only: relative path → TEXT content (not SKILL.md). For tiny text-only skills and edits. |
| `files_bin` | object | `update` only: relative path → **base64** content. Small inline binaries only — a real import goes through `begin_upload`, because base64 tool arguments push every byte through your model. |
| `executables` | array | `update` / inline `import` only: which of the files above land with the executable bit. An archive carries its own bits (tar mode / zip attributes). |
| `overwrite` | boolean | `import` only. Required to replace a user skill that already exists under the same slug — the call is refused without it, so a hand-made skill is never destroyed silently. |

**Importing a local skill (the archive flow).**

1. `action=begin_upload` → `{upload_id, put_url, put_path,
   expires_at, max_bytes, formats, curl}`. The `put_url` is signed,
   single-use and short-lived; the signature IS the authorization, so
   no API token belongs in any shell command. If the `put_url` origin
   differs from the MCP URL you are configured with (a reverse proxy
   in front of the server), `PUT` to **your own MCP origin +
   `put_path`** — the path and its signature are the contract, the
   origin is just a hint.
2. Run the returned `curl` in YOUR shell — `tar czf` (or `zip`) of the
   skill directory, `PUT` as the request body. A
   `multipart/form-data` POST with one `file` field works too (it
   must declare `Content-Length`; a chunked body is refused with 411);
   the raw `PUT` is the documented default. Format is decided by magic
   bytes (`.tar.gz`, `.tar`, `.zip`), and the size cap is enforced
   while streaming.
3. `action=import` with the `upload_id`. The server extracts the
   archive strictly — regular files only (no symlinks, hardlinks,
   devices or FIFOs), safe relative names, no duplicates, one common
   top-level directory stripped, `SKILL.md` required at the resulting
   root (exactly that name) — and installs it through the one write
   path every save uses. Everything is stored byte-identical. macOS
   packaging junk (`._*`, `__MACOSX/`, `.DS_Store`) is skipped and
   listed under `ignored` in the result — the server never silently
   installs a different tree than the one you packed.

**Writes.** Every path and size is checked before the first byte
lands, so a refused call leaves nothing behind and there is no
half-imported state. `import` **replaces** the skill directory
wholesale (stale scripts from a previous version are deleted — the
tree is staged and swapped in, so a failed import leaves the old
skill untouched), while `update` **merges** (it writes the files
given and leaves the rest of the skill alone). A replace over an
EXISTING user skill needs `overwrite` (a re-import after an edit
passes it): without it the call answers "a skill named X already
exists (source=…)" and changes nothing — confirm with your user
first. Paths are relative to the skill directory; `..`, absolute
paths, `SKILL.md` itself and symlink escapes are refused.

Inline `files` / `files_bin` remain for TINY text-only skills and
edits. Do not carry large or binary content through tool arguments —
that is exactly what the archive upload exists for.

**Returns.** Per action: the skills with `slug`, `name`,
`description`, `source` (`builtin` skills are read-only) and
`updatable` (`list`); one skill with its full `skill_md` and `files`
(`get`); the created path (`create`); the upsert result (`update` /
`import` — `{slug, path, action}`); the presigned upload
(`begin_upload` — `{upload_id, put_url, put_path, expires_at,
max_bytes, formats, curl}`); `{slug, status}` (`delete`); the sync result and its
metadata (`sync` / `sync_meta`).

## workspace_assistant

**Purpose.** The workspace assistant: a per-user chat agent that
organizes the workspace for you (knowledge files, store notes). Ask it
to reorganize notes, summarize a store's knowledge, or draft a store
profile — it edits workspace files itself and never runs store tasks.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `send`, `status` or `stop`. |
| `message` | string | Required for `send`. What to ask or tell it. |
| `profile_id` | string | Optional for `send`. Omit for the server default. |
| `wait_seconds` | int | Optional for `send`. How long to wait for the reply (default 45, max 120). |

**Returns.** For `send`: `ok`, `done` / `timed_out`, the `reply` (the
turn's result text) and the `messages` that arrived — a slow turn
times out and says to poll `status`. For `status`: `running` and the
assistant's `last_reply` (its answers are streamed, not stored — the
last one is kept in memory). For `stop`: `ok` (false when nothing was
running).

# Server administration tools

These wrap the web UI's Settings pages — the same routes, the same
permission checks (an admin-only route answers 403 unless the token's
owner is an admin). One tool per resource, with an `action` argument
for the operation.

**Secrets are write-only.** Passwords, API keys, webhook URLs,
signing secrets and the vision key are accepted on create/update and
never returned: reads give a mask (`****` + last 4, or plain `****`
for URLs) or a `has_secret`/`*_set` flag. Never ask the tool to show
one, and never store one from an earlier write.

**Echoing a mask keeps the stored secret.** If you send back a value
exactly as you read it (a mask), the server substitutes the secret it
has stored — so the natural read → edit one field → update flow is
safe. A mask when NOTHING is stored is an error: send the real secret
or omit the field.

**They act as the token's owner.** Anything the web UI would ask a
confirmation dialog for (delete a store, disconnect an integration,
force-restart Ziniao) deserves the same pause: check with your user
first.

## manage_store

**Purpose.** Manage stores — the Settings → Stores page.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `list`, `get`, `create`, `update`, `delete` or `bookmarks`. |
| `store_id` | string | The store, for `get`/`update`/`delete`/`bookmarks`. |
| `name` | string | Store name; required for `create`. |
| `browser_backend` | string | `create` only: `chrome` (default) or `ziniao`. |
| `browser_config` | object | `create`/`update`: e.g. `{"proxy_server": …, "proxy_bypass": …}` for chrome. |
| `ziniao_account_id` | string | `create`, ziniao stores: the account id. |
| `browser_oauth` | string | `create`, ziniao stores: the profile oauth from `manage_ziniao_account(browsers)`. |
| `platforms` | list | `create`/`update`: marketplaces, default `['amazon']`. |
| `countries` | list | `create`/`update`: countries, default `['US']`. |
| `platform_countries` | object | `create`/`update`: per-marketplace countries, e.g. `{"amazon": ["US","AE"]}`. |
| `delete_files` | bool | `delete`: also remove the store workspace and browser profile (default false). |

**Returns.** The store rows the web UI sees (create → `created`,
update → `updated`, get → `store`, list → `stores`, bookmarks →
`bookmarks`; delete → `ok`).

Deleting a store deletes its tasks and schedules; the route refuses
while a task on it is running. `update` cannot change the browser
backend (immutable, as in the UI).

## browser

**Purpose.** Start a browser session a task will use: a store's main
browser, its login-less aux browser, or the store-less web browser.
These are the same routes the store wrappers call — a start reuses a
healthy running session and never kills a running task's browser.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `start_store`, `start_aux`, `start_web` or `ziniao_launcher`. |
| `store_id` | string | The store, for `start_store`/`start_aux`. |
| `force` | bool | `start_store` only: kill-and-relaunch Ziniao in WebDriver mode when it sits in normal mode. Ask your user first. |

**Returns.** `ok` (plus the aux endpoint for `start_aux`), or a
`download_url` for `ziniao_launcher` — that URL is for a HUMAN to
open on the Windows machine; you cannot run the .bat.

This does NOT let you drive the browser. There is no click, navigate
or screenshot here — starting a session just makes one ready for the
tasks.

## manage_ziniao_account

**Purpose.** Manage Ziniao anti-detect accounts (Settings → Stores →
Ziniao) and list their browser profiles.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `list`, `create`, `update`, `delete`, `browsers` or `restart`. |
| `account_id` | string | The account, for `update`/`delete`/`browsers`/`restart`. |
| `name` | string | Display name; blank falls back to the company (as the UI does). |
| `company` | string | Ziniao tenant; required for `create`. |
| `username` | string | Login username; required for `create`. |
| `password` | string | Login password. **Write-only**; empty on update means unchanged. |
| `socket_port` | int | Local client API port (default 16851). |
| `client_path` | string | Client command/path (default `ziniao`). |

**Returns.** Account rows (no password field exists on them),
`browsers` → the profile list (`browser_name` + `browser_oauth`,
needed when creating a ziniao store), `restart` → `ok`.

`restart` only works while Ziniao sits in normal mode — it refuses
(409) rather than kill an active WebDriver session.

## manage_profile

**Purpose.** Manage AI profiles (Settings → AI): which model and
endpoint a task runs under.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `list`, `get`, `create`, `update`, `delete`, `set_default`, `validate` or `presets`. |
| `profile_id` | string | The profile (the new id for `create`; also lets `validate` restore masked keys from the stored env). |
| `name` | string | Display name; required for `create`. |
| `env` | object | Env vars, e.g. `{"ANTHROPIC_BASE_URL": …, "ANTHROPIC_MODEL": …, "ANTHROPIC_AUTH_TOKEN": …}`. API keys inside are **write-only** (masked on read); a masked value you echo back keeps the stored key. On `update` the env MERGES with the stored one: keys you omit stay unchanged, and a `null` value deletes that key. |
| `description` | string | Free-form note. |
| `load_global_mcp` | bool | Load the machine-wide MCP servers (default false). |
| `set_default` | bool | `create` only: also make it the account default. Opt-in — it silently re-points every future task. |

**Returns.** Profile rows with `env` present but key values masked;
`presets` → provider templates; `validate` → the probe verdict.

`validate` probes an env without saving — and it **spends one tiny
real model call**, so do not loop it. `set_default`/`delete` interact
with the caller's default: deleting the default resets it to
`default`.

## app_settings

**Purpose.** Server settings, the Google Workspace integration and
the vision (image-generation) key — Settings → General / Integrations
/ AI → Vision. `update`, `gws_enable`, `gws_disable` and
`vision_update` are admin-only.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `get`, `update`, `gws_status`, `gws_enable`, `gws_disable`, `vision_get` or `vision_update`. |
| `settings` | object | `update`: setting key → value. Accepted keys: `auth_required`, `browser_headless`, `max_agent_concurrency`, `default_schedule_phase_mode`, `default_schedule_timezone`, `telemetry_enabled`, `skills_auto_sync_enabled`, `task_retention_days`. Unknown keys are an error, and the result echoes the values that LANDED (the route clamps and normalizes). |
| `kie_api_key` | string | `vision_update`: the kie.ai key. **Write-only**; the result only reports whether one is set. |

**Returns.** The settings map (`get`), the landed values (`update`),
Google Workspace prereqs (`gws_status`), or the key status
(`vision_get`/`vision_update`).

## manage_email_account

**Purpose.** Manage email accounts (Settings → Email) and their
store links.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `list`, `get`, `create`, `update`, `delete`, `test`, `test_smtp`, `send`, `sync_now`, `links`, `link`, `unlink` or `info_by_store`. |
| `account_id` | string | The account, for `update`/`delete`/`test_smtp`/`send`/`links`. |
| `email` | string | Address; required for `create`/`test`. |
| `password` | string | Mailbox password. **Write-only**; required for `create`/`test`, empty on update means unchanged. |
| `imap_host` | string | IMAP host; omit on create to auto-discover. |
| `imap_port` | int | IMAP port (default 993). |
| `use_ssl` | bool | IMAP over SSL (default true). |
| `smtp_host` | string | SMTP host; omit on create to auto-discover. |
| `smtp_port` | int | SMTP port. |
| `smtp_use_tls` | bool | SMTP STARTTLS (default true). |
| `to` | string/list | `send`: recipient address(es). |
| `subject` | string | `send`: subject. |
| `body` | string | `send`: plain-text body. |
| `body_html` | string | `send`: optional HTML body. |
| `account_email` | string | `sync_now`: the account address to pull. |
| `store_id` | string | `link`/`unlink`/`info_by_store`: the store. |
| `email_account_id` | string | `link`: the account to link. |
| `link_id` | string | `unlink`: the link id (from `links`). |

**Returns.** Account rows (no password field exists on them), link
rows, or — for `info_by_store` — the per-account mail DB paths and
the query notes for reading mail.

**`send` sends a REAL email.** Only call it when your user asked for
that email. `test`/`test_smtp` log in once to verify credentials.

## manage_chat_bot

**Purpose.** Manage chat-bot group webhooks (WeCom, DingTalk, Feishu,
Slack) — Settings → Integrations → Chat bots.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `platforms`, `list`, `get`, `create`, `update`, `delete`, `test`, `send` or `send_file`. |
| `bot_id` | string | The bot, for `get`/`update`/`delete`/`test`/`send`/`send_file`. |
| `name` | string | Bot label; required for `create`. |
| `webhook_url` | string | The webhook URL; required for `create`. The host decides the platform. **Write-only** (masked on read). |
| `secret` | string | Signing secret. **Write-only**; empty string on update clears it. |
| `keyword` | string | Security keyword (not a secret; shown like in the UI). |
| `content` | string | `test` (optional — defaults to the standard test message) and `send`: the message. |
| `msgtype` | string | `send`: `text` (default) or `markdown`. |
| `content_base64` | string | `send_file`: the file, base64 (max 10 MB; WeCom only). |
| `filename` | string | `send_file`: the name to give the file. |

**Returns.** Bot rows — `webhook_url` masked, `has_secret` as a flag
(even from `get`, which the web UI's edit form reads raw).

**`test` and `send` post REAL messages to the group.** Use them only
on the user's behalf.

## manage_ticktick

**Purpose.** The TickTick / Dida365 integration (Settings →
Integrations): tasks land in the connected project.

**Arguments.**

| Name | Type | Notes |
| --- | --- | --- |
| `action` | string | Required. `status`, `setup_mcp`, `authorize`, `projects`, `configure` or `disconnect`. |
| `client_id` | string | `authorize`: the OAuth app client id. |
| `client_secret` | string | `authorize`: the OAuth app client secret. **Write-only**. |
| `service_type` | string | `authorize`: `dida365` (default) or `ticktick`. |
| `project_id` | string | `configure`: the project id from `projects` (empty string clears it). |

**Returns.** `status` → connection state; `authorize` → the
`auth_url`; `projects` → the project list; the rest → `ok`.

**You cannot finish OAuth.** `authorize` returns a URL for a HUMAN to
open and approve in their browser; hand it to your user, then poll
`status` until `connected` is true. `setup_mcp` clones and installs
the ticktick helper (network, can take a minute) — run it before the
first `authorize`, as the UI does.
