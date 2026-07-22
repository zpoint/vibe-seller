"""Task chat/conversation endpoints: messages, questions, retry."""

import asyncio
from datetime import UTC, datetime
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.claude_backend_manager import agent_manager
from app.ai.compaction import build_history_prompt, dump_history_file
from app.ai.profiles import DEFAULT_PROFILE_ID
from app.auth import get_current_user
from app.browser.manager import browser_manager
from app.database import get_db
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.models.task_log import TaskLog
from app.models.task_message import TaskMessage
from app.models.task_step import TaskStep
from app.models.user import User
from app.plan_states import PlanStatus
from app.routers.dida365_oauth import refresh_token_if_needed
from app.routers.tasks import schedule_or_run
from app.scheduler.task_queue import task_queue_scheduler
from app.task_runner import (
    TaskHeader,
    build_system_extra,
    get_store_emails,
    reopen_parent_if_child_active,
)
from app.task_runner_auto import auto_run_task
from app.task_runner_exec import execute_planned_task
from app.task_runner_followup import (
    spawn_followup_agent as _spawn_followup_agent,
)
from app.task_states import (
    RETRIABLE,
    WAKEABLE,
    TaskStatus,
    assert_transition,
)
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)

# Transcript size beyond which a follow-up starts a FRESH CLI session
# (compacted context) instead of --resume. See the context-rot guard
# in the message handler.
FRESH_SESSION_MSG_LIMIT = int(
    os.environ.get('VIBE_FRESH_SESSION_MSG_LIMIT', '400')
)

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


def _follow_up_auto_approve(task: Task) -> bool:
    """Mirror of the ``auto_approve_plan`` gate from
    ``task_runner_auto.py``.

    Follow-up flows bypass ``auto_run_task`` and call
    ``agent_manager.run()`` directly, so they must compute the same
    gate locally. Without this, a plan-mode task with
    ``schedule_id`` set (e.g. a plan-only planner whose first session
    crashed and the user is nudging via follow-up) would leave
    ``auto_approve_plan=False``, the agent would save its plan and
    then wait forever for an approval that never arrives.
    """
    return bool(task.schedule_id) or not task.plan_mode


async def get_next_seq(db: AsyncSession, task_id: str) -> int:
    """Get the next sequence number for a task's messages."""
    result = await db.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.seq.desc())
        .limit(1)
    )
    last_msg = result.scalar_one_or_none()
    return (last_msg.seq + 1) if last_msg else 0


