from dataclasses import dataclass
from datetime import UTC, datetime
import enum
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import store_slug as _store_slug
from app.database import async_session
from app.events.bus import event_bus
from app.models.email_account import EmailAccount
from app.models.schedule import Schedule
from app.models.schedule_constants import StalenessCheck
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.prompts import (
    CATALOG_RESTRICTION_PROMPT_L2,
    CATALOG_RESTRICTION_PROMPT_L3,
    DESIGN_SYSTEM_PROMPT,
    DESIGN_SYSTEM_PROMPT_AUTO,
    SCHEDULED_PRETASK_PROMPT,
    WAITING_INSTRUCTION_PROMPT,
    render_prompt,
)
from app.question_answers import expand_free_text_answers
from app.task_runner_context import (
    build_all_stores_context,
    build_store_context,
    build_system_context,
    detect_language_hint,
    ticktick_context,
)
from app.task_states import (
    TaskStatus,
    assert_transition,
)
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)


# ── Prompt assembly ──────────────────────────────────────────


class TaskHeader(enum.StrEnum):
    """Task instruction type. Determines user prompt and mode."""

    DESIGN = 'design'
    AUTO = 'auto'
    EXECUTE = 'execute'
    WOKEN = 'woken'
    CHAT = 'chat'
    RAW = 'raw'


@dataclass(frozen=True)
class PromptBundle:
    """Result of prompt assembly."""

    prompt: str
    system_extra: str
    mode: str


def _format_header(
    header: TaskHeader,
    task: 'Task',
) -> tuple[str, str, str]:
    """Return ``(prompt, mode, extra_context)``.

    All prompt text is centralised here — callers must not
    hard-code prompt strings.
    """
    desc = task.description or task.title

    if header == TaskHeader.DESIGN:
        prompt = 'Design an execution plan for this task: ' + task.title
        if task.description:
            prompt += f'\n\nDetails: {task.description}'
        return prompt, 'plan_then_execute', ''

    if header == TaskHeader.AUTO:
        prompt = task.title
        if task.description:
            prompt += f'\n\nDetails: {task.description}'
        return prompt, 'auto', ''

    if header == TaskHeader.EXECUTE:
        mode = 'auto' if not task.plan_mode else 'execute'
        extra = ''
        if task.plan:
            extra = 'Execute the following plan:\n\n' + task.plan
        return desc, mode, extra

    if header == TaskHeader.WOKEN:
        mode = 'auto' if not task.plan_mode else 'execute'
        extra = ''
        if task.plan:
            extra = 'Continue executing the task. Plan:\n\n' + task.plan
        return desc, mode, extra

    if header == TaskHeader.CHAT:
        mode = 'plan_then_execute' if task.plan_mode else 'auto'
        return desc, mode, ''

    # RAW
    return desc, 'auto', ''


