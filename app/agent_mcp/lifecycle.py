"""Lifecycle guidance for the hosted MCP endpoint.

An external agent sees a bare status string (``planned``,
``waiting``, …) and must know what it means and what it may do
next — in code, not prose. This module is that table:

* :func:`task_guidance` — one ``TaskStatus`` → meaning + the tools
  that make sense right now;
* :func:`plan_guidance` — one ``PlanStatus`` (a Schedule's plan
  lifecycle) → meaning + whether the schedule can fire;
* :func:`render_instructions` — the compact rendering for
  ``initialize.instructions`` (kept under ~400 tokens);
* :func:`render_status_reference` — the generated status section of
  ``agent/skills/vibe-seller/references/flows.md``.

The next-actions are DERIVED from the named groups in
``app/task_states.py`` (``STOPPABLE``, ``RETRIABLE``, ``WAKEABLE``,
``ACTIVE``) and the plan gate in ``app/plan_states.py`` / the
schedule routes — the same objects the routes check — so this table
cannot drift from the state machines. The status→meaning maps are
keyed by the enums themselves: a status added without a meaning here
fails ``tests/unit/test_agent_mcp_lifecycle.py`` (exhaustive guard).

Route guards each derivation cites (verified 2026-11-04):

* ``stop_task``     — ``app/routers/tasks.py`` ``stop_agent``:
  ``status not in STOPPABLE`` → the stop is a best-effort no-op, so
  the tool is only named for STOPPABLE states.
* ``retry_task``    — ``app/routers/tasks_conversation.py``
  ``retry_task``: ``status not in RETRIABLE`` → 400.
* ``review_plan``   — ``app/routers/tasks.py`` ``execute_plan``:
  ``status != PLANNED`` → 400, and ``plan is None`` → 400. (Approve
  on a plan-only task freezes the plan onto its schedule —
  ``execute_planned_task`` → ``approve_plan`` →
  ``_commit_plan_only_approval``.)
* ``answer_question`` — ``app/routers/tasks_conversation.py``
  ``answer_question``: 409 unless a live session or a parked
  question matches — i.e. unless the pending-question flag is set.
* ``delete_task``   — ``app/routers/tasks.py`` ``delete_task`` →
  ``app/task_delete.py``: 409 while the subtree is ACTIVE
  (designing/running); allowed in every other status.
* ``send_message``  — ``send_task_message`` has no status guard;
  named where it is THE move (wake a WAITING task, continue a
  finished one).
* ``wait_task``     — the MCP long-poll; it returns instantly on
  planned/waiting/terminal, so it is only named for in-flight
  states where waiting means something.
* ``run_schedule``  — ``app/routers/schedules.py``
  ``trigger_schedule``: 409 for a plan-mode schedule unless
  ``plan_status == ready``.
* ``update_schedule`` — PUT is never plan-gated; its ``replan``
  variant needs a plan-mode schedule (``/replan`` 400s otherwise)
  and its ``paused=false`` (resume) variant is 409 unless the plan
  gate is open (``resume_schedule``).
"""

from pathlib import Path

from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.routers.schedule_planning import schedule_needs_plan
from app.task_states import (
    ACTIVE,
    RETRIABLE,
    STOPPABLE,
    WAKEABLE,
    TaskStatus,
)

# The tool names the endpoint serves. Kept as constants so the
# table, the tests and the schedule tools cannot disagree about a
# spelling.
GET_TASK = 'get_task'
WAIT_TASK = 'wait_task'
GET_PENDING_QUESTIONS = 'get_pending_questions'
ANSWER_QUESTION = 'answer_question'
IMAGE_REQUEST = 'image_request'
REVIEW_PLAN = 'review_plan'
SEND_MESSAGE = 'send_message'
STOP_TASK = 'stop_task'
RETRY_TASK = 'retry_task'
DELETE_TASK = 'delete_task'
UPDATE_SCHEDULE = 'update_schedule'
RUN_SCHEDULE = 'run_schedule'

# Statuses where waiting means something: the task may still move on
# its own. wait_task returns IMMEDIATELY on planned (needs_operator),
# waiting, completed and failed, so advising it there would be a
# busy-loop, not guidance. pending/queued are the launcher's
# pre-execution states (STARTABLE covers pending; queued is the task
# queue's park — app/scheduler/task_queue.py).
_IN_FLIGHT = ACTIVE | {TaskStatus.PENDING, TaskStatus.QUEUED}

