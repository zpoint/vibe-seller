---
description: "Scheduled tasks: frequency and timezone choices, store schedules vs All-Stores schedules (Fan out / Single), optional plan review, pause and Run-now, run history, and cross-run memory."
---

# Scheduled Tasks

A scheduled task is "create a task automatically when the time comes": you write
a recurring job (daily report export, weekly ad check, hourly inbox sweep) once,
pick a timetable, and it fires on its own from then on.

## Creating a schedule

The **Tasks** page in the sidebar has two sub-tabs, **Tasks** and **Scheduled**;
on the Scheduled tab, click **New Schedule**. As with one-off tasks, **where you
create it decides its scope** — [Tasks](using-tasks.md) explains the two scopes:

- Created **with a store selected** — the modal is titled "New Schedule for
  {store}"; each fire creates one task for that store, using that store's
  browser and memory;
- Created **with no store selected** (All Stores scope) — you get an extra
  **Mode** choice, covered in the next section.

Write what the AI should do on each recurring basis (the subtitle asks "What
should the AI do on a recurring basis?" — phrase it like a [task
description](using-tasks.md#creating-a-task)), pick the frequency and time,
submit.

![The New Schedule form: frequency, at-time, timezone, and — in the All Stores scope — the execution mode with "Run stores one at a time"](images/schedules-app-new-modal_en.png)

## Two scopes and the execution modes

| | Store schedule | All Stores · **Fan out** | All Stores · **Single** |
|---|---|---|---|
| Per fire | **1 task** for that store | **one task per store**, fired in parallel | exactly **1 task**, bound to no store |
| Browser | That store's Ziniao / Chrome profile | Each store task uses its own store's browser | The generic web browser (public pages, email, chat messages); store-backend work is split into sub-tasks by the agent |
| Good for | One shop's reports or ad patrols | Every store doing the same job: weekly ad audit, listing patrol | Work that spans or sits outside stores: a shared mailbox, cross-store summaries |

Fan-out schedules have two optional behaviors:

- **Run stores one at a time** — the batch's store tasks line up and run one
  after another instead of sharing browsers at the same moment. Use it when
  every store logs into the same marketplace portal: on one machine the Ziniao
  profiles crowd into one client, and two stores driving the same portal at
  once starve each other. Unchecked, the stores run in parallel. One store
  failing does not block the rest of the line;
- **Finalize step** — when the job needs a single cross-store summary after
  every store finishes, the AI registers it while authoring the plan (the plan
  panel then shows "Finalize step (registered by the agent)"). Once the whole
  batch is done, the system automatically runs one extra store-less task that
  receives every store's results and merges them into the overall report.
  Nothing for you to do — just read it in the run history.

![A fan-out schedule with serial runs: the plan panel shows "stores run one at a time" and the finalize step; runs are grouped per store](images/schedules-app-fanout-serial_en.png)

## Review the plan first (optional)

A new schedule is **automatic** by default: when it's due, it runs your
instructions directly — nothing to confirm.

If you'd like to see how the AI plans to do it first, tick **Let me review the
plan before it runs** when you create it:

- Right after creation the AI writes a plan (status **Planning**); when it's
  done the status becomes **Needs your approval**, and a bar appears at the
  **very top** of the app: "N scheduled tasks have a plan waiting for your
  approval → Review". Click it to open the plan.
- Press **Approve** and the status becomes **Ready**; every later run follows
  that plan without re-planning. Not right? Press **Request changes**, say what
  to change, and the AI writes a new version for you to approve.
- **Approval never expires**: the plan waits for you as long as it takes.
  Until you approve, the schedule runs nothing.
- To adjust a plan that's already in use, also press **Request changes** — the
  **approved plan keeps running** until you approve the new one.
- **Editing the schedule description** drops the old plan; the AI writes a new
  one for your approval, and the schedule doesn't run until you approve it.
  Changing the title, frequency or timezone doesn't affect the plan.
- If writing the plan fails, the status shows **Plan failed** with the reason;
  press **Try again**.

You can switch review on or off any time under **Edit**: switched on, it needs
an approved plan before it runs again; switched off, the plan is dropped and it
runs your instructions directly.

## Frequency and timezone

Frequencies are "every N minutes / hours / days", or a weekly (pick the
weekday) or monthly (pick the day of month) cycle; day/week/month runs also
take an **At time** (hour and minute). A one-day interval displays as "Daily
HH:MM" in the list.

Each schedule carries its own **timezone** — "the time it fires" is computed in
that zone, independent of the server or where you are. New schedules start from
your browser's timezone; the global default is set in **Settings → General →
Schedule Defaults → Default Schedule Timezone**.

## Defaults

**Settings → General → Schedule Defaults** controls two things: the default
timezone above, and **Default Mode** — whether a new *All Stores* schedule
starts with **Fan out (one task per store)** or **Single (one task)**
pre-selected. (The default Auto/Review choice for one-off tasks lives next door
under **Task Defaults → Default Execution Mode**.)

## Pause, resume, Run Now

The schedule detail page has **Pause / Resume** and **Run Now**:

- Run Now fires once regardless of whether the schedule is paused — for a
  manual pass before a vacation, or a trial run right after you approve a plan.
  A schedule with plan review needs an approved plan first (otherwise the
  button is disabled);
- While one of this schedule's runs is still in progress, Run Now is disabled
  with the tooltip "A task is already running";
- **Runs** lists the tasks each fire created (the planning task is excluded);
  All-Stores runs are grouped by store. Open any run for the normal task detail
  — follow it up or retry it there. The next fire time is shown as **Next Run**.

## AI profile

Schedules created in the web app **follow your default AI profile**: each fire
takes whatever your current default profile is at that moment. Switch your
default from model A to model B and every unpinned schedule uses B from the
next fire — no per-schedule edits.

Schedules created by external agents (over MCP) can **pin** a specific profile;
pinned schedules ignore your default. The toggle in Settings → AI profiles,
**"Sync scheduled tasks when changing the default profile"** (off by default),
exists for those: when on, changing the default also re-points your pinned
schedules to the new default.

## Memory across runs

Successive fires of one schedule don't start from amnesia: the agent keeps
cursors between runs — "last email read", "last order processed", "last report
date" — so each run handles only what's new. Nothing to configure; the agent
maintains this per task type on its own.

## Common recipes

| What you want | How to set it up |
|---|---|
| Export yesterday's sales report for this shop at 9:00 every morning | Schedule under the store, every 1 day at 09:00 |
| A weekly ad audit of every store, plus one merged summary | No store, **Fan out** + **Run stores one at a time**; the finalize step gets registered by the AI in the plan |
| Sweep the shared inbox hourly: reply where due, file what matters | No store, **Single**, every 1 hour |

## Limits and gotchas

- **Mode and store binding are fixed at creation.** You can't convert a store
  schedule into an All-Stores one later — delete it and create it again;
- A **Fan out** schedule creates a task for **every store on this server** per
  fire — with many stores, mind concurrency and token spend;
- A schedule with plan review but no approved plan yet skips its fires and
  refuses Run Now;
- **A fired run never waits for an answer.** Nobody is at the screen when a
  schedule fires, so the AI is not allowed to stop and ask: it picks the
  option it would recommend (the safest one when unsure), or skips a step it
  can't do safely, and lists every question it would have asked under
  "Open questions" in its result — the run then completes, it does not sit in
  **Waiting** for your reply. Put decisions you want made a certain way
  into the schedule's prompt. (With plan review on, the AI writing the plan
  may still ask — you are reviewing it then.)
- If a fired task is stuck in **Waiting** for your answer when the next fire
  arrives, the scheduler cancels the stale waiting run rather than blocking
  the timetable — the new fire proceeds normally.
- If the computer is asleep or off, or Vibe Seller isn't running, when a fire
  comes due, that fire is skipped and not made up later. To keep overnight
  schedules reliable, see [Keep It Running 24/7](always-on.md).
