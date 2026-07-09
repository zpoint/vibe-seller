import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.ai.bash_safety import check_exec_review_status
from app.ai.claude_backend_manager import agent_manager
from app.ai.profiles import DEFAULT_PROFILE_ID, profile_kind_for_id
from app.ai.stop_gates import (
    SOFT_GATE_MAX_DENIALS,
    clear_skill_bindings,
    markdown_format as md_format_gate,
    record_attempt,
    recorded_skills,
    report_reviewer,
    reset_attempts,
    resolve_skill_gates,
    result_language as language_gate,
)
from app.auth import get_current_user
from app.browser.manager import store_slug as _store_slug
from app.database import async_session, get_db
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.models.task_step import TaskStep
from app.models.user import User
from app.routers.tasks_files import (
    looks_like_result_path,
    resolve_store_rules,
    resolve_workspace_result_path,
)
from app.scheduler.task_queue import task_queue_scheduler
from app.schemas.task import TaskCreate, TaskResponse, TaskStepResponse
from app.schemas.user import TaskModeToggle
from app.task_delete import delete_task as delete_task_record
from app.task_runner import (
    TaskHeader,
    build_store_context,
    build_system_extra,
    check_parent_completion,
    get_store_emails,
    reopen_parent_if_child_active,
)
from app.task_runner_auto import auto_run_task
from app.task_runner_exec import execute_planned_task
from app.task_states import (
    DESIGNABLE,
    STARTABLE,
    STOPPABLE,
    WAKEABLE,
    TaskStatus,
    assert_transition,
)
from app.telemetry_events import TelemetryEvent
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


# ── Router ───────────────────────────────────────────────────

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


async def _resolve_creator_names(
    tasks: list[Task],
    db: AsyncSession,
) -> dict[str, str]:
    """Build {user_id: username} map for task creators."""
    user_ids = {t.created_by for t in tasks if t.created_by}
    if not user_ids:
        return {}
    result = await db.execute(
        select(User.id, User.username).where(User.id.in_(user_ids))
    )
    return {uid: uname for uid, uname in result.all()}


def _tasks_with_creator(
    tasks: list[Task],
    names: dict[str, str],
) -> list[TaskResponse]:
    """Convert Task ORM objects to responses with creator name."""
    out = []
    for t in tasks:
        resp = TaskResponse.model_validate(t)
        resp.created_by_name = names.get(t.created_by, 'admin')
        out.append(resp)
    return out


