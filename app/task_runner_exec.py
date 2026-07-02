from datetime import UTC, datetime, timedelta
import json
import logging

from sqlalchemy import select

from app import telemetry
from app.ai.claude_backend_manager import agent_manager
from app.ai.claude_backend_utils import parse_wait_condition
from app.ai.external_config import (
    ExternalConfigOverrideError,
    assert_profile_compatible,
)
from app.ai.profiles import DEFAULT_PROFILE_ID, profile_kind_for_id
from app.browser.manager import browser_manager, store_slug as _store_slug
from app.database import async_session
from app.errors import categorize_ziniao_error
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.task_runner import (
    TaskHeader,
    build_system_extra,
    coalesce_history,
    format_trigger_context,
    get_store_emails,
    has_incomplete_todos,
    mark_waiting_for_input,
    maybe_wait_for_children,
)
from app.task_session_lifecycle import (
    _wait_for_session_end,
    wait_for_session_with_retry,
)
from app.task_states import (
    TaskStatus,
    assert_transition,
    can_transition,
)
from app.telemetry_events import TaskFailureCategory, TaskFailurePhase
from app.telemetry_tasks import send_task_completed, send_task_failed
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)


_send_task_completed = send_task_completed
_send_task_failed = send_task_failed


async def _fail_task_external_config_override(
    task_id: str, ext_err: ExternalConfigOverrideError
) -> None:
    # Persist as structured failure: JSON detail in task.error +
    # error_category='external_config_override' so the frontend's
    # ExternalConfigOverrideErrorCard renders the localized template.
    async with async_session() as db_fail:
        t = await db_fail.get(Task, task_id)
        if t and can_transition(t.status, TaskStatus.FAILED):
            t.status = TaskStatus.FAILED
            t.error = json.dumps(ext_err.to_api_detail())
            t.error_category = 'external_config_override'
            t.completed_at = datetime.now(UTC).isoformat()
            await db_fail.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.FAILED,
                    'error': t.error,
                },
            )