async def build_system_extra(
    task: 'Task',
    store: 'Store | None',
    *,
    header: TaskHeader = TaskHeader.AUTO,
    store_emails: list[str] | None = None,
    extra_context: str = '',
    schedule: 'Schedule | None' = None,
) -> PromptBundle:
    """Single entry point for ALL task prompt assembly.

    Returns a :class:`PromptBundle` with user prompt,
    system_extra, and mode.  Every caller should use this
    instead of assembling prompts inline.

    ``schedule`` is an optional pre-loaded Schedule row for
    tasks with ``task.schedule_id`` set.  Passing it avoids an
    extra DB round-trip when the caller already has it loaded
    (e.g. ``auto_run_task`` which needs it for the staleness
    gate too).  When omitted, the Schedule is loaded inline.
    """
    prompt, mode, header_extra = _format_header(header, task)

    # Base prompt — only use the plan-mode template for actual
    # plan-then-execute runs. Execute/woken runs for planned
    # tasks must not inherit plan-only sections (ExitPlanMode).
    base = (
        DESIGN_SYSTEM_PROMPT
        if mode == 'plan_then_execute'
        else DESIGN_SYSTEM_PROMPT_AUTO
    )

    # Fill workspace guidance slot. Catalog-sync tasks need
    # the L2/L3 restriction prompt; this is driven by the
    # schedule's staleness_check flag.
    if schedule is None and task.schedule_id:
        async with async_session() as db:
            schedule = await db.get(Schedule, task.schedule_id)
    is_catalog = bool(
        schedule and schedule.staleness_check == StalenessCheck.CATALOG
    )
    if is_catalog:
        guidance = (
            CATALOG_RESTRICTION_PROMPT_L2
            if task.store_id is None
            else CATALOG_RESTRICTION_PROMPT_L3
        )
    else:
        guidance = ''
    base = base.replace('{workspace_guidance}', guidance)

    # Always same order, always all pieces
    parts: list[str] = [base]
    parts.append(detect_language_hint(task.title, task.description))
    parts.append(WAITING_INSTRUCTION_PROMPT)

    if store:
        emails = store_emails
        if emails is None:
            async with async_session() as db:
                emails = await get_store_emails(db, store.id)
        parts.append(
            build_store_context(
                store,
                email_addresses=emails,
                task_platform=task.platform,
                task_country=task.country,
            )
        )
    else:
        async with async_session() as db:
            ctx = await build_all_stores_context(db)
        if ctx:
            parts.append(ctx)

    parts.append(ticktick_context())
    parts.append(await build_system_context(task))

    # Reflection reminder — full prompt delivered via Stop hook
    if not is_catalog:
        parts.append(
            'You will be asked to reflect and update knowledge'
            ' before the session ends — plan accordingly.'
        )

    # Scheduled-task block: tell the agent it's scheduled and
    # point it at the cross-run state tools. Catalog sync jobs
    # are scheduled but have no cursor to resume, so skip.
    # Plan-only tasks (is_plan_only=True) are creation-time planners,
    # not scheduled runs — they get their own block instead.
    if task.schedule_id and not is_catalog and not task.is_plan_only:
        parts.append(SCHEDULED_PRETASK_PROMPT)

    # Plan-only authoring block: tells the agent it is writing a
    # reusable plan that will run N times across M stores, so the
    # plan must not reference a specific store or per-store path.
    if task.is_plan_only:
        # A store-bound schedule (store_id set → always phase_mode
        # 'single') targets ONE specific store; every fire runs the
        # plan directly as that single store's agent. It must NOT be
        # authored as a multi-store / orchestrator plan. A store-less
        # schedule runs across many stores (fanout, or the all-stores
        # 'single' task), so it keeps the abstract framing.
        store_bound = schedule is not None and schedule.store_id is not None
        if store_bound:
            scope_intro = (
                'You are authoring a reusable plan for a schedule bound'
                ' to a SINGLE specific store. Every fire runs this plan'
                " directly as that one store's own browser agent."
            )
            scope_abstraction = (
                '- Write the plan as the concrete, top-to-bottom steps'
                ' ONE store-bound agent performs in this store, using'
                " the store's L3 catalog / notes to find concrete"
                ' paths. Per-store context is injected at fire time.\n'
            )
        else:
            scope_intro = (
                'You are authoring a reusable plan for a schedule that'
                ' will run many times, potentially across many stores.'
            )
            scope_abstraction = (
                '- Describe the procedure abstractly: what to do for'
                ' each (store, platform, site), using the per-store'
                ' L3 catalog to discover concrete paths.\n'
                '- Do NOT reference a specific store slug, store name,'
                ' or hard-coded file path from any one store.\n'
            )
        plan_only_block = (
            scope_intro + '\n\nRequirements for this plan:\n'
            '- **You MUST call ExitPlanMode with your plan text.**'
            ' Not calling ExitPlanMode — even if the task seems'
            ' trivial, or you think "nothing to plan" — is a'
            ' failure. The plan cannot be frozen onto the schedule'
            ' without it, and the schedule will stay stuck in'
            ' `planning` state until a human intervenes. There is'
            ' always something to write: even a one-paragraph plan'
            ' is better than no plan.\n'
            + scope_abstraction
            + '- Do NOT execute the task now — after ExitPlanMode the'
            ' session terminates and the plan is frozen onto the'
            ' schedule. Each future fire re-injects per-store'
            ' context and runs the plan against that store.\n'
            '- **Ask clarifying questions early with AskUserQuestion.**'
            ' The user is present right now; this is the one chance'
            ' to lock in their intent before the plan is frozen.'
            ' If ANYTHING is ambiguous (scope, thresholds, target'
            ' platforms, output channels, failure handling, cadence),'
            ' ask before calling ExitPlanMode. A plan that fires the'
            ' wrong thing N times is worse than one extra question.'
            ' Planned-run fires do NOT prompt the user — if the plan'
            ' itself demands runtime input, fire-time agents must'
            ' park in WAITING.\n'
            '- **Write the entire plan in the same language the user'
            ' used in the task title and description.** Match the'
            " user's language verbatim — headings, bullets, step"
            ' descriptions, error messages, field names, all of it.'
            ' The generic language hint earlier in this prompt is a'
            ' soft nudge; this rule is hard. Do not translate the'
            " plan into a different language because it's more"
            ' convenient for you. Mixed-language plans are also'
            ' wrong.'
        )
        # Store-bound schedule: the fire is ONE store-bound task that
        # runs the plan directly. Forbid orchestrator/sub-task spawning
        # so the plan doesn't enumerate stores or fan out — that is the
        # all-stores ('fanout') schedule's job, not this one's.
        if store_bound:
            plan_only_block += (
                '\n- This schedule is SINGLE-STORE: the fire runs this'
                ' plan directly as one store-bound agent. Do NOT'
                ' enumerate stores, do NOT call `vibe_seller_create_task`,'
                ' do NOT design an orchestrator or parent/child'
                ' sub-tasks, and do NOT reference or touch any other'
                ' store. The plan is the direct steps for this one store.'
            )
        # Fanout schedules (phase_mode='fanout') already spawn one
        # per-store child Task at fire time. If the plan tells the
        # agent to call `vibe_seller_create_task` from within it,
        # each store's child will recursively create more children.
        # Forbid orchestrator-style spawning explicitly — the hook
        # (`_validate_fanout_plan_text` in claude_backend_hooks.py)
        # also enforces this at ExitPlanMode time.
        elif schedule is not None and schedule.phase_mode == 'fanout':
            plan_only_block += (
                '\n- This schedule is FANOUT mode: the scheduler'
                ' already creates one per-store Task per fire and'
                ' runs this plan once per store. Do NOT call'
                ' `vibe_seller_create_task`, do NOT design an'
                ' orchestrator step, and do NOT reference'
                ' parent_task_id or sub-task spawning. The plan'
                ' should describe what ONE store-bound agent does'
                ' — fanout handles the rest.'
                '\n- FINALIZE ability: by default each store finishes'
                ' independently and nothing runs afterward. If — and'
                ' only if — this task needs ONE cross-store step AFTER'
                ' every store finishes (combine all stores into a'
                ' single PR / report / notification, or retry the'
                ' failed stores), you MUST actually CALL the'
                ' `vibe_seller_register_finalize` tool DURING this'
                ' planning session, passing a natural-language'
                ' description of that final step. This is the ONE'
                ' planning-time side effect that is expected — writing'
                ' the finalize step IS part of authoring the plan, so'
                ' it does not count as "executing the task". Make the'
                ' tool call (typically right before ExitPlanMode).'
                ' **Merely describing the finalize in your plan text'
                ' does NOT register it — only the tool call does;'
                ' a plan that talks about a finalize step without'
                ' calling the tool is a FAILED plan.** The framework'
                ' then runs ONE finalize task once all per-store'
                ' children are terminal, handed a batch_results.json'
                " with every store's status + result + output"
                ' location. Keep per-store work in the plan; put the'
                ' cross-store combine ONLY in register_finalize —'
                ' never as a per-store step (that would run the'
                ' combine N times). If the work is fully independent'
                ' per store, do not call it.'
            )
        parts.append(plan_only_block)

    # Task-derived context (plan text) + caller context
    if header_extra:
        parts.append(header_extra)
    if extra_context:
        parts.append(extra_context)

    # Plugin extension seam: fragments registered for the 'system_extra'
    # slot are appended to the end of the assembled system prompt.
    from app.plugins import registered_prompt_fragments  # noqa: PLC0415

    parts.extend(registered_prompt_fragments('system_extra'))

    result = '\n\n'.join(p.strip() for p in parts if p and p.strip())
    if store:
        result = render_prompt(
            result, store_slug=_store_slug(store.name, store.id)
        )
    return PromptBundle(prompt=prompt, system_extra=result, mode=mode)


