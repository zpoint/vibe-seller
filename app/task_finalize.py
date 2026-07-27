"""The terminal-state writer — the one place a run is declared over.

Lives beside :mod:`app.task_outcome` because the two halves belong
together: that module decides WHAT a run produced, this one turns that
verdict into a status transition, an SSE event and a commit. Split out
of ``task_runner_auto`` (which was over the per-file line limit) rather
than shuffling lines to fit — the finalizer is called from both the
auto-run pipeline and the follow-up pipeline and was never specific to
either.

Both entry points in ``task_runner_auto`` call
:func:`finalize_terminal_state`; nothing else may write a terminal
status for a RUNNING/DESIGNING task.
"""

from datetime import UTC, datetime, timedelta
import json
import logging

from app.ai.claude_backend_manager import agent_manager
from app.ai.claude_backend_utils import parse_wait_condition
from app.ai.task_status_reconcile import should_park_qa_followup
from app.database import async_session
from app.events.bus import event_bus
from app.models.task import Task
from app.task_outcome import OutcomeKind, apply_outcome, resolve_outcome
from app.task_runner import (
    has_incomplete_todos,
    mark_waiting_for_input,
    maybe_wait_for_children,
    park_waiting_for_pending_question,
    park_waiting_for_text_only_response,
)
from app.task_states import TaskStatus, assert_transition

logger = logging.getLogger(__name__)