@router.post('/{task_id}/messages')
async def send_task_message(
    task_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send a chat message to a running agent with profile switching support.

    If profile_id differs from the task's current profile, the agent is
    restarted with the new profile environment and full chat history.
    """
    content = body.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail='content is required')

    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    requested_profile_id = body.get(
        'profile_id', task.ai_profile_id or DEFAULT_PROFILE_ID
    )
    current_profile_id = task.ai_profile_id or DEFAULT_PROFILE_ID
    is_profile_switch = requested_profile_id != current_profile_id

    # Save the user message
    seq = await get_next_seq(db, task_id)
    user_message = TaskMessage(
        task_id=task_id,
        role='user',
        content=content,
        seq=seq,
        profile_id=requested_profile_id if is_profile_switch else None,
    )
    db.add(user_message)

    # If profile changed, update task's attached profile
    # and clear session_id (new profile = fresh session)
    if is_profile_switch:
        task.ai_profile_id = requested_profile_id
        task.session_id = None
        task.updated_at = datetime.now(UTC).isoformat()

    await db.commit()

    # Emit user message to SSE so other clients see it
    await event_bus.emit(
        'task_message',
        {
            'task_id': task_id,
            'role': 'user',
            'content': content,
        },
    )

    # Handle waiting tasks: treat message as wake action
    if task.status in WAKEABLE:
        condition = json.loads(task.wait_condition or '{}')
        condition['woken_at'] = datetime.now(UTC).isoformat()
        condition['woken_by'] = 'user'
        condition['trigger_data'] = content
        task.wait_condition = json.dumps(condition)
        task.status = TaskStatus.QUEUED
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()

        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': TaskStatus.QUEUED},
        )

        if task.store_id:
            await task_queue_scheduler.submit(task_id, task.store_id)

        return {
            'ok': True,
            'task_id': task_id,
            'profile_id': requested_profile_id,
            'profile_switched': is_profile_switch,
            'woken': True,
        }

    # Check if agent is running and handle profile switch or send message
    if agent_manager.is_running(task_id):
        if is_profile_switch:
            # Stop current agent - new one will start with new profile
            await agent_manager.stop(task_id)
        elif task.status != TaskStatus.PLANNED:
            # Agent running with same profile - just send message.
            # Skip when PLANNED: agent is blocked on plan approval
            # hook and can't process stdin; must use reject_plan.
            # Delivery is confirmed: a message racing the turn
            # terminator (stdin just closed) returns False and falls
            # through to the resume/fresh-session spawn below instead
            # of being silently dropped into a dying pipe.
            if await agent_manager.send_message(task_id, content):
                # The task-level verdict is TURN-SCOPED: a delivered
                # follow-up opens a new turn, so the prior turn's
                # result/error must not survive it (the finished-task
                # spawn path below clears the same fields). Without
                # this, a turn-1 ``set_task_result``/``set_task_error``
                # sticks: ``_save_result``'s preserve-existing rule
                # then refuses the new turn's streamed result and the
                # UI shows the stale verdict next to the new turn's
                # answer. Gate redrives do NOT pass through here, so
                # a converging turn keeps its accepted result.
                task.result = None
                task.error = None
                task.error_category = None
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
                await event_bus.emit(
                    'task_update',
                    {
                        'task_id': task_id,
                        'status': task.status,
                        'error': None,
                    },
                )
                return {
                    'ok': True,
                    'task_id': task_id,
                    'profile_id': requested_profile_id,
                    'profile_switched': False,
                }

    # Determine if we can resume a CLI session (has full context)
    is_plan_feedback = task.status == TaskStatus.PLANNED
    has_resumable_session = bool(task.session_id) and not is_profile_switch
    # Context-rot guard: past a certain transcript size, a resumed
    # session carries more failed-attempt residue than signal — stale
    # file paths, superseded conclusions, and old batch state dominate
    # the context and the agent re-verifies dead artifacts (observed
    # live: a reviewer "verified" a prior turn's report because its
    # path was still in context). Beyond the threshold, start a FRESH
    # CLI session seeded with the compact history summary the
    # non-resumable branch below already builds (history file + recent
    # messages + plan). The task workspace (files on disk) is unchanged.
    if has_resumable_session:
        n_msgs = await db.scalar(
            select(func.count())
            .select_from(TaskMessage)
            .where(TaskMessage.task_id == task_id)
        )
        if (n_msgs or 0) > FRESH_SESSION_MSG_LIMIT:
            has_resumable_session = False
            logger.info(
                'Task %s transcript has %d messages (> %d) — starting '
                'a fresh session with compacted context instead of '
                'resuming',
                task_id,
                n_msgs,
                FRESH_SESSION_MSG_LIMIT,
            )

    # Plan approval is a ONE-WAY exit from plan mode (like Claude Code:
    # an approved ExitPlanMode leaves the session in normal mode for
    # good). ``started_at`` is stamped at approval and only reset by a
    # full task retry (which also clears the plan and resets status to
    # PENDING), so a task is still "planning" only if it hasn't started
    # executing AND is in a pre-execution status. Past that a follow-up
    # behaves like one on a non-plan task — resume and keep executing,
    # never re-plan. (Bug: approved plan tasks sit in DESIGNING/COMPLETED
    # between turns and used to re-plan every turn.) ``is_plan_only``
    # schedule tasks only ever plan; PLANNED plan-feedback routes above.
    # FAILED is included ONLY together with ``started_at is None``: a
    # plan-mode task that failed BEFORE any plan was approved died in
    # the planning phase, so a follow-up must resume PLANNING — without
    # this it fell through to auto mode and executed the review-first
    # task unreviewed under bypassPermissions. A COMPLETED task with no
    # ``started_at`` (the plan-skip path: agent delivered a result
    # without ExitPlanMode) stays auto — its work is already done and
    # accepted as-is by the plan-skip contract.
    plan_phase_active = task.plan_mode and (
        task.is_plan_only
        or (
            task.started_at is None
            and task.status
            in (
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
                TaskStatus.DESIGNING,
                TaskStatus.FAILED,
            )
        )
    )

    # When resuming, skip conversation reconstruction — the CLI
    # session already has all prior context. Only inject mode
    # switch instructions and the plan feedback instruction.
    conversation_context = ''
    if has_resumable_session:
        if is_plan_feedback:
            conversation_context = (
                '\n\nIMPORTANT: Your final output MUST be the '
                'COMPLETE revised plan — not just the changes '
                'or a summary. Include all original sections '
                '(updated as needed) so the plan can fully '
                'replace the previous version. '
                'If the user asks questions, use '
                'AskUserQuestion to ask them interactively, '
                'then revise the plan after receiving answers.'
            )
    else:
        # No resumable session — build full context from DB
        result = await db.execute(
            select(TaskMessage)
            .where(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.seq)
        )
        messages = result.scalars().all()

        # Always dump history file so the agent can read
        # prior conversation on demand (even when a plan
        # is shown inline).
        prior_msgs = [
            m
            for m in messages
            if m.role in ('user', 'assistant') and m.id != user_message.id
        ]
        history_file = None
        if prior_msgs:
            history_dicts = [
                {
                    'role': m.role,
                    'content': m.content,
                    'seq': m.seq,
                }
                for m in prior_msgs
            ]
            history_file = dump_history_file(task_id, history_dicts)

        if is_plan_feedback and task.plan:
            conversation_context = (
                '\n\n## Current Plan (draft, not yet confirmed)\n'
                'The following plan was designed in a previous '
                'session. The user is providing feedback to '
                'revise or extend it.\n\n'
                'IMPORTANT: Your final output MUST be the '
                'COMPLETE revised plan — not just the changes '
                'or a summary. Include all original sections '
                '(updated as needed) so the plan can fully '
                'replace the previous version. '
                'If the user asks questions, use '
                'AskUserQuestion to ask them interactively, '
                'then revise the plan after receiving '
                'answers.\n\n' + task.plan
            )
        elif task.plan:
            conversation_context = '\n\n## Task Plan\n' + task.plan
        else:
            if prior_msgs and history_file:
                conversation_context = (
                    '\n\n## Prior Conversation\n'
                    + build_history_prompt(history_dicts, history_file)
                )

        # Add history file reference to system prompt when
        # a plan was shown inline (agent can read full
        # conversation from the file if needed).
        if history_file and task.plan:
            conversation_context += (
                f'\n\nNote: Full conversation history '
                f'({len(prior_msgs)} messages) is saved '
                f'at {history_file}. Read it if you need '
                f'context from prior discussion.'
            )

    # Build full system prompt with all context
    store = await db.get(Store, task.store_id) if task.store_id else None
    store_emails: list[str] = []
    if store:
        store_emails = await get_store_emails(db, store.id)

    bundle = await build_system_extra(
        task,
        store,
        header=TaskHeader.CHAT,
        store_emails=store_emails,
        extra_context=conversation_context,
    )
    system_extra = bundle.system_extra
    if is_profile_switch:
        system_extra += f'\n\n[Profile switched to: {requested_profile_id}]'

    if is_plan_feedback:
        # Transition planned → designing so UI updates
        assert_transition(task.status, TaskStatus.DESIGNING)
        task.status = TaskStatus.DESIGNING
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
        await event_bus.emit(
            'task_update',
            {
                'task_id': task_id,
                'status': TaskStatus.DESIGNING,
            },
        )

        # Try rejecting plan in running session (native mode)
        rejected = await agent_manager.reject_plan(
            task_id,
            feedback=content,
        )
        if rejected:
            return {
                'ok': True,
                'task_id': task_id,
                'profile_id': requested_profile_id,
                'profile_switched': is_profile_switch,
            }

        # Session died — start fresh plan_then_execute. Spawn off
        # the request path so a saturated agent semaphore doesn't
        # block this POST past the client read timeout.
        await refresh_token_if_needed()
        asyncio.create_task(
            _spawn_followup_agent(
                task_id,
                store,
                prompt=content,
                system_extra=system_extra,
                mode='plan_then_execute',
                profile_id=requested_profile_id,
                auto_approve_plan=_follow_up_auto_approve(task),
                resume=has_resumable_session,
                revert_status=TaskStatus.PLANNED,
            )
        )
        return {
            'ok': True,
            'task_id': task_id,
            'profile_id': requested_profile_id,
            'profile_switched': is_profile_switch,
        }
    elif task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        # Follow-up on a finished task: clear stale data and
        # restart.  Auto mode → RUNNING; plan mode → DESIGNING.
        prev_status = task.status
        prev_error = task.error
        prev_error_category = task.error_category
        if task.error:
            task.error = None
            task.error_category = None
        if plan_phase_active:
            # Plan-mode task whose plan was never approved/executed —
            # a follow-up on it is still plan feedback: keep planning.
            assert_transition(task.status, TaskStatus.DESIGNING)
            task.status = TaskStatus.DESIGNING
            follow_up_mode = 'plan_then_execute'
        else:
            # Non-plan task, OR a plan-mode task whose plan was already
            # approved and executed. A follow-up continues execution
            # with full context, identical to a non-plan task — no
            # re-plan. Clear stale run-scoped state and resume.
            task.result = None
            task.todos = None
            task.wait_condition = None
            assert_transition(task.status, TaskStatus.RUNNING)
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(UTC).isoformat()
            follow_up_mode = 'auto'
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': task.status},
        )
        # If this child was resumed, revert parent to WAITING
        await reopen_parent_if_child_active(task, db)
        # Regenerate browser wrapper with fresh auth token so
        # the agent can auto-start the browser even if the
        # original token expired (e.g. follow-up next day).
        if store:
            await browser_manager.write_browser_config_for_store(store, db)
        await refresh_token_if_needed()
        # Spawn off the request path — see _spawn_followup_agent
        # docstring. Errors and started=False both fall through to
        # the same revert logic in the helper.
        asyncio.create_task(
            _spawn_followup_agent(
                task_id,
                store,
                prompt=content,
                system_extra=system_extra,
                mode=follow_up_mode,
                profile_id=requested_profile_id,
                auto_approve_plan=_follow_up_auto_approve(task),
                resume=has_resumable_session,
                revert_status=TaskStatus(prev_status),
                revert_error=prev_error,
                revert_error_category=prev_error_category,
            )
        )
        return {
            'ok': True,
            'task_id': task_id,
            'profile_id': requested_profile_id,
            'profile_switched': is_profile_switch,
        }
    else:
        await refresh_token_if_needed()
        # Mode selection for follow-ups on tasks NOT in WAITING /
        # COMPLETED / FAILED. The remaining states are PENDING /
        # QUEUED / DESIGNING / PLANNED / RUNNING.
        #
        # Still in the plan phase (``plan_phase_active``) → keep
        # plan_then_execute so the resumed session stays in plan mode
        # (otherwise ExitPlanMode returns "You are not in plan mode").
        # Covers PENDING/QUEUED before the agent starts, DESIGNING after
        # a crash/interrupt, and is_plan_only tasks.
        #
        # Non-plan task → auto. Plan-mode task past its plan phase
        # (approved: RUNNING, or DESIGNING with started_at set) →
        # execute: bypassPermissions, continues with context, no
        # re-plan — exactly like a non-plan follow-up.
        if plan_phase_active:
            follow_up_mode = 'plan_then_execute'
        elif not task.plan_mode:
            follow_up_mode = 'auto'
        else:
            follow_up_mode = 'execute'
        # Spawn off the request path so the POST returns before
        # the agent semaphore wait. Tasks already in non-terminal
        # states have nothing to revert to on failure — leave
        # status as-is and rely on the next user signal.
        asyncio.create_task(
            _spawn_followup_agent(
                task_id,
                store,
                prompt=content,
                system_extra=system_extra,
                mode=follow_up_mode,
                profile_id=requested_profile_id,
                auto_approve_plan=_follow_up_auto_approve(task),
                resume=has_resumable_session,
                revert_status=TaskStatus(task.status),
            )
        )

    return {
        'ok': True,
        'task_id': task_id,
        'profile_id': requested_profile_id,
        'profile_switched': is_profile_switch,
    }


@router.get('/{task_id}/questions/pending')
async def get_pending_questions(
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """Get pending question for a task (if agent is waiting)."""
    data = agent_manager.get_pending_questions(task_id)
    if not data:
        return {'pending': False}
    return {'pending': True, **data}


@router.post('/{task_id}/questions/answer')
async def answer_question(
    task_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Submit an answer for a pending AskUserQuestion from the agent.

    Two paths:
    1. Live session — forward answers over IPC so the SDK returns a
       `control_response` to the agent and it continues the turn.
    2. Dead session, task in WAITING with a matching ``pending_question``
       — persist answers into ``wait_condition.answers``, flip the
       task to QUEUED, and re-queue. The task runner will then
       spawn a fresh session with ``claude --resume <session_id>`` and
       deliver the answers as the next user turn.
    """
    request_id = body.get('request_id', '')
    answers = body.get('answers', {})
    if not request_id:
        raise HTTPException(status_code=400, detail='request_id is required')

    # Path 1: live session
    submitted = await agent_manager.submit_answer(task_id, request_id, answers)
    if submitted:
        return {'ok': True}

    # Path 2: dead session + WAITING task with matching pending question
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    try:
        cond = json.loads(task.wait_condition or '{}')
    except (json.JSONDecodeError, TypeError):
        cond = {}
    if not isinstance(cond, dict):
        cond = {}
    pq = cond.get('pending_question') or {}
    if task.status == TaskStatus.WAITING and pq.get('request_id') == request_id:
        now = datetime.now(UTC).isoformat()
        cond['answers'] = answers
        cond['answered_at'] = now
        task.wait_condition = json.dumps(cond)
        task.status = TaskStatus.QUEUED
        task.updated_at = now
        await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': TaskStatus.QUEUED},
        )
        # Dispatch: store tasks go through the per-store queue
        # (browser-concurrency gate); no-store tasks launch directly
        # since there's no browser to serialise on. Mirrors the shape
        # of `routers.tasks.schedule_or_run`.
        if task.store_id:
            await task_queue_scheduler.submit(task_id, task.store_id)
        else:
            asyncio.create_task(auto_run_task(task_id, None))
        return {'ok': True, 'resumed': True}

    raise HTTPException(
        status_code=409, detail='No agent running or no pending question'
    )