async def execute_planned_task(task_id: str, store: Store | None):
    """Background pipeline for executing a planned task.

    First tries to approve the pending plan in a running session
    (native plan mode). If the session died, starts a new execute
    session. Either way, the agent session is held during execution.
    """
    try:
        # Write browser config (needed for retry — server may have
        # restarted since the task was created; no-store tasks get the
        # store-less web wrapper with a fresh token so the execute phase
        # can browse even though planning ran in a separate session).
        async with async_session() as db_mcp:
            await browser_manager.write_task_browser_config(store, db_mcp)

        # Plan-only tasks (is_plan_only=True): the hook commits plan
        # + Task DESIGNING/PLANNED → COMPLETED + sends control deny.
        # We must NOT also force the task to RUNNING here — that
        # races the hook's status write and, when it wins, hides the
        # approved plan from `_commit_plan_only_approval`'s guard
        # (which expects PLANNED/DESIGNING/QUEUED). Load the flag
        # before approving so we branch correctly.
        async with async_session() as db_pre:
            pre_task = await db_pre.get(Task, task_id)
            is_plan_only = bool(pre_task and pre_task.is_plan_only)

        # Try to approve pending plan in running session first.
        # This handles the native plan mode case where the agent
        # is still alive waiting for plan approval.
        # Capture the session BEFORE approve so we await the same
        # session object that owns the plan (approve_plan just
        # unblocks the existing session, it doesn't spawn a new one).
        existing_session = agent_manager.get_session(task_id)
        approved = await agent_manager.approve_plan(task_id)
        if approved:
            if not is_plan_only:
                # Regular plan_then_execute: ensure status is RUNNING.
                # _approve_plan_request may skip the transition if the
                # task was still PLANNED or QUEUED when it ran.
                async with async_session() as db:
                    task = await db.get(Task, task_id)
                    if task and task.status in (
                        TaskStatus.PLANNED,
                        TaskStatus.QUEUED,
                    ):
                        assert_transition(
                            task.status,
                            TaskStatus.RUNNING,
                        )
                        task.status = TaskStatus.RUNNING
                        task.started_at = (
                            task.started_at or datetime.now(UTC).isoformat()
                        )
                        task.updated_at = datetime.now(UTC).isoformat()
                        await db.commit()
                        await event_bus.emit(
                            'task_update',
                            {
                                'task_id': task_id,
                                'status': TaskStatus.RUNNING,
                            },
                        )

            # Block on the session's own done event (event-driven,
            # shared with `auto_run_task`). Bail on supersession —
            # a retry that registered a fresh session under this
            # task_id owns the state machine now; the status-based
            # guard below only catches resets (PENDING / FAILED),
            # not the RUNNING-committed-by-retry window.
            if not await _wait_for_session_end(task_id, existing_session):
                return
            # Fall through to post-execution handling below
        else:
            # Session died — start a new execute session
            # Load prior message history in a separate session
            # to avoid StaticPool contention with the status
            # update below.
            history: list[dict] = []
            async with async_session() as db_hist:
                msgs = await db_hist.execute(
                    select(TaskMessage)
                    .where(TaskMessage.task_id == task_id)
                    .order_by(TaskMessage.seq)
                )
                history = coalesce_history(msgs.scalars().all())

            async with async_session() as db:
                task = await db.get(Task, task_id)
                if not task:
                    return
                # Plan mode requires a plan; auto mode continues
                # without one.
                if task.plan is None and task.plan_mode:
                    return

                # Ziniao pre-check before execution
                if store and store.browser_backend == 'ziniao':
                    try:
                        await browser_manager.check_ziniao_reachable(
                            store,
                            db,
                        )
                    except RuntimeError as exc:
                        assert_transition(
                            task.status,
                            TaskStatus.FAILED,
                        )
                        task.status = TaskStatus.FAILED
                        task.error = str(exc)
                        task.error_category = categorize_ziniao_error(str(exc))
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
                        _send_task_failed(
                            task,
                            phase=TaskFailurePhase.PRE_RUNNING,
                            category=TaskFailureCategory.ZINIAO_UNAVAILABLE,
                        )
                        return

                if task.status != TaskStatus.RUNNING:
                    assert_transition(task.status, TaskStatus.RUNNING)
                    task.status = TaskStatus.RUNNING
                    task.started_at = datetime.now(UTC).isoformat()
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': task_id,
                            'status': TaskStatus.RUNNING,
                        },
                    )

            bundle = await build_system_extra(
                task,
                store,
                header=TaskHeader.EXECUTE,
            )
            # Re-check the override before each execute-phase spawn —
            # cc-switch may have written settings.json since the
            # initial plan.
            try:
                assert_profile_compatible(
                    task.ai_profile_id or DEFAULT_PROFILE_ID
                )
            except ExternalConfigOverrideError as ext_err:
                await _fail_task_external_config_override(task_id, ext_err)
                return
            await agent_manager.run(
                task_id,
                bundle.prompt,
                system_extra=bundle.system_extra,
                mode=bundle.mode,
                profile_id=(task.ai_profile_id or DEFAULT_PROFILE_ID),
                message_history=history,
                resume=True,
                store_slug=(
                    _store_slug(store.name, store.id) if store else None
                ),
                skip_reflection=task.skip_reflection,
            )

            # Event-driven wait on the freshly-registered session.
            # Bail on supersession for the same reason as the
            # approved-branch wait above. The helper transparently
            # retries-without-resume if Claude Code rejected the
            # `--resume` session_id (we passed resume=True above).
            if (
                await wait_for_session_with_retry(
                    task_id, agent_manager.get_session(task_id)
                )
                is None
            ):
                return

        # Guard: bail out if task left our expected state.
        # Catches user-initiated stops (status flipped to FAILED
        # directly) AND retries that reset to PENDING/QUEUED while
        # we were in the wait loop. Agent-initiated errors via
        # set_task_error leave status RUNNING so we fall through
        # to the cleanup pipeline (§5b, §5c, §7) which transitions
        # on the task.error field.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in {
                TaskStatus.RUNNING,
                TaskStatus.DESIGNING,
            }:
                return

        # Guard: auto-mode agent exited without result → FAILED
        # (plan-mode may design without result, so only check auto mode)
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if (
                task
                and task.status == TaskStatus.RUNNING
                and not task.plan_mode
                and not task.result
            ):
                task.status = TaskStatus.FAILED
                task.error = 'Agent exited without producing a result'
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
                _send_task_failed(
                    task,
                    phase=TaskFailurePhase.RUNNING,
                    category=TaskFailureCategory.NO_RESULT,
                )
                return

        # Commit any knowledge changes from the agent
        try:
            await workspace_manager._auto_commit(
                f'Knowledge update after task: {task.title}'
            )
        except Exception as e:
            logger.warning(
                'Knowledge commit after task %s failed: %s',
                task_id,
                e,
            )

        # Check for wait condition or mark completed
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task and task.status == TaskStatus.RUNNING:
                # Fallback parse
                if not task.wait_condition and task.result:
                    parsed = parse_wait_condition(task.result)
                    if parsed:
                        task.wait_condition = json.dumps(parsed)

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
                        {
                            'task_id': task_id,
                            'status': TaskStatus.WAITING,
                        },
                    )
                    return

                # Agent annotated an unrecoverable error via the
                # set_task_error MCP tool → transition to FAILED
                # with the agent's message + category.
                if task.error:
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
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.RUNNING,
                        category=TaskFailureCategory.AGENT_SET_ERROR,
                    )
                    return

                # Check if agent exited with an error result (CLI
                # signaled is_error=true in its final result event,
                # distinct from the agent-annotated error above).
                session = agent_manager.get_session(task_id)
                if session and getattr(session, '_is_error_result', False):
                    assert_transition(task.status, TaskStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    # Prefer the CLI's error text (e.g. "API Error:
                    # 529 ...") over task.result which may hold an
                    # earlier successful output.  This ensures
                    # pytest --only-rerun filters can match the
                    # actual error.
                    cli_error = getattr(session, '_last_result_event', '')
                    task.error = (
                        cli_error or task.result or 'Agent exited with error'
                    )
                    task.error_category = getattr(
                        session, '_error_category', None
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
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.RUNNING,
                        category=TaskFailureCategory.CLI_ERROR,
                    )
                    return

                # Agent exited with incomplete todos → wait (only if no result)
                if has_incomplete_todos(task) and not (
                    task.result and task.result.strip()
                ):
                    await mark_waiting_for_input(
                        task,
                        db,
                        task_id,
                    )
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
                    {
                        'task_id': task_id,
                        'status': TaskStatus.COMPLETED,
                    },
                )
                await _send_task_completed(task)

    except Exception as e:
        logger.exception(
            'Execute-plan pipeline failed for task %s: %s',
            task_id,
            e,
        )
        try:
            async with async_session() as db:
                task = await db.get(Task, task_id)
                if task and can_transition(task.status, TaskStatus.FAILED):
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': task_id,
                            'status': TaskStatus.FAILED,
                            'error': str(e),
                        },
                    )
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.PIPELINE,
                        category=TaskFailureCategory.UNHANDLED_EXCEPTION,
                    )
        except Exception:
            logger.exception(
                'Failed to update task %s status after error',
                task_id,
            )
    finally:
        pass


