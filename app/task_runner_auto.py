import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging

from app.ai.claude_backend_manager import agent_manager
from app.ai.claude_backend_utils import parse_wait_condition
from app.ai.external_config import (
    ExternalConfigOverrideError,
    assert_profile_compatible,
)
from app.ai.profiles import DEFAULT_PROFILE_ID
from app.ai.task_status_reconcile import qa_followup_needs_input
from app.browser.manager import browser_manager, store_slug as _store_slug
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.schedule_constants import StalenessCheck
from app.models.store import Store
from app.models.task import Task
from app.routers.dida365_oauth import refresh_token_if_needed
from app.task_runner import (
    TaskHeader,
    build_system_extra,
    check_parent_completion,
    get_store_emails,
    has_incomplete_todos,
    mark_waiting_for_input,
    maybe_inject_pending_answers,
    maybe_wait_for_children,
    park_waiting_for_pending_question,
    park_waiting_for_text_only_response,
    sync_store_metadata,
)
from app.task_session_lifecycle import (
    wait_for_session_with_retry,
)
from app.task_states import (
    TaskStatus,
    assert_transition,
    can_transition,
)
from app.workspace.knowledge_sync import knowledge_sync
from app.workspace.manager import workspace_manager
from app.workspace.skills_sync import skills_sync

logger = logging.getLogger(__name__)