# The statuses nothing more will change (list_tasks' own inline
# ``terminal`` tuple). app/task_states.py deliberately has no named
# group for these, so it is defined once here, next to its users.
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED}

# TaskStatus → what it means. Keyed BY the enum: a new TaskStatus
# member without an entry here fails the exhaustive unit test.
TASK_STATUS_MEANINGS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: ('Created, not started yet; it launches on its own.'),
    TaskStatus.QUEUED: (
        "Waiting for a free run slot (the store's browser session is "
        'busy); it starts by itself.'
    ),
    TaskStatus.DESIGNING: (
        'The agent is writing a plan (read-only); it may ask a question.'
    ),
    TaskStatus.PLANNED: (
        'Plan ready — review_plan approves (executes) or rejects '
        'with feedback (revises). An unreviewed planned task holds '
        "one of the server's agent slots until it is approved, "
        'rejected or stopped — never abandon one.'
    ),
    TaskStatus.RUNNING: (
        'The agent is executing the task; it may ask a question.'
    ),
    TaskStatus.WAITING: (
        'Parked on child tasks or a wake condition; send_message wakes it.'
    ),
    TaskStatus.COMPLETED: (
        'Done — the result is available. send_message continues it; '
        'retry_task starts over.'
    ),
    TaskStatus.FAILED: (
        'Errored or stopped. retry_task starts over from scratch; '
        'send_message continues it.'
    ),
}

# Plan-only tasks (``is_plan_only``) author a Schedule's plan and
# never execute; where that changes what the status MEANS, it
# overrides the entry above. Only the two statuses a plan-only task
# can present to an operator differ.
_PLAN_ONLY_MEANINGS: dict[TaskStatus, str] = {
    TaskStatus.PLANNED: (
        "Plan ready — this task only authors its schedule's plan: "
        'approving (review_plan) freezes the plan onto the schedule '
        'and nothing executes. An unreviewed planned task holds one '
        "of the server's agent slots — approve, reject or stop it, "
        'never abandon one.'
    ),
    TaskStatus.COMPLETED: (
        "Done — it authored its schedule's plan; nothing was "
        'executed. send_message revises it.'
    ),
}

# PlanStatus → what it means for the Schedule that carries it. Every
# entry must address firing in plain words: an external agent that
# reports "scheduled" for a schedule that cannot fire is the exact
# failure this table exists to prevent.
PLAN_STATUS_MEANINGS: dict[PlanStatus, str] = {
    PlanStatus.NONE: (
        'No plan lifecycle (system schedule) — fires its prompt as-is.'
    ),
    PlanStatus.PLANNING: (
        'A plan-only task is authoring the plan; the schedule cannot '
        'fire until the plan is frozen, and the plan is then accepted '
        'automatically. Pause the schedule now '
        '(update_schedule paused=true) if your user has not seen the '
        'plan.'
    ),
    PlanStatus.READY: (
        'Plan written and accepted automatically — the schedule WILL '
        'fire on its cron (unless it is paused). Show the plan to '
        'your user; pause or replan it if they object.'
    ),
    PlanStatus.STALE: (
        'The prompt changed after the plan was frozen; the stored '
        'plan no longer matches. Re-plan; it cannot fire.'
    ),
    PlanStatus.FAILED: (
        'The planning task failed; re-plan to try again. It cannot fire.'
    ),
}

# ``none`` on a schedule that DOES owe a plan (only reachable by
# hand-edited rows — creation forces ``planning``) means something
# different from the system-schedule ``none``.
_NEEDS_PLAN_NONE_MEANING = (
    'No plan authored yet; re-plan to author one. It cannot fire.'
)

# A status the enum does not know (only reachable by a hand-edited
# row). Reported as-is instead of raising: one bad row must not
# make a whole listing fail.
_UNKNOWN_STATUS_MEANING = (
    'Unrecognised status — read the task detail before acting on it.'
)
_UNKNOWN_PLAN_MEANING = (
    'Unrecognised plan status — the schedule will not fire until it is '
    're-planned.'
)


