"""Task chat/conversation endpoints: messages, questions, retry."""

import asyncio
from datetime import UTC, datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.claude_backend_manager import agent_manager
from app.ai.compaction import build_history_prompt, dump_history_file
from app.ai.external_config import (
    ExternalConfigOverrideError,
    assert_profile_compatible,
)
from app.ai.profiles import DEFAULT_PROFILE_ID
from app.auth import get_current_user
from app.browser.manager import browser_manager, store_slug as _store_slug
from app.database import async_session, get_db
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
from app.task_runner_auto import auto_run_task, finalize_followup_session
from app.task_runner_exec import execute_planned_task
from app.task_states import (
    RETRIABLE,
    WAKEABLE,
    TaskStatus,
    assert_transition,
)
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)

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


async def _spawn_followup_agent(
    task_id: str,
    store: Store | None,
    *,
    prompt: str,
    system_extra: str,
    mode: str,
    profile_id: str,
    auto_approve_plan: bool,
    revert_status: TaskStatus,
    revert_error: str | None = None,
    revert_error_category: str | None = None,
):
    """Run agent_manager.run() off the request path.

    The HTTP handler can't await the run because acquiring the
    agent-concurrency semaphore can take seconds-to-minutes under
    load (catalog-sync fanout, parallel e2e workers). Awaiting it
    inline blocks the response past the client's read timeout.

    On success, schedules ``finalize_followup_session`` so the
    session still transitions out of RUNNING/DESIGNING when it
    ends. On failure (started=False or exception), reverts the
    task back to its prior terminal state via a fresh DB session
    so the UI doesn't get stuck mid-transition.
    """
    started = False
    try:
        # cc-switch / external override may have appeared since the
        # initial task ran — re-check before each follow-up spawn so
        # the agent never silently routes to whatever endpoint the
        # external tool configured. Treat as a normal launch failure
        # so the existing revert path runs (task back to its prior
        # terminal status, error surfaced on the task card).
        try:
            assert_profile_compatible(profile_id)
        except ExternalConfigOverrideError as override_err:
            # Expected, user-actionable condition — don't ``raise``
            # into the outer ``except Exception`` (it would log a
            # full stack trace via ``logger.exception``). Instead
            # update the revert vars in-place and skip past the
            # agent_manager.run call so the existing revert path
            # below runs with the structured detail.
            #
            # JSON-encode so the frontend's
            # ``ExternalConfigOverrideErrorCard`` renders the
            # localized template (same shape as auto_run_task).
            revert_error = json.dumps(override_err.to_api_detail())
            revert_error_category = 'external_config_override'
            logger.info(
                'Follow-up for task %s blocked by external config '
                'override (%s); will revert.',
                task_id,
                override_err.overriding_keys,
            )
            started = False
        else:
            started = await agent_manager.run(
                task_id,
                prompt,
                system_extra=system_extra,
                mode=mode,
                profile_id=profile_id,
                resume=True,
                auto_approve_plan=auto_approve_plan,
                store_slug=(
                    _store_slug(store.name, store.id) if store else None
                ),
                # Follow-up turns are conversational — reflection is
                # for initial task knowledge capture. Re-running it
                # on every follow-up forces a text-emitting
                # reflection phase that overwrites the agent's
                # actual response when the model put its answer
                # only in a thinking block (e.g. GLM-4.7 on terse
                # follow-ups). The initial task's reflection already
                # captured any learnings; conversational turns have
                # nothing new to learn from.
                skip_reflection=True,
            )
    except Exception:
        logger.exception(
            'Background follow-up agent run failed for task %s', task_id
        )
    if started:
        asyncio.create_task(finalize_followup_session(task_id, store))
        return
    # Revert to prior terminal state — the request handler already
    # committed RUNNING/DESIGNING and emitted task_update; we need
    # to roll that back so the UI doesn't sit on a phantom phase.
    async with async_session() as db2:
        task_obj = await db2.get(Task, task_id)
        if task_obj is None:
            return
        task_obj.status = revert_status.value
        if revert_error is not None or revert_error_category is not None:
            task_obj.error = revert_error
            task_obj.error_category = revert_error_category
        task_obj.updated_at = datetime.now(UTC).isoformat()
        await db2.commit()
    await event_bus.emit(
        'task_update',
        {'task_id': task_id, 'status': revert_status.value},
    )


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
            await agent_manager.send_message(task_id, content)
            return {
                'ok': True,
                'task_id': task_id,
                'profile_id': requested_profile_id,
                'profile_switched': False,
            }

    # Determine if we can resume a CLI session (has full context)
    is_plan_feedback = task.status == TaskStatus.PLANNED
    has_resumable_session = bool(task.session_id) and not is_profile_switch

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
        if task.plan_mode:
            # Plan mode follow-up
            assert_transition(task.status, TaskStatus.DESIGNING)
            task.status = TaskStatus.DESIGNING
            follow_up_mode = 'plan_then_execute'
        else:
            # Auto mode follow-up: clear stale run-scoped state
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
        # For plan-mode tasks, follow-ups during the DESIGN phase
        # (including PENDING/QUEUED before the agent even started,
        # and DESIGNING after a crash/interrupt) must continue in
        # plan_then_execute so the resumed session stays in plan
        # mode — otherwise the agent gets ``--permission-mode
        # bypassPermissions`` and ExitPlanMode returns "You are
        # not in plan mode". Only PLANNED / RUNNING are past the
        # plan phase and legitimately want ``execute`` mode.
        # (PLANNED follow-ups actually route through the
        # is_plan_feedback path above, so they don't land here.)
        if not task.plan_mode:
            follow_up_mode = 'auto'
        elif task.status in (
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
            TaskStatus.DESIGNING,
        ):
            follow_up_mode = 'plan_then_execute'
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
    # Wipe per-task workspace for a clean start (best-effort)
    try:
        await workspace_manager.prepare_task_workspace(
            task_id,
            store_id=task.store_id,
            clean=True,
        )
    except Exception:
        logger.exception(
            'Failed to clean workspace for task %s on retry',
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