# ── Shared helpers ──────────────────────────────────────────


async def get_store_emails(
    db: AsyncSession,
    store_id: str,
) -> list[str]:
    """Get email addresses linked to a store."""
    result = await db.execute(
        select(EmailAccount.email)
        .join(
            StoreEmailLink,
            StoreEmailLink.email_account_id == EmailAccount.id,
        )
        .where(StoreEmailLink.store_id == store_id)
    )
    return [row[0] for row in result.all()]


def has_incomplete_todos(task: Task) -> bool:
    """Return True if the task has any non-completed todo items."""
    if not task.todos:
        return False
    try:
        todos = json.loads(task.todos)
        return any(t.get('status') != 'completed' for t in todos)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            'Failed to parse todos for task %s, treating as incomplete',
            task.id,
        )
        return True


async def mark_waiting_for_input(task: Task, db: AsyncSession, task_id: str):
    """Transition a RUNNING task to WAITING when todos are
    incomplete — agent exited mid-task needing user input."""
    now = datetime.now(UTC).isoformat()
    task.wait_condition = json.dumps({
        'reason': (
            'Agent exited with incomplete steps — waiting for user input'
        ),
        'check_strategy': 'manual',
        'waiting_since': now,
        'last_checked_at': now,
        'max_wait_days': 30,
    })
    assert_transition(task.status, TaskStatus.WAITING)
    task.status = TaskStatus.WAITING
    task.updated_at = now
    await db.commit()
    await event_bus.emit(
        'task_update',
        {'task_id': task_id, 'status': TaskStatus.WAITING},
    )