def _task_next_actions(
    status: TaskStatus,
    *,
    has_pending_question: bool,
    has_plan: bool,
    has_active_descendant: bool = False,
    has_pending_image: bool = False,
) -> list[str]:
    """The tools that make sense right now, best first.

    Derived from the same groups the routes check (see the module
    docstring for the citation per tool) — never a per-status list.
    """
    actions: list[str] = []
    if has_pending_question:
        # answer_question 409s without one; the companion read is how
        # the caller gets the exact question text to key the answer.
        actions += [GET_PENDING_QUESTIONS, ANSWER_QUESTION]
    if has_pending_image:
        # A pending image card is the OTHER operator flag: the task
        # agent's generate call is parked on a human decision
        # (app/routers/vision.py generate_task_image blocks on a
        # future until confirm/cancel). image_request(get) is the read
        # that names the card; confirm/cancel resolve it.
        actions.append(IMAGE_REQUEST)
    if status == TaskStatus.PLANNED and has_plan:
        # execute_plan: PLANNED + a plan (both guards).
        actions.append(REVIEW_PLAN)
    if status in WAKEABLE or status in _TERMINAL:
        # send_task_message wakes a WAITING task, continues a
        # finished one. No status guard exists; named where it is
        # the move.
        actions.append(SEND_MESSAGE)
    if status in _IN_FLIGHT:
        actions.append(WAIT_TASK)
    if status in STOPPABLE:
        actions.append(STOP_TASK)
    if status in RETRIABLE:
        actions.append(RETRY_TASK)
    if status not in ACTIVE and not has_active_descendant:
        # delete route: 409 while ANY task in the SUBTREE is ACTIVE.
        # The guard is a property of the tree, not of this row — a
        # parent that failed while a child still runs cannot be
        # deleted, so it must not be named here. The tool layer
        # resolves the flag (app/agent_mcp/support.py) with the
        # route's own query.
        actions.append(DELETE_TASK)
    return actions


def task_guidance(
    task: Task,
    *,
    has_pending_question: bool,
    has_active_descendant: bool = False,
    has_pending_image: bool = False,
) -> dict:
    """Meaning + next actions for a task's current status.

    ``has_pending_question`` is the flag the tool layer already
    computes (live session question, or one parked on a WAITING
    task) — a pending question is a flag on an active state, not a
    status.

    ``has_pending_image`` is its sibling: the task agent proposed an
    image and its generate call is parked on a confirm card
    (``app/routers/vision.py``). Also a flag, never a status.

    ``has_active_descendant`` is the other flag: the DELETE guard is
    about the task's SUBTREE (app/task_delete.py refuses while any
    task in the tree is ACTIVE), so a parent whose child still runs
    must not be offered ``delete_task``. The tool layer resolves it
    with the route's own query.

    A status the enum does not know is reported as-is with a
    "look at it" meaning rather than raising: one hand-edited row
    must not make a whole task listing fail.
    """
    try:
        status = TaskStatus(task.status)
    except ValueError:
        return {
            'status': str(task.status),
            'status_meaning': _UNKNOWN_STATUS_MEANING,
            'next_actions': [GET_TASK],
        }
    plan_only = bool(getattr(task, 'is_plan_only', False))
    meaning = (
        _PLAN_ONLY_MEANINGS.get(status) if plan_only else None
    ) or TASK_STATUS_MEANINGS[status]
    return {
        'status': status.value,
        'status_meaning': meaning,
        'next_actions': _task_next_actions(
            status,
            has_pending_question=has_pending_question,
            has_plan=task.plan is not None,
            has_active_descendant=has_active_descendant,
            has_pending_image=has_pending_image,
        ),
    }


def _plan_gate_open(plan_status: PlanStatus, needs_plan: bool) -> bool:
    """May this schedule pass the fire gate right now?

    ``_schedule_can_fire`` (app/scheduler/cron.py) and
    ``trigger_schedule``: system and non-plan-mode schedules always
    pass; plan-mode schedules only at ``ready``.
    """
    return (not needs_plan) or plan_status == PlanStatus.READY