async def auto_run_task(task_id: str, store: Store | None):
    """Background pipeline: single plan_then_execute session.

    Uses native plan mode: agent plans in read-only mode, calls
    ExitPlanMode, backend captures plan and auto-approves (or
    waits for user if interactive), then agent executes in
    the same session with full permissions.

    Launched as a background asyncio task from create_task().
    Multiple tasks for the same store can run concurrently
    with CDP-level isolation via CDPMuxProxy.
    """
    catalog_backups: dict = {}
    schedule: Schedule | None = None
    try:
        # Check task wasn't cancelled while queued.  Also load the
        # Schedule (if any) in the same session — reused for the
        # staleness gate below and for prompt assembly, so we pay
        # at most one Schedule lookup per task.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in (
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
            ):
                return
            if task.schedule_id:
                schedule = await db.get(Schedule, task.schedule_id)

        # Fail fast if ~/.claude/settings.json overrides the chosen
        # profile's env. Without this gate the agent silently runs
        # against whatever external tool (cc-switch / similar) wrote
        # into settings.json, the user's UI selection is a no-op, and
        # the failure shows up later as an opaque API error.
        try:
            assert_profile_compatible(task.ai_profile_id)
        except ExternalConfigOverrideError as e:
            # Store the structured payload as JSON in ``task.error``
            # so the frontend can render it in the user's locale
            # (see ``frontend/src/views/TasksView.tsx`` error_category
            # branch). Non-i18n consumers (logs / CLI) get the same
            # JSON and can still extract the English ``message`` field.
            async with async_session() as db_fail:
                t = await db_fail.get(Task, task_id)
                if t and can_transition(t.status, TaskStatus.FAILED):
                    t.status = TaskStatus.FAILED
                    t.error = json.dumps(e.to_api_detail())
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
            return

        # 0. Write MCP config (no browser start — agent does that lazily).
        # Store tasks get their per-store wrapper; no-store tasks get the
        # store-less "web" wrapper. Also query linked email addresses.
        store_emails: list[str] = []
        async with async_session() as db:
            await browser_manager.write_task_browser_config(store, db)
            if store:
                store_emails = await get_store_emails(db, store.id)

        # 1. Check remote for updates (async, non-blocking).
        # Local package sync runs once at startup (main.py lifespan).
        asyncio.create_task(knowledge_sync.check_and_sync_remote())
        asyncio.create_task(skills_sync.check_and_sync_remote())

        # 1c. Catalog sync: staleness check + rotation
        # Driven by the schedule's staleness_check flag.
        if (
            schedule is not None
            and schedule.staleness_check == StalenessCheck.CATALOG
        ):
            if task.store_id is None:
                # L2: global catalog only
                l2_stale, _ = knowledge_sync.catalog_needs_update(None)
                if not l2_stale:
                    async with async_session() as db:
                        task = await db.get(Task, task_id)
                        if task:
                            task.status = TaskStatus.COMPLETED
                            task.result = 'L2 catalog already up-to-date'
                            task.completed_at = datetime.now(UTC).isoformat()
                            task.updated_at = task.completed_at
                            await db.commit()
                            await event_bus.emit(
                                'task_update',
                                {
                                    'task_id': task_id,
                                    'status': TaskStatus.COMPLETED,
                                },
                            )
                    return
                catalog_backups = knowledge_sync.rotate_catalogs(
                    None,
                    l2_stale=True,
                    l3_stale=False,
                )
            else:
                # L3: per-store catalog only
                store_slug = _store_slug(store.name, store.id)
                _, l3_stale = knowledge_sync.catalog_needs_update(
                    store_slug,
                )
                if not l3_stale:
                    async with async_session() as db:
                        task = await db.get(Task, task_id)
                        if task:
                            task.status = TaskStatus.COMPLETED
                            task.result = 'L3 catalog already up-to-date'
                            task.completed_at = datetime.now(UTC).isoformat()
                            task.updated_at = task.completed_at
                            await db.commit()
                            await event_bus.emit(
                                'task_update',
                                {
                                    'task_id': task_id,
                                    'status': TaskStatus.COMPLETED,
                                },
                            )
                    return
                catalog_backups = knowledge_sync.rotate_catalogs(
                    store_slug,
                    l2_stale=False,
                    l3_stale=True,
                )

        # 2. Start session: auto mode or plan_then_execute
        # Re-read task to get current fields; status transition is
        # deferred to on_start (after the concurrency semaphore is
        # acquired) so queued tasks stay PENDING until they truly
        # start.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in (
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
            ):
                return

        h = TaskHeader.DESIGN if task.plan_mode else TaskHeader.AUTO
        bundle = await build_system_extra(
            task,
            store,
            header=h,
            store_emails=store_emails,
            schedule=schedule,
        )

        # If this run is resuming a WAITING task that the operator
        # answered via the UI, inject their answers as the user turn
        # and clear the wait_condition so a subsequent re-queue
        # doesn't re-deliver the same stale answers. The resumed
        # session (task.session_id -> `claude --resume`) already has
        # the AskUserQuestion tool call in its transcript, so the
        # agent just needs the answers delivered as a follow-up turn.
        bundle = await maybe_inject_pending_answers(task_id, bundle)

        async def _on_start() -> bool:
            """Transition status after concurrency slot acquired."""
            async with async_session() as db2:
                t = await db2.get(Task, task_id)
                if not t or t.status not in (
                    TaskStatus.PENDING,
                    TaskStatus.QUEUED,
                ):
                    return False
                if t.plan_mode:
                    assert_transition(t.status, TaskStatus.DESIGNING)
                    t.status = TaskStatus.DESIGNING
                else:
                    assert_transition(t.status, TaskStatus.RUNNING)
                    t.status = TaskStatus.RUNNING
                    t.started_at = datetime.now(UTC).isoformat()
                t.updated_at = datetime.now(UTC).isoformat()
                await db2.commit()
                await event_bus.emit(
                    'task_update',
                    {'task_id': task_id, 'status': t.status},
                )
            return True

        await refresh_token_if_needed()
        # Resume when a prior session exists for this task — e.g.
        # WAITING → QUEUED flow where the operator just answered a
        # pending AskUserQuestion. Harmless when session_id is null
        # (the backend no-ops the resume flag in that case).
        resume_existing_session = bool(task.session_id)
        started = await agent_manager.run(
            task_id,
            bundle.prompt,
            system_extra=bundle.system_extra,
            mode=bundle.mode,
            profile_id=task.ai_profile_id or DEFAULT_PROFILE_ID,
            no_store=not store,
            auto_approve_plan=(
                # Any schedule-owned task (plan-only authoring OR a
                # scheduled fire) auto-approves at ExitPlanMode —
                # the frozen-plan architecture handles review via
                # the SchedulePlanPanel + Re-plan flow, and the
                # fanout-plan validator enforces structural rules
                # at ExitPlanMode time. Standalone tasks still
                # honor task.plan_mode (user.plan_mode_default
                # controls that path at task create time).
                bool(task.schedule_id) or not task.plan_mode
            ),
            store_slug=(_store_slug(store.name, store.id) if store else None),
            on_start=_on_start,
            skip_reflection=task.skip_reflection,
            resume=resume_existing_session,
        )
        if not started:
            return

        # Wait for the session and finalize status. Shared with
        # follow-up paths (see `finalize_followup_session` below).
        # `wait_for_session_with_retry` transparently retries when
        # Claude Code rejects a `--resume` session_id — old design
        # did this inside `claude_backend_manager._release_on_done`,
        # but that raced with this finalizer.
        my_session = await wait_for_session_with_retry(
            task_id, agent_manager.get_session(task_id)
        )
        if my_session is None:
            return

        # Guard + DESIGNING handling are in `_finalize_terminal_state`
        # below; bail out here only to skip the knowledge-commit /
        # metadata-sync side effects when the task is already terminal
        # (user stop, retry reset, etc.).
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in {
                TaskStatus.RUNNING,
                TaskStatus.DESIGNING,
            }:
                return

        # 5b. Commit any knowledge changes from the agent
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

        # 5c. Sync store metadata.json → DB
        if store:
            async with async_session() as db_meta:
                await sync_store_metadata(store.id, db_meta)

        # 6. Re-check supersession before terminal writes — the
        #    awaits above (knowledge commit, metadata sync) may
        #    have yielded long enough for a retry to start.
        if agent_manager.get_session(task_id) is not my_session:
            return

        # 7. Terminal transition (shared with follow-up path).
        await _finalize_terminal_state(task_id, my_session)

    except Exception as e:
        logger.exception('Auto-run pipeline failed for task %s: %s', task_id, e)
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
        except Exception:
            logger.exception(
                'Failed to update task %s status after error',
                task_id,
            )
    finally:
        if catalog_backups:
            try:
                async with async_session() as db_cat:
                    t = await db_cat.get(Task, task_id)
                    if t and t.status == TaskStatus.COMPLETED:
                        knowledge_sync.cleanup_catalog_backups(catalog_backups)
                    else:
                        knowledge_sync.restore_catalogs(catalog_backups)
            except Exception:
                logger.warning(
                    'Catalog backup cleanup failed for %s',
                    task_id,
                    exc_info=True,
                )
        # If this task has a parent waiting on children, check
        # whether all siblings are now terminal.
        try:
            async with async_session() as db_parent:
                task = await db_parent.get(Task, task_id)
                if task:
                    await check_parent_completion(task, db_parent)
        except Exception:
            logger.debug(
                'Parent check after task %s: %s',
                task_id,
                exc_info=True,
            )