def format_answered_questions_prefix(
    questions: list[dict], answers: dict
) -> str:
    """Render a user-turn prefix that tells the resumed agent what
    the operator answered.

    ``questions`` is the AskUserQuestion payload we stored at park
    time; ``answers`` is the ``{question_text: answer_label}`` map
    the SDK delivers (same shape as the live control_response).
    Falls back to raw JSON when the shape doesn't match.
    """
    try:
        # Expand the UI free-text sentinel onto the asked questions so
        # 'Type freely instead' answers aren't dropped on resume (#211).
        answers = expand_free_text_answers(answers, questions)
        lines = ['The operator answered your earlier AskUserQuestion:']
        q_texts = []
        for q in questions:
            q_text = q.get('question', '') if isinstance(q, dict) else str(q)
            q_texts.append(q_text)
            a_text = answers.get(q_text, '')
            lines.append(f'- {q_text} → {a_text}')
        # Surface any answers not keyed to a listed question (e.g. free
        # text submitted when the question list couldn't be recovered).
        for key, value in answers.items():
            if key not in q_texts and value:
                lines.append(f'- {value}')
        lines.append('Please continue the task using those answers.')
        return '\n'.join(lines)
    except Exception:
        return (
            'The operator answered your earlier AskUserQuestion '
            f'(answers={json.dumps(answers, ensure_ascii=False)}). '
            'Please continue the task.'
        )


async def maybe_inject_pending_answers(
    task_id: str, bundle: 'PromptBundle'
) -> 'PromptBundle':
    """Prepend operator-supplied answers to the user turn if the
    task is resuming from a WAITING-with-pending-question park.

    Clears ``wait_condition`` after reading so a future re-queue
    doesn't re-inject stale answers. Returns the bundle unchanged
    when there are no answers to inject.
    """
    async with async_session() as db:
        task = await db.get(Task, task_id)
        if not task or not task.wait_condition:
            return bundle
        try:
            cond = json.loads(task.wait_condition)
        except (json.JSONDecodeError, TypeError):
            return bundle
        answers = cond.get('answers')
        pq = cond.get('pending_question') or {}
        if not answers or not pq:
            return bundle
        prefix = format_answered_questions_prefix(
            pq.get('questions', []), answers
        )
        task.wait_condition = None
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
    return PromptBundle(
        prompt=f'{prefix}\n\n{bundle.prompt}',
        system_extra=bundle.system_extra,
        mode=bundle.mode,
    )


async def park_waiting_for_text_only_response(
    task: Task, db: AsyncSession, task_id: str
):
    """Park a task in WAITING when the agent exited with text-only
    output (no tool_use blocks). Common shape: agent wrote a question
    in prose instead of calling AskUserQuestion. Operator answers via
    the UI; the task resumes through `claude --resume` with answers
    injected as the next user turn. Schema matches
    ``mark_waiting_for_input`` so consumers see consistent fields.
    """
    now = datetime.now(UTC).isoformat()
    task.wait_condition = json.dumps({
        'reason': (
            'Agent exited with text-only output (no tool use) — '
            'likely asked a question in prose; awaiting operator '
            'input'
        ),
        'check_strategy': 'manual',
        'waiting_since': now,
        'last_checked_at': now,
        'max_wait_days': 30,
    })
    assert_transition(task.status, TaskStatus.WAITING)
    task.status = TaskStatus.WAITING
    task.updated_at = now
    await db.commit()
    await event_bus.emit(
        'task_update',
        {'task_id': task_id, 'status': TaskStatus.WAITING},
    )