def _plan_next_actions(
    is_active: bool,
    plan_status: PlanStatus,
    needs_plan: bool,
) -> list[str]:
    """The tools that make sense for a schedule in this plan state.

    ``update_schedule`` is named where one of its variants is
    accepted:

    * ``paused=true`` — ALWAYS accepted (the pause route has no gate),
      and for an ACTIVE schedule mid-``planning`` it is the one call
      that preserves the human checkpoint: the plan is not ready yet,
      so the schedule cannot fire, and pausing now closes the window
      before the plan freezes.
    * ``replan`` — a plan-mode schedule that is not already planning.
    * ``paused=false`` (resume) — 409 unless the plan gate is open.

    A PAUSED schedule mid-``planning`` therefore names no update:
    pausing again is a no-op, resume would be refused, replan is an
    idempotent no-op, and the move is to wait for the planner.
    """
    actions: list[str] = []
    if plan_status == PlanStatus.PLANNING:
        if is_active:
            # Pause it FIRST: the plan freezes on its own, and once
            # it is ready the schedule fires unattended. Naming this
            # before wait_task keeps the checkpoint in the guidance
            # the agent actually follows.
            actions.append(UPDATE_SCHEDULE)
        # Follow the planning task (its id rides the schedule
        # result as plan_task_id). ExitPlanMode auto-approves for
        # schedule-owned tasks; if one does park at ``planned``,
        # task-level review_plan freezes it.
        actions += [WAIT_TASK, GET_TASK]
    if _plan_gate_open(plan_status, needs_plan):
        # trigger_schedule accepts regardless of is_active.
        actions.append(RUN_SCHEDULE)
        if not is_active:
            # Resume: accepted because the gate is open.
            actions.append(UPDATE_SCHEDULE)
    elif plan_status != PlanStatus.PLANNING:
        # Plan-mode schedule, gate closed, not mid-planning:
        # stale / failed / never-authored all move through replan.
        # (Mid-planning nothing is named: resume is 409 and replan
        # is an idempotent no-op — wait for the planner instead.)
        actions.append(UPDATE_SCHEDULE)
    return actions


def plan_guidance(schedule: Schedule) -> dict:
    """Meaning + fire-ability + next actions for a schedule's plan.

    ``fires`` answers "will this schedule fire as things stand?":
    the plan gate must be open AND the schedule must be active. A
    paused ready schedule reports ``fires: false`` — anything else
    and an external agent reports "scheduled" for a schedule that
    will never run.

    A plan status the enum does not know is treated as NOT ready
    (gate closed) rather than raising: one hand-edited row must not
    make a whole schedule listing fail.
    """
    needs_plan = schedule_needs_plan(schedule)
    raw = str(schedule.plan_status or PlanStatus.NONE)
    try:
        plan_status = PlanStatus(raw)
    except ValueError:
        return {
            'plan_status': raw,
            'plan_status_meaning': _UNKNOWN_PLAN_MEANING,
            'fires': False,
            'next_actions': [UPDATE_SCHEDULE],
        }
    if plan_status == PlanStatus.NONE and needs_plan:
        meaning = _NEEDS_PLAN_NONE_MEANING
    else:
        meaning = PLAN_STATUS_MEANINGS[plan_status]
    return {
        'plan_status': plan_status.value,
        'plan_status_meaning': meaning,
        'fires': bool(schedule.is_active)
        and _plan_gate_open(plan_status, needs_plan),
        'next_actions': _plan_next_actions(
            bool(schedule.is_active), plan_status, needs_plan
        ),
    }


def render_instructions() -> str:
    """The ``initialize.instructions`` payload (< ~400 tokens).

    Every task and plan status is named, so a client that shows the
    instructions gives the model the whole vocabulary even if it
    never reads a ``status_meaning`` field.

    The budget is for the SERVED instructions — this renderer plus
    the calling guidance the endpoint appends — so this text is kept
    tight; the per-status meanings carry the detail.
    """
    return (
        'Task lifecycle: pending (not started) → queued (waiting for '
        'a free slot) → designing (agent writes a plan, read-only) → '
        'planned (plan ready; review_plan approves or rejects with '
        'feedback) → running (executing) → waiting (parked on '
        'children or a condition; send_message wakes it) → completed '
        'or failed. A question can arrive mid-run: wait_task reason='
        'question → get_pending_questions → answer_question (key = '
        'full question text). An image proposal parks the agent: '
        'needs_operator pending.kind=image_confirm → '
        'image_request. Follow live tasks with '
        'wait_task: on question, on needs_operator (planned: '
        'review_plan) or terminal (read '
        'get_task). failed: retry_task '
        'starts over (wipes the workspace); send_message continues. '
        'completed: send_message continues or retry_task re-runs. A '
        'planned task holds an agent slot until approved, rejected or '
        'stopped — never abandon one. Schedule plan lifecycle: none '
        '(no plan; fires as-is) → planning (a plan-only task authors '
        'the plan; wait_task follows it) → ready (accepted '
        'automatically; it WILL fire — show the plan to your user, '
        'pause or replan if they object) → stale (prompt edited; '
        're-plan) → failed (planner failed; re-plan). A schedule whose '
        'plan is not ready never fires; a paused one neither.'
    )


# ── Generated skill reference ────────────────────────────────

FLOWS_MARKER_BEGIN = '<!-- lifecycle:begin -->'
FLOWS_MARKER_END = '<!-- lifecycle:end -->'
FLOWS_PATH = Path('agent/skills/vibe-seller/references/flows.md')