async def finalize_followup_session(task_id: str, store: Store | None):
    """Finalize a follow-up session's status outside `auto_run_task`.

    Follow-ups spawned via POST /tasks/{id}/messages call
    `agent_manager.run()` directly rather than going through
    `auto_run_task`, so nothing transitioned `task.status` from
    RUNNING / DESIGNING to COMPLETED / FAILED after the new session
    ended.  This helper is scheduled as a background task by
    `tasks_conversation.py` right after it starts a follow-up
    session; it waits for that session to finish and then runs the
    same terminal-state logic as `auto_run_task`.

    Skipped fast-paths (not applicable to follow-ups):
    - Catalog staleness check (catalog-sync tasks don't use
      /messages follow-ups).
    - The DESIGNING-without-plan branch (follow-ups re-enter via
      the Plan rejected path, which `execute_planned_task` owns
      after approve; the standalone case is not reachable here).
    """
    initial_session = agent_manager.get_session(task_id)
    if initial_session is None:
        return
    try:
        my_session = await wait_for_session_with_retry(task_id, initial_session)
        if my_session is None:
            return

        # Bail on user-initiated stops that already wrote FAILED,
        # and on retries that reset status to PENDING / QUEUED.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or task.status not in {
                TaskStatus.RUNNING,
                TaskStatus.DESIGNING,
            }:
                return

        # Knowledge commit + per-store metadata sync.  Same as the
        # §5b / §5c block in `auto_run_task`.  Errors are logged,
        # never fatal — the terminal transition below must still run.
        try:
            async with async_session() as db:
                task = await db.get(Task, task_id)
            if task:
                await workspace_manager._auto_commit(
                    f'Knowledge update after task: {task.title}'
                )
        except Exception as e:
            logger.warning(
                'Knowledge commit after follow-up %s failed: %s',
                task_id,
                e,
            )
        if store:
            try:
                async with async_session() as db_meta:
                    await sync_store_metadata(store.id, db_meta)
            except Exception:
                logger.debug(
                    'Store metadata sync after follow-up %s failed',
                    task_id,
                    exc_info=True,
                )

        # Re-check supersession before terminal writes — the awaits
        # above may have yielded long enough for another retry /
        # follow-up to start.
        if agent_manager.get_session(task_id) is not my_session:
            return

        # This follow-up finalize IS the owner of the terminal
        # transition — the /messages follow-up path has no
        # execute_planned_task — so tell _finalize_terminal_state not to
        # defer plan-mode completion to that (absent) owner.
        await _finalize_terminal_state(task_id, my_session, is_followup=True)
    except Exception as e:
        logger.exception(
            'Follow-up finalize failed for task %s: %s',
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
        except Exception:
            logger.exception(
                'Failed to update task %s status after finalize error',
                task_id,
            )


async def _finalize_terminal_state(
    task_id: str, my_session, *, is_followup: bool = False
):
    """Shared terminal-state writer.

    Called by both `auto_run_task` (post-wait section) and
    `finalize_followup_session`. Handles:
    - Plan-mode exit during DESIGNING without a plan → COMPLETED (skip-
      plan success) or FAILED (design failure)
    - Plan saved, ownership handed to `execute_planned_task` → return
    - Wait-condition → WAITING
    - Agent-annotated `task.error` → FAILED
    - CLI `_is_error_result` → FAILED
    - Incomplete todos without result → WAITING (set_task_error-free)
    - Empty result (and agent didn't signal success) → FAILED
    - Children-waiting guard
    - Default → COMPLETED
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

        if qa_followup_needs_input(session):  # prose-only after answer
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