@router.get('', response_model=list[TaskResponse])
async def list_tasks(
    store_id: str | None = None,
    parent_task_id: str | None = None,
    include_archived: bool = True,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = select(Task).order_by(Task.created_at.desc())
    if store_id == '__none__':
        q = q.where(Task.store_id.is_(None))
    elif store_id:
        q = q.where(Task.store_id == store_id)
    if parent_task_id:
        q = q.where(Task.parent_task_id == parent_task_id)
    if not include_archived:
        # Exclude terminal tasks older than 7 days
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        terminal = (TaskStatus.COMPLETED, TaskStatus.FAILED)
        q = q.where(~(Task.status.in_(terminal) & (Task.updated_at < cutoff)))
    result = await db.execute(q)
    tasks = list(result.scalars().all())
    names = await _resolve_creator_names(tasks, db)
    return _tasks_with_creator(tasks, names)


@router.post('', response_model=TaskResponse)
async def create_task(
    data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    store = None
    if data.store_id:
        store = await db.get(Store, data.store_id)
        if not store:
            raise HTTPException(status_code=404, detail='Store not found')
    if data.parent_task_id and not data.store_id:
        raise HTTPException(
            status_code=400,
            detail='Sub-tasks must have a store_id',
        )

    # Resolve plan_mode
    plan_mode = data.plan_mode
    if data.store_id is None:
        plan_mode = True  # Non-store tasks always plan mode
    elif plan_mode is None:
        plan_mode = current_user.plan_mode_default

    task = Task(
        store_id=data.store_id,
        parent_task_id=data.parent_task_id,
        schedule_id=data.schedule_id,
        created_by=current_user.id,
        title=data.title,
        description=data.description,
        platform=data.platform,
        country=data.country,
        status=TaskStatus.PENDING,
        plan_mode=plan_mode,
        skip_reflection=bool(data.skip_reflection),
        ai_profile_id=(
            data.profile_id or current_user.default_profile_id or 'default'
        ),
    )
    db.add(task)
    await db.commit()

    # If this is a new child of a completed parent, reopen parent
    if data.parent_task_id:
        await reopen_parent_if_child_active(task, db)

    # Notify other tabs/users so their list updates without a
    # refetch.  Emitted before schedule_or_run so any subsequent
    # task_update (QUEUED → RUNNING) arrives after task_created
    # — the client relies on that ordering to apply the patch.
    names = await _resolve_creator_names([task], db)
    payload = _tasks_with_creator([task], names)[0]
    await event_bus.emit(
        TelemetryEvent.TASK_CREATED,
        {
            'task_id': task.id,
            'store_id': task.store_id,
            'task': payload.model_dump(mode='json'),
        },
    )
    desc_len = len(task.description or '')
    telemetry.send(
        TelemetryEvent.TASK_CREATED,
        {
            'is_store_task': task.store_id is not None,
            'plan_mode': bool(plan_mode),
            'has_schedule': task.schedule_id is not None,
            'has_parent': task.parent_task_id is not None,
            'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
            'description_length_bucket': telemetry.length_bucket(desc_len),
            'created_by_role': current_user.role,
        },
    )

    # Route through queue for store tasks (enforces
    # platform/country concurrency rules), or launch
    # directly for no-store tasks.
    await schedule_or_run(task.id, store)

    return task


async def schedule_or_run(
    task_id: str,
    store: Store | None,
    launcher: Callable | None = None,
):
    """Route store tasks through the queue scheduler.

    No-store tasks launch immediately (no browser conflict
    possible).  Store tasks go through the queue so the
    same-platform/different-country QUEUE rule is enforced.

    Falls back to direct launch if the scheduler hasn't
    started (e.g. in tests without full app lifespan).

    *launcher* defaults to ``auto_run_task``; pass
    ``execute_planned_task`` for tasks that already have
    a plan.  Note: when routing through the queue the
    launcher is ignored — _dispatch() picks the right
    handler from task state.
    """
    fn = launcher or auto_run_task
    if store and task_queue_scheduler.is_running:
        await task_queue_scheduler.submit(task_id, store.id)
    else:
        asyncio.create_task(fn(task_id, store))


@router.get('/{task_id}', response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    names = await _resolve_creator_names([task], db)
    return _tasks_with_creator([task], names)[0]


@router.delete('/{task_id}')
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete a task: dependent rows, workspace dir, then the row.

    Refuses while the task is DESIGNING/RUNNING — caller should
    Stop first. Returns ``{ok: true}`` whether or not the row
    existed (idempotent), matching the store-delete pattern.
    """
    try:
        await delete_task_record(db, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Drop the gate-attempt counters and the durable skill→gate
    # binding file with the task (bounded server state).
    reset_attempts(task_id)
    clear_skill_bindings(task_id)
    return {'ok': True}


@router.post('/{task_id}/start')
async def start_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in STARTABLE:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot start task in status {task.status}',
        )

    if not task.store_id:
        raise HTTPException(
            status_code=400,
            detail='Cannot start browser task without a store. Assign a store first.',
        )

    store = await db.get(Store, task.store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')

    # Submit to task queue scheduler (handles browser sessions + concurrency)
    await task_queue_scheduler.submit(task_id, store.id)

    return {
        'ok': True,
        'task_id': task_id,
        'status': TaskStatus.QUEUED,
    }


@router.get('/{task_id}/steps', response_model=list[TaskStepResponse])
async def get_task_steps(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TaskStep)
        .where(TaskStep.task_id == task_id)
        .order_by(TaskStep.step_index)
    )
    return result.scalars().all()


@router.post('/{task_id}/agent/start')
async def start_agent(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start a Claude Code agent session for this task."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    prompt = task.description or task.title

    # Build store context for system prompt
    system_extra = ''
    store = None
    if task.store_id:
        store = await db.get(Store, task.store_id)
        if store:
            system_extra = build_store_context(store)

    # If task has a plan, include it as context for execution
    if task.plan:
        system_extra += f'\n\nExecution Plan:\n{task.plan}'

    slug = _store_slug(store.name, store.id) if store else None
    started = await agent_manager.run(
        task_id,
        prompt,
        system_extra=system_extra,
        profile_id=task.ai_profile_id or DEFAULT_PROFILE_ID,
        store_slug=slug,
        skip_reflection=True,
    )
    if not started:
        raise HTTPException(
            status_code=409, detail='Agent is already running for this task'
        )
    return {'ok': True, 'task_id': task_id, 'status': 'agent_started'}


@router.post('/{task_id}/design')
async def design_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start a design agent to create an execution plan for this task."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in DESIGNABLE:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot design task in status {task.status}',
        )

    store = None
    store_emails: list[str] = []
    if task.store_id:
        store = await db.get(Store, task.store_id)
        if store:
            store_emails = await get_store_emails(db, store.id)

    bundle = await build_system_extra(
        task,
        store,
        header=TaskHeader.DESIGN,
        store_emails=store_emails,
    )

    async def _on_design_start() -> bool:
        async with async_session() as db2:
            t = await db2.get(Task, task_id)
            if not t or t.status not in DESIGNABLE:
                return False
            assert_transition(t.status, TaskStatus.DESIGNING)
            t.status = TaskStatus.DESIGNING
            t.updated_at = datetime.now(UTC).isoformat()
            await db2.commit()
            await event_bus.emit(
                'task_update',
                {'task_id': task_id, 'status': t.status},
            )
        return True

    started = await agent_manager.run(
        task_id,
        bundle.prompt,
        system_extra=bundle.system_extra,
        mode=bundle.mode,
        profile_id=task.ai_profile_id or DEFAULT_PROFILE_ID,
        no_store=not task.store_id,
        store_slug=(_store_slug(store.name, store.id) if store else None),
        on_start=_on_design_start,
    )
    if not started:
        raise HTTPException(
            status_code=409, detail='Agent is already running for this task'
        )
    return {
        'ok': True,
        'task_id': task_id,
        'status': TaskStatus.DESIGNING,
    }


@router.patch('/{task_id}/review-plan')
async def toggle_task_mode(
    task_id: str,
    body: TaskModeToggle,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle plan_mode and update user default."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    user = await db.get(User, current_user.id)

    if body.plan_mode is not None:
        # Non-store tasks: reject turning off plan mode
        if not body.plan_mode and not task.store_id:
            raise HTTPException(
                status_code=400,
                detail='Non-store tasks require plan mode',
            )
        task.plan_mode = body.plan_mode
        if user:
            user.plan_mode_default = body.plan_mode

    task.updated_at = datetime.now(UTC).isoformat()
    if user:
        user.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    return {
        'ok': True,
        'plan_mode': task.plan_mode,
    }


@router.post('/{task_id}/execute-plan')
async def execute_plan(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Execute a planned task — starts agent with plan as context."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status != TaskStatus.PLANNED:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot execute task in status {task.status}',
        )
    if task.plan is None:
        raise HTTPException(status_code=400, detail='Task has no plan')

    store = None
    if task.store_id:
        store = await db.get(Store, task.store_id)

    # Transition PLANNED → QUEUED before enqueueing so a
    # second call to execute-plan is rejected (guard above
    # requires PLANNED) and no duplicate queue entries.
    if store and task_queue_scheduler.is_running:
        assert_transition(task.status, TaskStatus.QUEUED)
        task.status = TaskStatus.QUEUED
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': TaskStatus.QUEUED},
        )

    await schedule_or_run(task.id, store, launcher=execute_planned_task)
    actual_status = TaskStatus.RUNNING
    if store and task_queue_scheduler.is_running:
        actual_status = TaskStatus.QUEUED
    return {
        'ok': True,
        'task_id': task_id,
        'status': actual_status,
    }


@router.post('/{task_id}/agent/stop')
async def stop_agent(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Stop a running AI agent and update task status."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in STOPPABLE:
        # Best-effort stop even if task is already terminal
        await agent_manager.stop(task_id)
        return {'ok': True, 'task_id': task_id, 'status': task.status}

    # Commit FAILED *before* stopping the agent so that
    # auto_run_task sees FAILED when it reads the DB after
    # agent_manager.is_running() flips to False.
    task.status = TaskStatus.FAILED
    task.error = 'Stopped by user'
    task.error_category = 'stopped_by_user'
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

    await agent_manager.stop(task_id)
    await check_parent_completion(task, db)

    return {'ok': True, 'task_id': task_id, 'status': 'agent_stopped'}


class SetTaskErrorRequest(BaseModel):
    error: str


@router.post('/{task_id}/error')
async def set_task_error(
    task_id: str,
    body: SetTaskErrorRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Record an unrecoverable error for a task.

    Called by the agent via the `vibe_seller_set_task_error`
    MCP tool. Saves `task.error` and `task.error_category`
    but does NOT transition status — status transitions are
    owned by `auto_run_task` cleanup after the agent session
    exits. It detects a non-empty `task.error` and marks the
    task FAILED at the same cleanup step where successful
    tasks become COMPLETED. This keeps post-task knowledge
    commit + metadata sync running even on failure.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in {TaskStatus.RUNNING, TaskStatus.DESIGNING}:
        raise HTTPException(
            status_code=400,
            detail=(f'Cannot set error on task in status {task.status}'),
        )
    task.error = body.error
    task.error_category = task.error_category or 'agent_reported'
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    # Emit status=task.status (not FAILED) so the UI doesn't
    # flip the badge prematurely. The FAILED transition lands
    # later via auto_run_task cleanup and re-emits task_update.
    await event_bus.emit(
        'task_update',
        {
            'task_id': task_id,
            'status': task.status,
            'error': task.error,
        },
    )
    return {'ok': True, 'task_id': task_id, 'status': task.status}


class SetTaskResultRequest(BaseModel):
    result: str


@router.post('/{task_id}/result')
async def set_task_result(
    task_id: str,
    body: SetTaskResultRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Record the task result summary.

    Called by the agent via the `vibe_seller_set_task_result`
    MCP tool when it wants the final result shown to the user
    to differ from its last chat message.

    This endpoint does NOT transition task status — status is
    owned by `auto_run_task` cleanup after the agent session
    ends. Keeping status transitions in one place avoids the
    short-circuit race that the old `/complete` endpoint
    created (MCP flipped the status mid-session, which caused
    `auto_run_task` to skip post-task knowledge commit and
    metadata sync).
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in {TaskStatus.RUNNING, TaskStatus.DESIGNING}:
        raise HTTPException(
            status_code=400,
            detail=(f'Cannot set result on task in status {task.status}'),
        )

    task_root = (VIBE_SELLER_DIR / 'tasks' / task_id).resolve()

    # Phase-4 execution review gate (no-op unless an ``EXECUTION_LOG.md``
    # exists in the workspace). The audit-report reviewer is no longer a
    # separate subagent loop gated here — it's the constructive
    # completeness reviewer below (``ad_completeness_review``), which
    # lists what's missing each round and converges. See
    # ``amazon-ads/references/output-spec.md``.
    deny = check_exec_review_status(task_root)
    if deny:
        raise HTTPException(status_code=400, detail=deny)

    # File-pointer mode: if `body.result` points at a file inside this
    # task's workspace, read the file and use its contents (see
    # ``resolve_workspace_result_path`` for the path-resolution
    # contract). Otherwise treat the value as direct content.
    raw = body.result
    resolved_content: str | None = None
    target = resolve_workspace_result_path(raw, task_root)
    if target is not None:
        # File reads are blocking; offload so the event loop stays
        # responsive when an agent saves a multi-100KB report.
        try:
            resolved_content = await asyncio.to_thread(
                target.read_text, encoding='utf-8'
            )
        except OSError:
            resolved_content = None

    # A DANGLING pointer must be rejected, never demoted to content: a
    # path-like string that resolves to no file would sail through every
    # content gate vacuously (no ad sections to check) and complete the
    # task with a literal path as its "result" — the bypass that let a
    # quoted pointer end a 3/46 audit. Tell the agent exactly what to do.
    if resolved_content is None and looks_like_result_path(raw):
        raise HTTPException(
            status_code=400,
            detail=(
                f'result looks like a file path but no such file exists '
                f'in this task workspace: {raw!r}. Write the report file '
                'first (built-in Write/Edit), then call set_task_result '
                'with its path (e.g. "./AD_AUDIT_<date>.md") — or pass '
                'the full report content directly.'
            ),
        )

    final_result = resolved_content if resolved_content is not None else raw

    # Generic soft gates — run on the resolved result text, regardless
    # of task type. Each gate gets at most SOFT_GATE_MAX_DENIALS denies
    # per task; the next attempt is allowed through with the original
    # text so a stubborn lint failure (or untranslatable identifier
    # cluster) doesn't trap the agent forever. The agent sees the deny
    # reason as the 400 detail; it can edit and call again. See
    # ``app/ai/stop_gates/`` for the gate bodies and the rationale for
    # placing them at set_task_result rather than the Stop hook (some
    # backends don't emit Stop events).
    for gate_module, gate_args in (
        (md_format_gate, (final_result,)),
        (language_gate, (final_result, task.title, task.description)),
    ):
        deny = gate_module.check(*gate_args)
        if not deny:
            continue
        attempt = record_attempt(task_id, deny.gate)
        if attempt <= SOFT_GATE_MAX_DENIALS:
            raise HTTPException(status_code=400, detail=deny.reason)
        # Past the cap: log and allow through.
        logger.warning(
            'Soft gate %s exceeded %d denials for task %s — allowing '
            'result through anyway. Reason: %s',
            deny.gate,
            SOFT_GATE_MAX_DENIALS,
            task_id,
            deny.reason[:200],
        )

    # Skill-declared domain gates: the session's loaded skills name WHICH
    # reviewers apply (``gates: [...]`` in SKILL.md → get_registered_gates).
    # A session that loaded no gate-declaring skill gets only the generics.
    rules = await resolve_store_rules(db, task.store_id)
    loaded, workspace = agent_manager.loaded_skills_and_workspace(task_id)
    skill_gates = resolve_skill_gates(loaded, workspace or VIBE_SELLER_DIR)

    for gate_name, gate in skill_gates:
        deny = gate.check(final_result, task_id, rules)
        if not deny:
            continue
        # Fail open only on STALL when the gate tracks one (see
        # ad_completeness_review for the stall design).
        is_stalled = getattr(gate, 'is_stalled', None)
        if is_stalled is None or not is_stalled(task_id):
            raise HTTPException(status_code=400, detail=deny.reason)
        logger.warning(
            'Gate %s stalled for task %s — accepting best result. Gaps: %s',
            gate_name,
            task_id,
            deny.reason[:200],
        )

    # Active reviewer sign-off — enforced HERE too, not only in the Stop
    # hook. Some backends never emit a Stop event and finish by calling
    # this endpoint directly; gating the reviewer only at the Stop hook
    # let those runs complete a shallow-but-covering report with the
    # ``ads-report-review`` subagent never spawned (the all-ads
    # slip-through). Trigger = ANY ads skill bound to the task — the
    # reviewer decides whether there was real work to verify or nothing
    # to review (it signs off fast on a lookup). Fail-open is bounded
    # and marks the result UNVERIFIED — never a silent "done".
    if recorded_skills(task_id) & report_reviewer.AD_SKILLS:
        deny_reason = report_reviewer.reviewer_verdict(task_root)
        if deny_reason:
            attempt = record_attempt(task_id, 'ads_report_reviewer')
            if attempt <= report_reviewer.REVIEWER_STALL_CAP:
                raise HTTPException(status_code=400, detail=deny_reason)
            logger.warning(
                'Reviewer stalled for task %s after %d denials — accepting '
                'result as UNVERIFIED. Last reason: %s',
                task_id,
                attempt,
                deny_reason[:200],
            )
            final_result = report_reviewer.partial_banner() + final_result

    task.result = final_result
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()

    # Result persisted — drop the per-task gate-attempt counters and
    # any gate-owned progress state so a long-running server doesn't
    # accumulate stale entries (and a later retry starts fresh).
    reset_attempts(task_id)
    for _name, gate in skill_gates:
        reset = getattr(gate, 'reset_progress', None)
        if reset is not None:
            reset(task_id)

    # Emit a result message so the frontend conversation stream
    # renders the Result section immediately via SSE. Don't
    # persist a TaskMessage here — the agent's _stream_output
    # will persist one when the CLI sends its own result event.
    await event_bus.emit(
        'task_message',
        {
            'task_id': task_id,
            'role': 'result',
            'content': task.result,
        },
    )
    return {
        'ok': True,
        'task_id': task_id,
        'status': task.status,
        'resolved_from_file': resolved_content is not None,
    }


class WakeRequest(BaseModel):
    message: str | None = None


@router.post('/{task_id}/wake')
async def wake_task(
    task_id: str,
    body: WakeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Manually wake a waiting task."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in WAKEABLE:
        raise HTTPException(
            status_code=400,
            detail='Task is not in waiting status',
        )

    condition = json.loads(task.wait_condition or '{}')
    condition['woken_at'] = datetime.now(UTC).isoformat()
    condition['woken_by'] = 'user'
    if body and body.message:
        condition['trigger_data'] = body.message
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

    return {'ok': True, 'task_id': task_id, 'status': 'queued'}