def _plan_fires_note(schedule_active: bool, gate_open: bool) -> str:
    if not gate_open:
        return 'no (plan gate)'
    return 'yes' if schedule_active else 'no (paused)'


def render_status_reference() -> str:
    """The generated status section for ``references/flows.md``.

    Regenerate with ``python -m app.agent_mcp.lifecycle``. A unit
    test compares the file against this rendering, so the skill
    cannot drift from the table.
    """
    lines = [
        '## Status reference',
        '',
        'Generated from `app/agent_mcp/lifecycle.py` — do not edit',
        'between the `lifecycle` markers; run',
        '`python -m app.agent_mcp.lifecycle` to regenerate.',
        '',
        'What each status means, and which tools make sense in it. A',
        'pending question overrides everything: `wait_task` returns',
        '`reason=question`, then `get_pending_questions` →',
        '`answer_question` — it is a flag on an active state, not a',
        'status. A pending image proposal is the sibling flag:',
        'the agent proposed an image and its generate call is parked',
        'on a confirm card; `wait_task` returns',
        '`reason=needs_operator` with `pending.kind=image_confirm`,',
        'then `image_request` (get, then confirm or cancel).',
        '',
        'The table shows the tools for a task with nothing running',
        'under it. `delete_task` is also a property of the SUBTREE:',
        'the route refuses it while any task in the tree is running',
        '("stop them first"), so a parent with a live child is not',
        'offered it.',
        '',
        '### Task statuses',
        '',
        '| Status | Meaning | Next tools |',
        '| --- | --- | --- |',
    ]
    for status in TaskStatus:
        actions = ', '.join(
            _task_next_actions(
                status, has_pending_question=False, has_plan=True
            )
        )
        lines.append(
            f'| `{status.value}` | {TASK_STATUS_MEANINGS[status]} | '
            f'`{actions}` |'
        )
    lines += [
        '',
        "A plan-only task (one authoring a schedule's plan) reads",
        'differently at the two statuses that matter:',
        '',
    ]
    for status, meaning in _PLAN_ONLY_MEANINGS.items():
        lines.append(f'- `{status.value}` — {meaning}')
    lines += [
        '',
        '### Schedule plan statuses',
        '',
        'Shown for an active schedule; a paused one fires in no plan',
        'status.',
        '',
        '| Plan status | Meaning | Fires? | Next tools |',
        '| --- | --- | --- | --- |',
    ]
    for plan_status in PlanStatus:
        # ``none`` renders as the system-schedule shape (no plan
        # lifecycle); the owes-a-plan variant lives only on
        # hand-edited rows.
        needs_plan = plan_status is not PlanStatus.NONE
        gate_open = _plan_gate_open(plan_status, needs_plan)
        actions = ', '.join(_plan_next_actions(True, plan_status, needs_plan))
        fires = _plan_fires_note(True, gate_open)
        lines.append(
            f'| `{plan_status.value}` | '
            f'{PLAN_STATUS_MEANINGS[plan_status]} | {fires} | '
            f'`{actions}` |'
        )
    lines += [
        '',
        'A paused schedule resumes (`update_schedule`) once the plan',
        'gate is open — resuming mid-`planning` is refused.',
    ]
    return '\n'.join(lines)


def regenerate_flows_section(repo_root: Path | None = None) -> Path:
    """Rewrite the generated section of ``flows.md`` in place.

    Returns the path written. The content between the markers is
    replaced wholesale; everything outside them is preserved.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    path = root / FLOWS_PATH
    text = path.read_text(encoding='utf-8')
    begin = text.index(FLOWS_MARKER_BEGIN) + len(FLOWS_MARKER_BEGIN)
    end = text.index(FLOWS_MARKER_END)
    updated = (
        text[:begin] + '\n\n' + render_status_reference() + '\n\n' + text[end:]
    )
    path.write_text(updated, encoding='utf-8')
    return path


if __name__ == '__main__':  # pragma: no cover
    import sys

    written = regenerate_flows_section()
    sys.stdout.write(f'regenerated {written}\n')


__all__ = [
    'FLOWS_MARKER_BEGIN',
    'FLOWS_MARKER_END',
    'FLOWS_PATH',
    'PLAN_STATUS_MEANINGS',
    'TASK_STATUS_MEANINGS',
    'plan_guidance',
    'regenerate_flows_section',
    'render_instructions',
    'render_status_reference',
    'task_guidance',
]
