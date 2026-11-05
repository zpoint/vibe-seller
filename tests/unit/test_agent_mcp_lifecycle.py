"""Unit tests for the MCP lifecycle table (``app/agent_mcp/lifecycle.py``).

Three guards:

1. **Exhaustive** — every ``TaskStatus`` / ``PlanStatus`` member has
   guidance; a status added without an entry fails.
2. **Consistency** — ``next_actions`` never names a tool the route
   would reject in that state. Both sides of each assertion derive
   from the SAME named groups in ``app/task_states.py`` /
   ``app/plan_states.py`` that the routes import (cited per test);
   nothing here re-encodes a status list by hand.
3. **No drift** — ``agent/skills/vibe-seller/references/flows.md``
   carries exactly what ``render_status_reference()`` produces.
"""

import enum
from pathlib import Path

import pytest

from app.agent_mcp.lifecycle import (
    FLOWS_MARKER_BEGIN,
    FLOWS_MARKER_END,
    FLOWS_PATH,
    PLAN_STATUS_MEANINGS,
    TASK_STATUS_MEANINGS,
    plan_guidance,
    regenerate_flows_section,
    render_instructions,
    render_status_reference,
    task_guidance,
)
from app.agent_mcp.router import INSTRUCTIONS
from app.agent_mcp.support import WAIT_TASK_INSTANT_STATUSES
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.task_states import (
    ACTIVE,
    RETRIABLE,
    STOPPABLE,
    WAKEABLE,
    TaskStatus,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

# The two shapes that are not named groups in app/task_states.py:
# the launcher's pre-execution states (STARTABLE covers PENDING; the
# task queue parks at QUEUED) and the terminal pair (the inline
# ``terminal`` tuple in list_tasks). wait_task returns INSTANTLY on
# planned/waiting/terminal (app/agent_mcp/tools.py wake conditions),
# so it is only guidance where the task can still move on its own.
_IN_FLIGHT = ACTIVE | {TaskStatus.PENDING, TaskStatus.QUEUED}
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED}


def _task(status, *, plan='plan text', is_plan_only=False):
    return Task(
        status=status.value if isinstance(status, TaskStatus) else status,
        plan_mode=True,
        plan=plan,
        is_plan_only=is_plan_only,
    )


def _sched(plan_status, *, plan_mode=True, is_system=False, active=True):
    return Schedule(
        plan_status=plan_status.value
        if isinstance(plan_status, PlanStatus)
        else plan_status,
        plan_mode=plan_mode,
        is_system=is_system,
        is_active=active,
    )


# ── 1. Exhaustive ──────────────────────────────────────────


def _missing(mapping: dict, enum_cls: type[enum.Enum]) -> list:
    return [member for member in enum_cls if member not in mapping]


def test_every_task_status_has_meaning():
    assert _missing(TASK_STATUS_MEANINGS, TaskStatus) == []
    # No stale entries either: a meaning for a removed status is
    # the same drift, the other way round.
    assert set(TASK_STATUS_MEANINGS) == set(TaskStatus)


def test_every_plan_status_has_meaning():
    assert _missing(PLAN_STATUS_MEANINGS, PlanStatus) == []
    assert set(PLAN_STATUS_MEANINGS) == set(PlanStatus)


def test_exhaustive_guard_catches_a_new_status():
    """A status added without guidance must fail, not fall through.

    Enumerating a stand-in enum with an unguided member is exactly
    what adding ``TaskStatus.NEW`` would look like to the tests
    above.
    """

    class _WithUnguidedMember(enum.StrEnum):
        GUIDED = 'guided'
        UNGUIDED = 'unguided'

    assert _missing({'guided': 'x'}, _WithUnguidedMember) == [
        _WithUnguidedMember.UNGUIDED
    ]


def test_plan_only_variants_only_where_they_differ():
    # The override applies to exactly the statuses a plan-only task
    # can show an operator (planned: approving freezes onto the
    # schedule; completed: it authored, nothing executed); every
    # other status keeps the base text.
    differing = set()
    for status in TaskStatus:
        base = task_guidance(_task(status), has_pending_question=False)[
            'status_meaning'
        ]
        overridden = task_guidance(
            _task(status, is_plan_only=True), has_pending_question=False
        )['status_meaning']
        if overridden != base:
            differing.add(status)
            assert 'schedule' in overridden
    assert differing == {TaskStatus.PLANNED, TaskStatus.COMPLETED}