async def finalize_terminal_state(
    task_id: str, my_session, *, is_followup: bool = False
):
    """Shared terminal-state writer.

    Called by both `auto_run_task` (post-wait section) and
    `finalize_followup_session`. Handles:
    - Plan-mode exit during DESIGNING without a plan → COMPLETED (skip-
      plan success) or FAILED (design failure)
    - Plan saved, ownership handed to `execute_planned_task` → return
    - Wait-condition → WAITING
    - Resolved outcome (see app.task_outcome) → COMPLETED / FAILED
    - CLI `_is_error_result` → FAILED
    - Incomplete todos without result → WAITING (set_task_error-free)
    - Children-waiting guard

    ``task.result`` is materialised up front by
    :func:`app.task_outcome.resolve_outcome`, which picks the best
    available deliverable — accepted result, else a retained-but-refused
    submission with its gaps, else the streamed prose tail. Every branch
    below that reads ``task.result`` therefore sees the same value the
    user will, and the terminal status comes from ``outcome.status``
    rather than from a bare ``if task.error:``.
    """
    async with async_session() as db:
        task = await db.get(Task, task_id)
        if agent_manager.get_session(task_id) is not my_session:
            return
        if not task or task.status not in {
            TaskStatus.RUNNING,
            TaskStatus.DESIGNING,
        }:
            return

        # Resolve ONCE, before any branch reads result/error. This is
        # the single place that decides what the run produced.
        outcome = resolve_outcome(task)
        apply_outcome(task, outcome)

        # Plan mode only: agent exited during planning without a plan.
        # Auto-mode tasks are RUNNING (not DESIGNING) so this block is
        # skipped for them.
        if (
            task.plan_mode
            and task.status == TaskStatus.DESIGNING
            and not task.plan
        ):
            session = agent_manager.get_session(task_id)
            is_error = session and getattr(session, '_is_error_result', False)
            # Plan-only tasks MUST produce a plan (the whole task
            # exists to author one). No skip-plan success path —
            # if the agent didn't call ExitPlanMode, the Schedule
            # never gets the plan committed and would silently stay
            # stuck at plan_status='planning'. Force-fail with a
            # specific error so the reaper + UI can surface it.
            # Guard MUST come before the `task.result and not is_error`
            # branch — a plan-only agent that returned a chat result
            # but skipped ExitPlanMode would otherwise be treated as
            # a success.
            if task.is_plan_only:
                assert_transition(task.status, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                if not task.error:
                    task.error = (
                        'Planning task ended without calling '
                        'ExitPlanMode. The plan was never saved to '
                        "the schedule, so it can't fire. Re-plan to "
                        'try again.'
                    )
                    if task.result:
                        task.error += f'\n\nAgent message: {task.result}'
                task.error_category = task.error_category or 'plan_missing'
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
                await event_bus.emit(
                    'task_update',
                    {
                        'task_id': task_id,
                        'status': TaskStatus.FAILED,
                        'error': task.error,
                    },
                )
                return

            # If the agent has a result and no error it skipped
            # planning and executed directly → treat as success.
            # (Normal plan-mode tasks only — the is_plan_only branch
            # above already returned.)
            if task.result and not is_error:
                if await maybe_wait_for_children(task, task_id, db):
                    return
                assert_transition(task.status, TaskStatus.COMPLETED)
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC).isoformat()
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
                await event_bus.emit(
                    'task_update',
                    {'task_id': task_id, 'status': TaskStatus.COMPLETED},
                )
                return
            # Otherwise → fail.  Preserve any error annotated by the
            # agent via set_task_error (task.error already set); only
            # synthesize "Design phase did not produce a plan" when
            # the agent didn't provide its own error.
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            cat = getattr(session, '_error_category', None) if session else None
            if not task.error:
                error_detail = 'Design phase did not produce a plan'
                if task.result:
                    error_detail += f'\n\n{task.result}'
                task.error = error_detail
            task.error_category = task.error_category or cat or 'design_failed'
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': task.error,
                },
            )
            return

        # Plan-mode follow-up: agent re-entered DESIGNING via follow-up
        # message, already has a plan from a prior phase, and wrote a
        # result without calling ExitPlanMode (e.g. Phase 2 execution
        # in a multi-turn plan-mode task).
        if task.plan_mode and task.plan and task.status == TaskStatus.DESIGNING:
            session = agent_manager.get_session(task_id)
            is_error = session and getattr(session, '_is_error_result', False)
            if task.result and not is_error:
                if await maybe_wait_for_children(task, task_id, db):
                    return
                assert_transition(task.status, TaskStatus.COMPLETED)
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC).isoformat()
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
                await event_bus.emit(
                    'task_update',
                    {
                        'task_id': task_id,
                        'status': TaskStatus.COMPLETED,
                    },
                )
                return
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            cat = getattr(session, '_error_category', None) if session else None
            if not task.error:
                error_detail = (
                    'Agent exited during follow-up without producing a result'
                )
                if task.result:
                    error_detail += f'\n\n{task.result}'
                task.error = error_detail
            task.error_category = (
                task.error_category or cat or 'followup_failed'
            )
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': task.error,
                },
            )
            return

        # Interactive plan mode with a saved plan: normally
        # `execute_planned_task` owns the terminal transition from here
        # (possibly already RUNNING after a concurrent /execute-plan),
        # so auto_run_task must NOT fall through. But a /messages
        # follow-up has no execute_planned_task — this finalize IS the
        # owner — so it must fall through and complete the task.
        if (
            not is_followup
            and task.plan_mode
            and not task.schedule_id
            and task.plan
            and task.status in (TaskStatus.PLANNED, TaskStatus.RUNNING)
        ):
            return

        # The remaining transitions (wait-condition / error / COMPLETED)
        # only apply to tasks in RUNNING — DESIGNING was fully handled
        # above.
        if task.status != TaskStatus.RUNNING:
            return

        if not task.wait_condition and task.result:
            parsed = parse_wait_condition(task.result)
            if parsed:
                task.wait_condition = json.dumps(parsed)
                logger.info(
                    'Fallback wait-condition parsed for %s',
                    task_id,
                )

        if task.wait_condition:
            condition = json.loads(task.wait_condition)
            now = datetime.now(UTC).isoformat()
            condition['waiting_since'] = now
            condition['last_checked_at'] = now
            interval = condition.get('check_interval_hours', 24)
            condition['next_check_at'] = (
                datetime.now(UTC) + timedelta(hours=interval)
            ).isoformat()
            task.wait_condition = json.dumps(condition)
            assert_transition(task.status, TaskStatus.WAITING)
            task.status = TaskStatus.WAITING
            task.updated_at = now
            await db.commit()
            await event_bus.emit(
                'task_update',
                {'task_id': task_id, 'status': TaskStatus.WAITING},
            )
            return

        # Terminal verdict from the resolved outcome. A recorded error
        # no longer fails a run on its own: an INCOMPLETE outcome — a
        # refused-but-retained submission, or an agent explaining
        # caveats over real output — completes with those caveats
        # attached. Only FAILED (no deliverable at all, or an
        # infra-detected error) lands in FAILED.
        if outcome.kind is OutcomeKind.FAILED and task.error:
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': task.error,
                },
            )
            return

        session = agent_manager.get_session(task_id)
        if session and getattr(session, '_is_error_result', False):
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            cli_error = getattr(session, '_last_result_event', '')
            task.error = cli_error or task.result or 'Agent exited with error'
            task.error_category = getattr(session, '_error_category', None)
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': task.error,
                },
            )
            return

        if has_incomplete_todos(task) and not (
            task.result and task.result.strip()
        ):
            await mark_waiting_for_input(task, db, task_id)
            return

        agent_ok = session and getattr(session, '_agent_success', False)
        if (
            not task.result
            and not task.wait_condition
            and task.status != TaskStatus.FAILED
            and not agent_ok
        ):
            # The subprocess exited without persisting a result.
            # Look for two shapes that should park in WAITING instead
            # of failing:
            #   (a) AskUserQuestion still outstanding — the operator
            #       answers via the UI and the task resumes via
            #       `claude --resume`.
            #   (b) Agent wrote text-only output (no tool_use) — likely
            #       a prose question; same operator-answer path.
            # Note: we intentionally keep this nested under the empty-
            # result gate. When the stream backend persists prose into
            # task.result (the common success path), `_save_result`
            # already ran and `agent_ok` is True — those tasks are
            # legitimate completions even if the prose looks question-
            # like (e.g. "Reply with OK" → "OK", "Capital?" → "Paris").
            # See PR #151 review + e2e regression for context.
            pending = getattr(session, '_last_pending_questions', {}) or {}
            if pending:
                await park_waiting_for_pending_question(
                    task, db, task_id, pending
                )
                return
            had_tool_use = getattr(session, '_had_tool_use', False)
            text_parts = getattr(session, '_exec_phase_text_parts', [])
            has_text = any(p.strip() for p in text_parts)
            if not had_tool_use and has_text:
                logger.info(
                    'Agent %s exited with no tool_use blocks — '
                    'parking in WAITING (text-only response)',
                    task_id[:8],
                )
                await park_waiting_for_text_only_response(task, db, task_id)
                return
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            task.error = 'Agent exited without producing a result'
            task.error_category = 'agent_empty_result'
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': task.error,
                },
            )
            return

        if should_park_qa_followup(session, task.result):
            await park_waiting_for_text_only_response(task, db, task_id)
            return
        if await maybe_wait_for_children(task, task_id, db):
            return

        assert_transition(task.status, TaskStatus.COMPLETED)
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now(UTC).isoformat()
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': TaskStatus.COMPLETED},
        )