async def park_waiting_for_pending_question(
    task: Task,
    db: AsyncSession,
    task_id: str,
    pending: dict,
):
    """Park a task in WAITING with an outstanding AskUserQuestion.

    `pending` maps ``request_id -> {'request_id', 'questions': [...]}``
    (the hook's `_pending_questions` snapshot). We keep only the
    first request since the SDK only has one pending per turn.

    The operator answers via the same ``/questions/answer`` endpoint
    used for live sessions; on dead session the router persists the
    answers into ``wait_condition.answers`` and re-queues the task,
    which resumes via ``claude --resume <session_id>``.
    """
    req_id = next(iter(pending))
    questions = pending[req_id].get('questions', [])
    now = datetime.now(UTC).isoformat()
    task.wait_condition = json.dumps({
        'reason': 'Agent asked a question; awaiting operator input',
        'check_strategy': 'manual',
        'waiting_since': now,
        'last_checked_at': now,
        'max_wait_days': 7,
        'pending_question': {
            'request_id': req_id,
            'questions': questions,
        },
    })
    assert_transition(task.status, TaskStatus.WAITING)
    task.status = TaskStatus.WAITING
    task.updated_at = now
    await db.commit()
    await event_bus.emit(
        'task_update',
        {'task_id': task_id, 'status': TaskStatus.WAITING},
    )


def coalesce_history(messages) -> list[dict]:
    """Build alternating user/assistant history from TaskMessages.

    Filters to user/assistant roles only, then merges consecutive
    same-role messages (which can happen when the agent sends
    multiple assistant turns). The Claude API requires strictly
    alternating roles.
    """
    result: list[dict] = []
    for m in messages:
        if m.role not in ('user', 'assistant'):
            continue
        if result and result[-1]['role'] == m.role:
            result[-1]['content'] += '\n\n' + m.content
        else:
            result.append({'role': m.role, 'content': m.content})
    return result


def format_trigger_context(
    strategy: str,
    woken_by: str,
    trigger_data,
) -> str:
    """Format trigger data into context for the agent."""
    if woken_by == 'user':
        msg = 'The user has manually woken this task.'
        if trigger_data:
            msg += f'\nUser message: {trigger_data}'
        return msg

    if strategy == 'email' and isinstance(trigger_data, dict):
        emails = trigger_data.get('emails', [])
        if emails:
            lines = ['New email(s) matching your wait keywords:']
            for e in emails:
                lines.append(
                    f'- From: {e.get("sender", "?")}, '
                    f'Subject: {e.get("subject", "?")}\n'
                    f'  Body: {e.get("body", "")[:500]}'
                )
            return '\n'.join(lines)

    if trigger_data:
        return f'Trigger data: {json.dumps(trigger_data, indent=2)}'

    return 'The wait condition has been resolved.'


async def sync_store_metadata(store_id: str, db: AsyncSession):
    """Sync metadata.json → DB platform_countries after task."""
    try:
        store = await db.get(Store, store_id)
        if not store:
            return
        slug = _store_slug(store.name, store.id)
        meta_path = workspace_manager.root / 'stores' / slug / 'metadata.json'
        if not meta_path.exists():
            return
        raw = meta_path.read_text(encoding='utf-8')
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning('metadata.json not a dict for %s', slug)
            return
        file_platforms = data.get('platforms', {})
        if not isinstance(file_platforms, dict):
            logger.warning(
                'metadata.json platforms not a dict for %s',
                slug,
            )
            return

        # Sanitize + merge
        existing = (
            json.loads(store.platform_countries)
            if store.platform_countries
            else {}
        )
        count = 0
        for platform, countries in file_platforms.items():
            if not isinstance(countries, list):
                continue
            p = platform.strip().lower()
            if not p or count >= 50:
                break
            cs = []
            for c in countries:
                if isinstance(c, str):
                    cv = c.strip().upper()
                    if cv and len(cs) < 100:
                        cs.append(cv)
            existing.setdefault(p, [])
            existing[p] = sorted(set(existing[p]) | set(cs))
            count += 1

        store.platform_countries = json.dumps(existing)
        await db.commit()
    except Exception as e:
        logger.warning(
            'Metadata sync failed for store %s: %s',
            store_id,
            e,
        )