# ── 2. Consistency: actions vs route guards ────────────────


def test_next_actions_match_route_guards_everywhere():
    """Each named tool is exactly what its route accepts.

    Route guards (per tool, cited):

    * retry_task     — app/routers/tasks_conversation.py retry_task:
      ``status not in RETRIABLE`` → 400.
    * stop_task      — app/routers/tasks.py stop_agent:
      ``status not in STOPPABLE`` → best-effort no-op that reports
      ok (so naming it there would lie), refused as guidance.
    * review_plan    — app/routers/tasks.py execute_plan:
      ``status != PLANNED`` → 400 and ``plan is None`` → 400.
    * delete_task    — app/routers/tasks.py delete_task →
      app/task_delete.py: 409 while ANY task in the SUBTREE is
      ACTIVE. The guard is a property of the tree, not of this row,
      so the row's own status is only half of it — a parent that
      failed while a child still runs cannot be deleted either. The
      subtree half is a flag the tool layer resolves with the
      route's own query (app/agent_mcp/support.py).
    * answer_question — app/routers/tasks_conversation.py
      answer_question: 409 without a pending question.
    """
    for status in TaskStatus:
        actions = task_guidance(_task(status), has_pending_question=False)[
            'next_actions'
        ]

        assert ('retry_task' in actions) == (status in RETRIABLE), (
            f'retry_task vs RETRIABLE mismatch at {status}'
        )
        assert ('stop_task' in actions) == (status in STOPPABLE), (
            f'stop_task vs STOPPABLE mismatch at {status}'
        )
        assert ('review_plan' in actions) == (status is TaskStatus.PLANNED), (
            f'review_plan vs PLANNED mismatch at {status}'
        )
        assert ('delete_task' in actions) == (status not in ACTIVE), (
            f'delete_task vs ACTIVE mismatch at {status}'
        )

        # The subtree half of the delete guard: with a running
        # descendant the route refuses the delete (409 "stop them
        # first"), so the table must not name delete_task — for ANY
        # status, including the ones that name it above.
        blocked = task_guidance(
            _task(status),
            has_pending_question=False,
            has_active_descendant=True,
        )['next_actions']
        assert 'delete_task' not in blocked, (
            f'delete_task named despite an active descendant at {status}'
        )
        assert 'answer_question' not in actions
        assert 'get_pending_questions' not in actions

        # Unguarded tools, so the guard is "does it mean anything":
        # wait_task returns instantly outside the in-flight states
        # (tools.py NEEDS_OPERATOR_STATUSES / TERMINAL_STATUSES);
        # send_task_message has no status guard but is THE move only
        # to wake a WAITING task (wake route: WAKEABLE) or continue
        # a finished one (follow-up branches).
        assert ('wait_task' in actions) == (status in _IN_FLIGHT), (
            f'wait_task guidance mismatch at {status}'
        )
        assert ('send_message' in actions) == (
            status in WAKEABLE or status in _TERMINAL
        ), f'send_message guidance mismatch at {status}'


def test_pending_question_flag_adds_the_answer_tools_first():
    # The question preempts everything else: answer_question 409s
    # without it (route above), so the pair appears iff the flag is
    # set — and leads the list, because nothing else matters first.
    for status in TaskStatus:
        flagged = task_guidance(_task(status), has_pending_question=True)[
            'next_actions'
        ]
        unflagged = task_guidance(_task(status), has_pending_question=False)[
            'next_actions'
        ]
        assert flagged[:2] == ['get_pending_questions', 'answer_question']
        assert flagged[2:] == unflagged


def test_planned_without_a_plan_omits_review_plan():
    # execute_plan: 'Task has no plan' → 400.
    task = _task(TaskStatus.PLANNED, plan=None)
    actions = task_guidance(task, has_pending_question=False)['next_actions']
    assert 'review_plan' not in actions