async def execute_woken_task(task_id: str, store: Store | None):
    """Resume a woken waiting task with trigger context."""
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            condition = json.loads(task.wait_condition or '{}')
            trigger_data = condition.get('trigger_data')
            strategy = condition.get('check_strategy', 'manual')
            woken_by = condition.get('woken_by', strategy)

            wait_started = condition.get('waiting_since')
            wait_secs: float | None = None
            if wait_started:
                try:
                    wait_secs = (
                        datetime.now(UTC) - datetime.fromisoformat(wait_started)
                    ).total_seconds()
                except (ValueError, TypeError):
                    wait_secs = None
            telemetry.send(
                'task_resumed',
                {
                    'is_store_task': task.store_id is not None,
                    'wait_duration_bucket': telemetry.duration_bucket(
                        wait_secs
                    ),
                    'wake_reason': woken_by,
                    'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
                },
            )

            trigger_context = format_trigger_context(
                strategy, woken_by, trigger_data
            )

            prompt = (
                f'You were working on: {task.title}\n\n'
                f'You previously entered a waiting state because: '
                f'{condition.get("reason", "waiting for response")}'
                f'\n\n{trigger_context}\n\n'
                f'Please review the new information and continue '
                f'working on the task.'
            )
            if task.plan:
                prompt += f' Your previous plan was:\n{task.plan}\n\n'
            prompt += f'Your previous result/progress:\n{task.result or "N/A"}'

            # Load prior message history for continuity
            msgs = await db.execute(
                select(TaskMessage)
                .where(TaskMessage.task_id == task_id)
                .order_by(TaskMessage.seq)
            )
            history = coalesce_history(msgs.scalars().all())

            # Ziniao pre-check before execution
            if store and store.browser_backend == 'ziniao':
                try:
                    await browser_manager.check_ziniao_reachable(
                        store,
                        db,
                    )
                except RuntimeError as exc:
                    assert_transition(
                        task.status,
                        TaskStatus.FAILED,
                    )
                    task.status = TaskStatus.FAILED
                    task.error = str(exc)
                    task.error_category = categorize_ziniao_error(str(exc))
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
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.PRE_RUNNING,
                        category=TaskFailureCategory.ZINIAO_UNAVAILABLE,
                    )
                    return

            # Transition to running — preserve wait_condition
            assert_transition(task.status, TaskStatus.RUNNING)
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(UTC).isoformat()
            task.updated_at = datetime.now(UTC).isoformat()
            condition['resumed'] = True
            task.wait_condition = json.dumps(condition)
            await db.commit()
            await event_bus.emit(
                'task_update',
                {
                    'task_id': task_id,
                    'status': TaskStatus.RUNNING,
                },
            )

        # Build system prompt (MCP config only — no browser start)
        store_emails: list[str] = []
        async with async_session() as db:
            await browser_manager.write_task_browser_config(store, db)
            if store:
                store_emails = await get_store_emails(db, store.id)

        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.WOKEN,
            store_emails=store_emails,
        )
        # Same cc-switch / external-override re-check as the execute
        # phase above.
        try:
            assert_profile_compatible(task.ai_profile_id or DEFAULT_PROFILE_ID)
        except ExternalConfigOverrideError as ext_err:
            await _fail_task_external_config_override(task_id, ext_err)
            return
        await agent_manager.run(
            task_id,
            prompt,
            system_extra=bundle.system_extra,
            mode=bundle.mode,
            profile_id=(task.ai_profile_id or DEFAULT_PROFILE_ID),
            message_history=history,
            resume=True,
            store_slug=(_store_slug(store.name, store.id) if store else None),
            skip_reflection=task.skip_reflection,
        )

        # Event-driven wait on the resumed session.  Bail on
        # supersession — a retry that took over this task_id
        # while the waiter was parked owns the terminal transition.
        # `wait_for_session_with_retry` transparently retries when
        # Claude Code rejects the `--resume` session_id (the wake
        # path always uses resume=True; without retry, a stale
        # session_id falls straight through to FAILED).
        if (
            await wait_for_session_with_retry(
                task_id, agent_manager.get_session(task_id)
            )
            is None
        ):
            return

        # Guard: bail out if task left our expected state.
        # Catches user-initiated stops (status flipped to FAILED
        # directly) AND retries that reset to PENDING/QUEUED while
        # we were in the wait loop. Agent-initiated errors via
        # set_task_error leave status RUNNING so we fall through
        # to the cleanup pipeline (§5b, §5c, §7) which transitions
        # on the task.error field.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in {
                TaskStatus.RUNNING,
                TaskStatus.DESIGNING,
            }:
                return

        try:
            await workspace_manager._auto_commit(
                f'Knowledge update after task: {task.title}'
            )
        except Exception as e:
            logger.warning(
                'Knowledge commit after woken task %s failed: %s',
                task_id,
                e,
            )

        # Check for another wait condition or mark completed
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task and task.status == TaskStatus.RUNNING:
                if not task.wait_condition and task.result:
                    parsed = parse_wait_condition(task.result)
                    if parsed:
                        task.wait_condition = json.dumps(parsed)

                # Check if agent signalled a NEW wait
                cond = json.loads(task.wait_condition or '{}')
                if task.wait_condition and not cond.get('resumed'):
                    now = datetime.now(UTC).isoformat()
                    cond['waiting_since'] = now
                    cond['last_checked_at'] = now
                    interval = cond.get('check_interval_hours', 24)
                    cond['next_check_at'] = (
                        datetime.now(UTC) + timedelta(hours=interval)
                    ).isoformat()
                    task.wait_condition = json.dumps(cond)
                    assert_transition(task.status, TaskStatus.WAITING)
                    task.status = TaskStatus.WAITING
                    task.updated_at = now
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': task_id,
                            'status': TaskStatus.WAITING,
                        },
                    )
                    return

                # Agent annotated an unrecoverable error via the
                # set_task_error MCP tool → transition to FAILED
                # with the agent's message + category.
                if task.error:
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
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.RUNNING,
                        category=TaskFailureCategory.AGENT_SET_ERROR,
                    )
                    return

                # Check if agent exited with an error result (CLI
                # signaled is_error=true in its final result event,
                # distinct from the agent-annotated error above).
                session = agent_manager.get_session(task_id)
                if session and getattr(session, '_is_error_result', False):
                    assert_transition(task.status, TaskStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    # Prefer the CLI's error text (e.g. "API Error:
                    # 529 ...") over task.result which may hold an
                    # earlier successful output.  This ensures
                    # pytest --only-rerun filters can match the
                    # actual error.
                    cli_error = getattr(session, '_last_result_event', '')
                    task.error = (
                        cli_error or task.result or 'Agent exited with error'
                    )
                    task.error_category = getattr(
                        session, '_error_category', None
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
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.RUNNING,
                        category=TaskFailureCategory.CLI_ERROR,
                    )
                    return

                # Agent exited with incomplete todos → wait (only if no result)
                if has_incomplete_todos(task) and not (
                    task.result and task.result.strip()
                ):
                    await mark_waiting_for_input(
                        task,
                        db,
                        task_id,
                    )
                    return

                # Clear old wait_condition on completion
                task.wait_condition = None
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
                await _send_task_completed(task)

    except Exception as e:
        logger.exception(
            'Woken task pipeline failed for %s: %s',
            task_id,
            e,
        )
        try:
            async with async_session() as db:
                task = await db.get(Task, task_id)
                if task and can_transition(task.status, TaskStatus.FAILED):
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': task_id,
                            'status': TaskStatus.FAILED,
                            'error': str(e),
                        },
                    )
                    _send_task_failed(
                        task,
                        phase=TaskFailurePhase.PIPELINE,
                        category=TaskFailureCategory.UNHANDLED_EXCEPTION,
                    )
        except Exception:
            logger.exception(
                'Failed to update woken task %s after error',
                task_id,
            )