_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED}


async def maybe_wait_for_children(
    task: 'Task',
    task_id: str,
    db: AsyncSession,
) -> bool:
    """If task has pending children, transition to WAITING.

    Returns True if the task was moved to WAITING (caller should
    ``return`` instead of completing).  Returns False if there
    are no children or all children are already terminal.
    """
    result = await db.execute(
        select(Task).where(Task.parent_task_id == task_id)
    )
    kids = list(result.scalars().all())
    if not kids:
        return False

    pending = [k for k in kids if k.status not in _TERMINAL]
    if not pending:
        # All children done — aggregate results into parent
        _aggregate_child_results(task, kids)
        return False

    # Children still running — wait
    task.wait_condition = json.dumps({'strategy': 'children'})
    assert_transition(task.status, TaskStatus.WAITING)
    task.status = TaskStatus.WAITING
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await event_bus.emit(
        'task_update',
        {'task_id': task_id, 'status': TaskStatus.WAITING},
    )
    return True


async def check_parent_completion(
    task: 'Task',
    db: AsyncSession,
) -> None:
    """If task's parent is WAITING on children, check siblings.

    When ALL siblings are terminal, auto-complete the parent
    with aggregated results.
    """
    if not task.parent_task_id:
        return
    parent = await db.get(Task, task.parent_task_id)
    if not parent or parent.status != TaskStatus.WAITING:
        return
    try:
        cond = json.loads(parent.wait_condition or '{}')
    except (json.JSONDecodeError, TypeError):
        return
    if cond.get('strategy') != 'children':
        return

    result = await db.execute(
        select(Task).where(Task.parent_task_id == task.parent_task_id)
    )
    siblings = list(result.scalars().all())
    if not all(k.status in _TERMINAL for k in siblings):
        return

    # All children done — complete parent
    _aggregate_child_results(parent, siblings)
    assert_transition(parent.status, TaskStatus.COMPLETED)
    parent.status = TaskStatus.COMPLETED
    parent.completed_at = datetime.now(UTC).isoformat()
    parent.updated_at = datetime.now(UTC).isoformat()
    parent.wait_condition = None
    await db.commit()
    await event_bus.emit(
        'task_update',
        {
            'task_id': parent.id,
            'status': TaskStatus.COMPLETED,
            'result': parent.result,
        },
    )


async def reopen_parent_if_child_active(
    task: 'Task',
    db: AsyncSession,
) -> None:
    """If a child leaves terminal state, revert parent to WAITING.

    When a completed/failed child gets a follow-up message and
    re-enters RUNNING, the parent (which auto-completed) must
    go back to WAITING until the child finishes again.
    """
    if not task.parent_task_id:
        return
    parent = await db.get(Task, task.parent_task_id)
    if not parent or parent.status != TaskStatus.COMPLETED:
        return
    # Only revert if parent was auto-completed by children strategy
    # (indicated by parent having children at all)
    result = await db.execute(
        select(Task).where(Task.parent_task_id == task.parent_task_id)
    )
    kids = list(result.scalars().all())
    if not kids:
        return
    # At least one child is non-terminal → revert parent
    if any(k.status not in _TERMINAL for k in kids):
        assert_transition(parent.status, TaskStatus.WAITING)
        parent.status = TaskStatus.WAITING
        parent.wait_condition = json.dumps({'strategy': 'children'})
        parent.completed_at = None
        parent.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': parent.id, 'status': TaskStatus.WAITING},
        )


def _aggregate_child_results(parent: 'Task', children: list['Task']) -> None:
    """Build a summary of child task results into parent.result."""
    lines: list[str] = []
    for k in children:
        icon = '\u2713' if k.status == TaskStatus.COMPLETED else '\u2717'
        title = k.title or k.id[:8]
        detail = (k.result or k.error or '')[:200]
        lines.append(f'{icon} {title}: {detail}')
    parent.result = '\n'.join(lines)
