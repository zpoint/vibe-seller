import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.claude_backend_manager import agent_manager
from app.ai.profiles import (
    DEFAULT_PROFILE_ID,
    resolve_owner_profile,
)
from app.ai.stop_gates import (
    clear_skill_bindings,
    reset_attempts,
    resolve_skill_gates,
)
from app.ai.stop_gates.listing_upload_gate import check_upload_verdicts
from app.ai.turn_hold_budget import reset_ledger
from app.auth import get_current_user
from app.browser.manager import store_slug as _store_slug
from app.database import async_session, get_db
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.models.task_step import TaskStep
from app.models.user import User
from app.routers.task_submission import (
    SetTaskResultRequest,
    apply_skill_gates,
    apply_soft_gates,
    declared_gaps,
    refuse as _refuse,
    resolve_submitted_result,
    retain_submission as _retain_submission,
)
from app.routers.tasks_files import resolve_store_rules
from app.scheduler.task_queue import task_queue_scheduler
from app.schemas.task import TaskCreate, TaskResponse, TaskStepResponse
from app.schemas.user import TaskModeToggle
from app.task_delete import delete_task as delete_task_record
from app.task_launch import schedule_or_run
from app.task_outcome import apply_outcome, resolve_outcome
from app.task_runner import (
    TaskHeader,
    build_store_context,
    build_system_extra,
    check_parent_completion,
    get_store_emails,
    reopen_parent_if_child_active,
)
from app.task_runner_exec import execute_planned_task
from app.task_states import (
    DESIGNABLE,
    STARTABLE,
    STOPPABLE,
    WAKEABLE,
    TaskStatus,
    assert_transition,
)
from app.telemetry_events import GateOutcome, TaskSource, TelemetryEvent
from app.telemetry_harness import send_result_gated
from app.telemetry_tasks import send_plan_verdict, send_task_created
from app.telemetry_via import current_via, source_of_via
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
    status: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List tasks, newest first.

    ``status`` and ``limit`` are filters the hosted MCP endpoint needs
    (``app/agent_mcp/tools.py``). They live HERE rather than in the MCP
    layer so both callers run the same query — a filter that only the
    MCP path had would be a second definition of "which tasks are
    these".
    """
    q = select(Task).order_by(Task.created_at.desc())
    if store_id == '__none__':
        q = q.where(Task.store_id.is_(None))
    elif store_id:
        q = q.where(Task.store_id == store_id)
    if parent_task_id:
        q = q.where(Task.parent_task_id == parent_task_id)
    if status:
        q = q.where(Task.status == status)
    if not include_archived:
        # Exclude terminal tasks older than 7 days
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        terminal = (TaskStatus.COMPLETED, TaskStatus.FAILED)
        q = q.where(~(Task.status.in_(terminal) & (Task.updated_at < cutoff)))
    if limit is not None:
        q = q.limit(limit)
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

    # Provenance is derived from the via channel (who is calling —
    # app.telemetry_via), never from a request field: a sub-task is
    # always 'subtask', an MCP caller is 'mcp_external'.
    source = (
        TaskSource.SUBTASK
        if data.parent_task_id
        else source_of_via(current_via())
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
        source=source,
        ai_profile_id=(
            data.profile_id
            or await resolve_owner_profile(current_user, db)
            or DEFAULT_PROFILE_ID
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
    send_task_created(
        task,
        user_id=current_user.id,
        created_by_role=current_user.role,
    )

    # Route through queue for store tasks (enforces
    # platform/country concurrency rules), or launch
    # directly for no-store tasks. When the client will upload
    # attachments first (defer_start), skip the launch — the client
    # POSTs /start once the files are in the workspace, so the agent's
    # prompt sees them (see build_system_extra uploaded-files note).
    if not data.defer_start:
        await schedule_or_run(task.id, store)

    return task


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
    # Drop the gate-attempt counters, the turn hold's re-drive
    # ledger, and the durable skill→gate binding file with the task
    # (bounded server state).
    reset_attempts(task_id)
    reset_ledger(task_id)
    clear_skill_bindings(task_id)
    return {'ok': True}


@router.post('/{task_id}/start')
async def start_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Launch a task that isn't running yet.

    Two callers, one meaning — "launch this task": the manual Run
    button, and the resume half of the deferred create flow
    (``defer_start``: the client uploads attachments into the workspace
    first, then POSTs here so the agent's prompt sees them).  It must
    therefore accept every task shape ``create_task`` accepts.

    Routing is delegated to ``schedule_or_run``.  This endpoint used to
    re-derive it inline — queue-submit, and 400 unless the task had a
    store — which predated ``defer_start``, back when /start meant only
    "start a browser task".  Once create started deferring, that guard
    became a dead end: a no-store task with an attachment was never
    launched by create, was refused by /start, and sat at PENDING with
    no error for the user to see.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in STARTABLE:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot start task in status {task.status}',
        )

    store = None
    if task.store_id:
        store = await db.get(Store, task.store_id)
        if not store:
            raise HTTPException(status_code=404, detail='Store not found')

    queued = await schedule_or_run(task_id, store)

    return {
        'ok': True,
        'task_id': task_id,
        # Only the queue parks the task at QUEUED; a direct launch
        # leaves it where it was until the launcher moves it.
        'status': TaskStatus.QUEUED if queued else task.status,
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

    store = await db.get(Store, task.store_id) if task.store_id else None
    store_emails = await get_store_emails(db, store.id) if store else []

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
    send_plan_verdict(task_id, approved=True)
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

    # File-pointer mode: if `body.result` points at a file inside this
    # task's workspace, read the file and use its contents (see
    # ``resolve_workspace_result_path`` for the path-resolution
    # contract). Otherwise treat the value as direct content.
    raw = body.result
    final_result, resolved_from_file = await resolve_submitted_result(
        raw, task_id, task_root
    )

    # RETAIN BEFORE REVIEW. Every gate below refuses by raising, and a
    # raise here used to discard the submission entirely — N refusals
    # left the run holding nothing, so the finalizer had only streamed
    # prose to offer. Submission and verdict are separate facts: what
    # the agent submitted is recorded now, unconditionally, and a
    # refusal only annotates it (see ``_refuse``). ``task.result`` is
    # still set only once every gate accepts.
    declared = declared_gaps(body)
    await _retain_submission(db, task, final_result)

    # A listing batch this turn uploaded to the live account must have a
    # clean verdict before the task may finish (no-op without uploads).
    # Hard: refused on every submit until the verdict is clean.
    deny = await asyncio.to_thread(check_upload_verdicts, task_root)
    if deny:
        send_result_gated(
            task_id,
            'upload_verdict',
            GateOutcome.REFUSED,
            attempt=task.submission_count or 1,
            gap_count=1,
        )
        await _refuse(db, task, deny, declared)

    soft_gaps = await apply_soft_gates(
        db, task, task_id, final_result, declared
    )

    # Skill-declared domain gates: the session's loaded skills name WHICH
    # gates apply (``gates: [...]`` in SKILL.md → get_registered_gates).
    # A session that loaded no gate-declaring skill gets only the generics.
    rules = await resolve_store_rules(db, task.store_id)
    # Gates and their inputs read files and the task DB synchronously;
    # keep each off the event loop.
    loaded, workspace = await asyncio.to_thread(
        agent_manager.loaded_skills_and_workspace, task_id
    )
    skill_gates = await asyncio.to_thread(
        resolve_skill_gates, loaded, workspace or VIBE_SELLER_DIR
    )

    final_result, skill_gaps = await apply_skill_gates(
        db, task, skill_gates, final_result, rules, declared
    )
    stalled_gaps = list(soft_gaps) + list(skill_gaps)

    # Recovery: a valid result supersedes an earlier agent-reported
    # error this turn (error → recovered → result must not ship as
    # FAILED). Infra-detected errors are not cleared. Cleared BEFORE
    # resolving, so a superseded error isn't re-rendered as a caveat —
    # the agent replaced it, it isn't an outstanding qualification.
    if task.error and task.error_category == 'agent_reported':
        task.error = None
        task.error_category = None

    # ACCEPTED — top of the precedence order.
    task.accepted_result = final_result
    task.submitted_result = final_result
    # A gate that failed open still had unmet items; keep them so the
    # outcome carries its caveats. Otherwise the verdict is clean.
    task.review_gaps = (
        json.dumps(declared + tuple(stalled_gaps), ensure_ascii=False)
        if (stalled_gaps or declared)
        else None
    )
    # Materialise the derived view now, so the SSE result card and any
    # reader before finalize see the same text the finalizer will.
    apply_outcome(task, resolve_outcome(task))
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()

    # Result persisted — drop the per-task gate-attempt counters, the
    # turn hold's re-drive ledger, and any gate-owned progress state
    # so a long-running server doesn't accumulate stale entries (and a
    # later retry starts fresh).
    reset_attempts(task_id)
    reset_ledger(task_id)
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
        'resolved_from_file': resolved_from_file,
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