class RetryRequest(BaseModel):
    profile_id: str | None = None


@router.post('/{task_id}/retry')
async def retry_task(
    task_id: str,
    body: RetryRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Retry a retriable task (FAILED / PENDING / COMPLETED).

    Default: clear plan + restart from PENDING (re-plan if plan mode).
    Scheduled plan-mode tasks re-seed from the owning Schedule's
    frozen plan (``plan_status='ready'``) and go straight to PLANNED
    → execute, matching the cron fire path.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in RETRIABLE:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot retry task in status {task.status}',
        )

    await db.execute(delete(TaskStep).where(TaskStep.task_id == task_id))
    await db.execute(
        delete(TaskMessage).where(
            TaskMessage.task_id == task_id,
        )
    )
    await db.execute(delete(TaskLog).where(TaskLog.task_id == task_id))

    store = None
    if task.store_id:
        store = await db.get(Store, task.store_id)

    if body and body.profile_id:
        task.ai_profile_id = body.profile_id

    task.status = TaskStatus.PENDING
    task.error = None
    task.error_category = None
    task.plan = None
    task.plan_history = None
    task.result = None
    task.todos = None
    task.wait_condition = None
    task.session_id = None
    # Clear run-scoped timestamps — without this, retrying a
    # COMPLETED task (RETRIABLE includes COMPLETED) leaves the
    # previous run's started_at + completed_at in the row during
    # the window between dispatch and the new run's first state
    # write, so the UI shows a misleading duration mid-retry.
    # auto_run_task overwrites started_at unconditionally on the
    # PENDING→RUNNING transition, but execute_planned_task's
    # session-resume branch uses ``task.started_at or now()``
    # which would preserve a stale value if we leave one here.
    task.started_at = None
    task.completed_at = None

    # For plan-mode scheduled tasks, re-seed the frozen schedule
    # plan so retry skips re-planning — mirrors the cron fire path
    # in app/scheduler/cron.py which sets status=PLANNED with the
    # saved plan when sched.plan_status=='ready'. Without this,
    # retry always wipes task.plan and kicks the agent back into
    # DESIGNING even though the schedule already has a frozen plan.
    #
    # Both sides must agree on plan_mode: the cron fire path sets
    # task.plan_mode=sched.plan_mode, so if they diverge the task was
    # created by a non-cron path (manual API call) and its recorded
    # mode wins — we don't flip an auto-mode task into planned
    # execution just because its schedule is plan-mode.
    if task.schedule_id and task.plan_mode:
        sched = await db.get(Schedule, task.schedule_id)
        if (
            sched
            and sched.plan_mode
            and sched.plan_status == PlanStatus.READY.value
            and sched.plan
        ):
            task.plan = sched.plan
            task.plan_version = sched.plan_version
            task.status = TaskStatus.PLANNED

    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await event_bus.emit(
        'task_update',
        {
            'task_id': task_id,
            'status': task.status,
        },
    )
    # If this child was retried, revert parent to WAITING
    await reopen_parent_if_child_active(task, db)
    # Retry is a DESTRUCTIVE fresh restart, so WIPE the per-task
    # workspace (best-effort). Leftover run data — above all the review
    # dumps under tasks/<id>/reviews/ — must not survive into the new
    # run: a prior run's files let the agent "resume" stale data or bless
    # them via a DoD-reviewer subagent instead of re-collecting, which is
    # exactly the freshness hole this closes. Incremental resume that
    # WANTS to keep banked progress (ad-audit reports/TSVs) goes through
    # Continue (POST /messages), which never wipes — see
    # test_wf_continue_vs_retry.
    try:
        await workspace_manager.prepare_task_workspace(task_id, clean=True)
    except Exception:
        logger.exception(
            'Failed to refresh workspace for task %s on retry',
            task_id,
        )
    # If plan was re-seeded from the schedule, skip planning: route
    # through execute_planned_task. For store tasks, _dispatch picks
    # the right handler from task.plan; for no-store tasks
    # schedule_or_run bypasses the queue and the launcher default
    # (auto_run_task) only accepts PENDING/QUEUED, so we must pass
    # it explicitly.
    launcher = (
        execute_planned_task if task.status == TaskStatus.PLANNED else None
    )
    await schedule_or_run(task.id, store, launcher=launcher)
    return {
        'ok': True,
        'task_id': task_id,
        'status': task.status,
        'profile_id': task.ai_profile_id,
    }


@router.get('/{task_id}/messages')
async def get_task_messages(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get chat history for a task."""
    result = await db.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task_id)
        .where(TaskMessage.role.notin_(['delta', 'thinking_delta']))
        .order_by(TaskMessage.seq)
    )
    messages = result.scalars().all()
    return [
        {
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'created_at': m.created_at,
        }
        for m in messages
    ]