def test_plan_only_planned_still_offers_review_plan():
    # Approving a plan-only task at PLANNED freezes the plan onto
    # its schedule (execute_planned_task → approve_plan →
    # _commit_plan_only_approval); execute_plan's guard is the same
    # PLANNED-and-has-plan pair.
    task = _task(TaskStatus.PLANNED, is_plan_only=True)
    guidance = task_guidance(task, has_pending_question=False)
    assert 'review_plan' in guidance['next_actions']
    assert 'schedule' in guidance['status_meaning']


# ── 3. Plan guidance vs the schedule routes ─────────────────


def test_fires_matches_the_fire_gate():
    """``fires`` == is_active AND the cron fire gate.

    _schedule_can_fire (app/scheduler/cron.py) and
    trigger_schedule (app/routers/schedules.py): system and
    non-plan-mode schedules always pass; plan-mode schedules only
    at ``ready``. A paused schedule fires in no state — reporting
    ``fires: true`` for one is the "agent said scheduled" bug.
    """
    for plan_status in PlanStatus:
        for plan_mode, is_system in (
            (True, False),  # user schedule: owes a plan
            (False, False),
            (False, True),  # system seed: no plan lifecycle
        ):
            gate_open = (not (plan_mode and not is_system)) or (
                plan_status is PlanStatus.READY
            )
            for active in (True, False):
                guidance = plan_guidance(
                    _sched(
                        plan_status,
                        plan_mode=plan_mode,
                        is_system=is_system,
                        active=active,
                    )
                )
                assert guidance['fires'] == (active and gate_open), (
                    f'fires mismatch at {plan_status} '
                    f'(plan_mode={plan_mode}, system={is_system}, '
                    f'active={active})'
                )


def test_run_schedule_named_only_when_the_gate_is_open():
    # trigger_schedule: 409 'Schedule cannot fire with
    # plan_status=...' for a plan-mode schedule that is not ready.
    for plan_status in PlanStatus:
        for plan_mode in (True, False):
            gate_open = (not plan_mode) or (plan_status is PlanStatus.READY)
            actions = plan_guidance(_sched(plan_status, plan_mode=plan_mode))[
                'next_actions'
            ]
            assert ('run_schedule' in actions) == gate_open, (
                f'run_schedule mismatch at {plan_status}'
            )


def test_paused_mid_planning_names_no_update():
    # resume_schedule: 409 'Cannot resume schedule with
    # plan_status=...' unless ready — and /replan is an idempotent
    # no-op while the planning task is non-terminal. Nothing to
    # name but following the planner.
    guidance = plan_guidance(_sched(PlanStatus.PLANNING, active=False))
    assert guidance['next_actions'] == ['wait_task', 'get_task']


def test_active_mid_planning_names_the_pause_first():
    """An ACTIVE schedule mid-planning names update_schedule.

    The pause variant is ALWAYS accepted (pause_schedule has no
    gate), and it is the one call that preserves the human
    checkpoint: the plan freezes on its own, and once it is ready the
    schedule fires unattended. It is named FIRST so an agent that
    follows the guidance pauses before the plan freezes, not after.
    """
    guidance = plan_guidance(_sched(PlanStatus.PLANNING, active=True))
    assert guidance['next_actions'] == [
        'update_schedule',
        'wait_task',
        'get_task',
    ]
    assert guidance['fires'] is False


def test_planning_meaning_says_to_pause():
    """The meaning has to name the pause, not just the status."""
    meaning = PLAN_STATUS_MEANINGS[PlanStatus.PLANNING]
    assert 'pause' in meaning.lower()
    assert 'automatically' in meaning, (
        'the plan is accepted by the server — the agent must know'
    )
    assert 'fire' in meaning


def test_not_ready_plan_status_names_replan():
    for plan_status in (PlanStatus.STALE, PlanStatus.FAILED):
        actions = plan_guidance(_sched(plan_status, active=False))[
            'next_actions'
        ]
        assert actions == ['update_schedule']


def test_every_plan_meaning_addresses_firing():
    # An external agent must never report "scheduled" for a schedule
    # that cannot fire — so every plan meaning says so in plain
    # words.
    for meaning in PLAN_STATUS_MEANINGS.values():
        assert 'fire' in meaning
    # The hand-edited edge (plan_mode schedule at none) says it too.
    assert (
        'cannot fire'
        in plan_guidance(_sched(PlanStatus.NONE))['plan_status_meaning']
    )


def test_needs_plan_none_is_the_owes_a_plan_variant():
    guidance = plan_guidance(_sched(PlanStatus.NONE))
    assert (
        guidance['plan_status_meaning'] != PLAN_STATUS_MEANINGS[PlanStatus.NONE]
    )
    assert 'cannot fire' in guidance['plan_status_meaning']
    # …while a non-plan (system) schedule at none fires as-is.
    system = plan_guidance(
        _sched(PlanStatus.NONE, plan_mode=False, is_system=True)
    )
    assert system['fires'] is True


# ── 4. Instructions + generated skill section ───────────────


def test_unknown_status_does_not_break_a_listing():
    """One hand-edited row must not make a whole listing fail.

    The status column is a plain string, so a value outside the enum
    is reachable in principle. The guidance reports it as-is with a
    "look at it" meaning instead of raising.
    """
    guidance = task_guidance(_task('bogus'), has_pending_question=False)
    assert guidance['status'] == 'bogus'
    assert 'Unrecognised' in guidance['status_meaning']
    assert guidance['next_actions'] == ['get_task']

    schedule_guidance = plan_guidance(_sched('garbage'))
    assert schedule_guidance['plan_status'] == 'garbage'
    assert 'Unrecognised' in schedule_guidance['plan_status_meaning']
    assert schedule_guidance['fires'] is False
    assert schedule_guidance['next_actions'] == ['update_schedule']


def test_instructions_under_400_tokens_and_complete():
    text = render_instructions()
    assert len(text) / 4 < 400, f'{len(text)} chars ≈ {len(text) // 4} tokens'
    for member in TaskStatus:
        assert member.value in text, f'{member} missing from instructions'
    for member in PlanStatus:
        assert member.value in text, f'{member} missing from instructions'


def _flows_section(path: Path) -> str:
    text = path.read_text(encoding='utf-8')
    begin = text.index(FLOWS_MARKER_BEGIN) + len(FLOWS_MARKER_BEGIN)
    end = text.index(FLOWS_MARKER_END)
    return text[begin:end].strip()


def test_flows_md_matches_the_table():
    """The skill's status section is the table, byte for byte."""
    section = _flows_section(REPO_ROOT / FLOWS_PATH)
    assert section == render_status_reference().strip()
    for member in TaskStatus:
        assert member.value in section
    for member in PlanStatus:
        assert member.value in section


def test_regenerate_replaces_only_the_marked_section(tmp_path):
    """The regeneration entrypoint is surgical.

    Everything outside the markers survives; what is between them
    becomes the current rendering.
    """
    inner = tmp_path / FLOWS_PATH
    inner.parent.mkdir(parents=True)
    inner.write_text(
        'keep-before\n'
        f'{FLOWS_MARKER_BEGIN}\nstale content\n{FLOWS_MARKER_END}\n'
        'keep-after\n',
        encoding='utf-8',
    )
    regenerate_flows_section(repo_root=tmp_path)
    text = inner.read_text(encoding='utf-8')
    assert text.startswith('keep-before\n')
    assert text.endswith('keep-after\n')
    assert 'stale content' not in text
    assert _flows_section(inner) == render_status_reference().strip()


def test_wait_task_is_named_only_where_it_waits():
    """The table and the long-poll must agree on when waiting waits.

    ``wait_task`` returns INSTANTLY on planned (a plan is ready for
    review), waiting (parked on a condition) and the terminal states —
    advising it there would be a busy-loop, not guidance. So the table
    names it only for the states where it actually blocks.
    """
    for status in TaskStatus:
        actions = task_guidance(_task(status), has_pending_question=False)[
            'next_actions'
        ]
        instant = status in WAIT_TASK_INSTANT_STATUSES
        assert ('wait_task' in actions) is not instant, (
            f'{status}: wait_task '
            f'{"should not be" if instant else "should be"} named'
        )


def test_served_instructions_name_every_status_and_stay_short():
    """What ``initialize`` actually serves, not just the renderer."""
    assert len(INSTRUCTIONS) / 4 < 400, (
        f'{len(INSTRUCTIONS)} chars ≈ {len(INSTRUCTIONS) // 4} tokens'
    )
    for member in TaskStatus:
        assert member.value in INSTRUCTIONS, f'{member} missing'


# ── The two facts an agent must not be able to miss ─────────


def test_ready_plan_says_it_was_accepted_automatically():
    """A schedule's plan is accepted by the SERVER, not by a human.

    The wording has to say that, because the alternative reading —
    "the plan is ready, so someone approved it" — is what makes an
    agent report "scheduled" without ever showing the plan.
    """
    meaning = PLAN_STATUS_MEANINGS[PlanStatus.READY]
    assert 'automatically' in meaning
    assert 'WILL fire' in meaning
    # The instruction to the agent: show it, and how to object.
    assert 'show the plan' in meaning.lower()
    assert 'pause' in meaning
    assert 'replan' in meaning


def test_planned_task_says_it_holds_an_agent_slot():
    """A task parked on an unreviewed plan holds a server slot.

    The reader of the plan in ``app/ai/claude_backend_stream.py``
    awaits the approval handler inline, so the session's permit is
    never released while the plan sits unreviewed. Two such tasks
    wedge the server. The wording must tell the agent not to leave
    one behind.
    """
    meaning = TASK_STATUS_MEANINGS[TaskStatus.PLANNED]
    assert 'agent slot' in meaning
    assert 'approved' in meaning
    assert 'rejected' in meaning
    assert 'stopped' in meaning
    assert 'never abandon' in meaning.lower()

    # The plan-only variant says it too — a plan-only task that parks
    # at planned holds a slot exactly the same way.
    plan_only = task_guidance(
        _task(TaskStatus.PLANNED, is_plan_only=True),
        has_pending_question=False,
    )['status_meaning']
    assert 'agent slot' in plan_only
    assert 'never abandon' in plan_only.lower()


def test_instructions_carry_both_facts():
    """``initialize.instructions`` is the only guidance some clients
    show — the two facts have to be in it, not just in the per-result
    meanings."""
    assert 'never abandon' in render_instructions().lower()
    assert 'agent slot' in render_instructions()
    assert 'accepted automatically' in render_instructions()
    assert 'show the plan' in render_instructions().lower()


def test_ready_meaning_survives_into_the_generated_reference():
    """The generated flows.md table is the manual's copy of the
    meanings — the new wording must be in it, not only in code."""
    reference = render_status_reference()
    assert 'accepted automatically' in reference
    assert 'never abandon' in reference.lower()
    assert 'agent slot' in reference


def test_pending_image_flag_adds_image_request_first():
    """The image-confirm card is the sibling of the question flag.

    While one is showing, the task agent's generate call is parked on
    a human decision (``app/routers/vision.py`` blocks on a future),
    so the guidance names ``image_request`` first — whatever status
    the task itself is in.
    """
    for status in TaskStatus:
        flagged = task_guidance(
            _task(status), has_pending_question=False, has_pending_image=True
        )['next_actions']
        unflagged = task_guidance(
            _task(status), has_pending_question=False, has_pending_image=False
        )['next_actions']
        assert flagged[0] == 'image_request', status
        assert flagged[1:] == unflagged, status


def test_both_flags_order_question_before_image():
    """A question outranks a card: answer_question 409s without one,
    and the user typing in the composer interrupts the card anyway."""
    guidance = task_guidance(
        _task(TaskStatus.RUNNING),
        has_pending_question=True,
        has_pending_image=True,
    )
    assert guidance['next_actions'][:3] == [
        'get_pending_questions',
        'answer_question',
        'image_request',
    ]


def test_image_gate_survives_into_instructions_and_reference():
    """The new wait state has to reach the agent through BOTH served
    surfaces, not only through per-result guidance."""
    instructions = render_instructions()
    assert 'image_confirm' in instructions
    assert 'image_request' in instructions
    reference = render_status_reference()
    assert 'image_confirm' in reference
    assert 'image_request' in reference
