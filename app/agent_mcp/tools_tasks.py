"""The task tools: list, follow, question, review, message, stop,
retry. One module per tool family, split out of ``tools.py`` when
that module passed the repo's 800-line limit; the contract they
follow is documented there. ``create_task`` lives in
``tools_task_create.py`` (this module passed the limit again when
attachments landed).
"""

import asyncio
import time
from typing import Any

from app import database
from app.agent_mcp.answers import UnknownQuestionKey, map_answers
from app.agent_mcp.files import (
    as_upload_file,
    decode_upload,
)
from app.agent_mcp.lifecycle import schedule_fired
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_WAIT_SECONDS,
    MAX_LIST_LIMIT,
    MAX_WAIT_SECONDS,
    NEEDS_OPERATOR_STATUSES,
    TERMINAL_STATUSES,
    WAIT_POLL_SECONDS,
    WAITING_STATUSES,
    McpContext,
    ToolError,
    call_route,
    callers_default_profile,
    guidance_for,
    pending_image_for,
    pending_question_for,
    require,
    stored_pending_question,
    task_summary,
    trim,
)
from app.agent_mcp.tools_task_files import compact_image_card
from app.models.task import Task
from app.routers import (
    tasks as tasks_route,
    tasks_conversation as conversation_route,
    tasks_files as tasks_files_route,
)
from app.routers.tasks_conversation import RetryRequest
from app.task_states import TaskStatus


@tool(
    'list_tasks',
    'List tasks, newest first. Filter by store and/or status.',
    schema({
        'store_id': {
            'type': ['string', 'null'],
            'description': (
                'Only tasks for this store. Omit for every task. '
                'Use "__none__" for tasks with no store.'
            ),
        },
        'status': {
            'type': ['string', 'null'],
            'enum': [
                'pending',
                'queued',
                'designing',
                'planned',
                'running',
                'waiting',
                'completed',
                'failed',
            ],
            'description': 'Only tasks in this status.',
        },
        'limit': {
            'type': 'integer',
            'minimum': 1,
            'maximum': MAX_LIST_LIMIT,
            'description': (
                'Maximum number of tasks to return '
                f'(default {DEFAULT_LIST_LIMIT}, max {MAX_LIST_LIMIT}).'
            ),
        },
    }),
)
async def list_tasks(ctx: McpContext, args: dict) -> dict:
    limit = args.get('limit') or DEFAULT_LIST_LIMIT
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    tasks = await call_route(
        tasks_route.list_tasks,
        store_id=args.get('store_id') or None,
        status=args.get('status') or None,
        limit=limit,
        db=ctx.db,
        _user=ctx.user,
    )
    summaries = []
    for task in tasks:
        summary = task_summary(task)
        summary.update(await guidance_for(ctx, task))
        summaries.append(summary)
    return {'tasks': summaries}


@tool(
    'get_task',
    'Get one task: status, plan, result, error, any pending question, '
    'and the last few steps. Long fields are truncated.',
    schema(
        {'task_id': {'type': 'string', 'description': 'The task id.'}},
        required=['task_id'],
    ),
)
async def get_task(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    task = await call_route(
        tasks_route.get_task, task_id, db=ctx.db, _user=ctx.user
    )
    steps = await call_route(
        tasks_route.get_task_steps, task_id, db=ctx.db, _user=ctx.user
    )
    pending = await pending_question_for(ctx, task)
    guidance = await guidance_for(ctx, task)
    plan, plan_cut = trim(task.plan)
    result, result_cut = trim(task.result)
    description, desc_cut = trim(task.description)
    return {
        **guidance,
        'id': task.id,
        'title': task.title,
        'store_id': task.store_id,
        'schedule_id': task.schedule_id,
        'plan_mode': task.plan_mode,
        'ai_profile_id': task.ai_profile_id,
        'description': description,
        'plan': plan,
        'result': result,
        'error': task.error,
        'error_category': task.error_category,
        'pending_question': (
            {
                'request_id': pending.get('request_id'),
                'questions': pending.get('questions', []),
            }
            if pending
            else None
        ),
        'steps': [
            {
                'step_index': step.step_index,
                'name': step.name,
                'action_type': step.action_type,
                'status': step.status,
                'error': step.error,
            }
            for step in steps[-5:]
        ],
        'truncated': {
            'description': desc_cut,
            'plan': plan_cut,
            'result': result_cut,
        },
        'created_at': task.created_at,
        'updated_at': task.updated_at,
    }


@tool(
    'wait_task',
    'Wait for a task to need you. Returns as soon as the agent asks a '
    'question, a plan is ready for review, the task finishes '
    '(completed/failed), or it is left waiting — or when the timeout '
    'expires, whichever comes first. Poll with it instead of sleeping.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'timeout_seconds': {
                'type': 'integer',
                'minimum': 1,
                'maximum': MAX_WAIT_SECONDS,
                'description': (
                    'How long to wait, in seconds (default 45, max 120).'
                ),
            },
        },
        required=['task_id'],
    ),
)
async def wait_task(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    timeout = args.get('timeout_seconds') or DEFAULT_WAIT_SECONDS
    timeout = max(1, min(int(timeout), MAX_WAIT_SECONDS))

    # End the request-scoped transaction before waiting. The auth check
    # left a read transaction open, and SQLite (this server's engine)
    # blocks every writer while a reader holds one — a two-minute wait
    # would freeze the whole server. Rolling back releases the
    # connection; the poll loop below uses its own short-lived
    # sessions, so nothing is held between polls either.
    await ctx.db.rollback()

    async with database.async_session() as db:
        task = await db.get(Task, task_id)
        if not task:
            raise ToolError(f'Task not found: {task_id}')
        start_status = task.status

    deadline = time.monotonic() + timeout
    status = start_status
    pending = None
    pending_image = None
    reason = 'timeout'
    client_gone = False
    # The task we already read. The loop re-reads it (the status may
    # move under us), but the FIRST read is what the result is built
    # from if the wait ends before the first poll — a client that
    # disconnected. Without it the result would carry no guidance at
    # all, which is the one result shape every other path guarantees.
    final_task = task
    # Wake on news, not on motion: a question, a finished task, a plan
    # ready for review, or a task parked on a condition. The transient
    # statuses (pending → queued → running) are progress; returning on
    # them would turn a long-poll into a busy loop.
    while True:
        # The budget is checked BEFORE the iteration's work, not after
        # it: one poll cycle costs a DB read plus two route probes, and
        # with the check last, that work ran past the deadline before
        # the loop noticed (a 1 s wait could answer at 1.6 s+). With it
        # first, the ceiling is honest — the caller re-issues the wait
        # and any news that landed inside the final interval answers
        # instantly on that next call.
        if time.monotonic() >= deadline:
            reason = 'timeout'
            break
        # A dropped client ends the wait instead of polling on: the
        # response will never be read, and the poll would otherwise
        # hold a request slot for up to the full timeout. The reason
        # stays one of the five the contract defines — the client is
        # gone, so there is nobody to tell — and the flag says why.
        if await ctx.request.is_disconnected():
            client_gone = True
            reason = 'timeout'
            break
        async with database.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                raise ToolError(f'Task not found: {task_id}')
            status = task.status
        final_task = task
        pending = await pending_question_for(ctx, task)
        if pending:
            reason = 'question'
            break
        # An image confirm card parks the task agent mid-RUN: the
        # status stays 'running' while a decision is owed, so the wait
        # must look for the card itself (same source guidance uses).
        pending_image = await pending_image_for(ctx, task)
        if pending_image:
            reason = 'needs_operator'
            break
        if status in TERMINAL_STATUSES:
            reason = 'terminal'
            break
        if status in NEEDS_OPERATOR_STATUSES and not schedule_fired(task):
            # `planned` needs an operator — EXCEPT on a schedule-fired
            # task, whose plan is the schedule's frozen one: the queue
            # auto-executes it, so the state is pre-execution, not a
            # checkpoint. Reporting needs_operator there would steer
            # the agent into review_plan, and rejecting that plan
            # swaps in an auto-approved revision (see
            # app/agent_mcp/lifecycle.py _SCHEDULE_FIRED_MEANINGS).
            reason = 'needs_operator'
            break
        if status in WAITING_STATUSES:
            reason = 'waiting'
            break
        # Never sleep past the caller's budget: a fixed interval with
        # a fraction of it left pushed the next deadline check (and
        # its work) past the ceiling.
        remaining = deadline - time.monotonic()
        await asyncio.sleep(max(0.05, min(WAIT_POLL_SECONDS, remaining)))

    guidance = await guidance_for(ctx, final_task)
    return {
        **guidance,
        'task_id': task_id,
        'status': status,
        'changed': status != start_status,
        'timed_out': reason == 'timeout',
        'reason': reason,
        'client_disconnected': client_gone,
        'pending_question': (
            {
                'request_id': pending.get('request_id'),
                'questions': pending.get('questions', []),
            }
            if pending
            else None
        ),
        # WHAT the needs_operator reason is about, when it is a card
        # and not a plan: kind=image_confirm carries the proposal
        # (image_request(get) has the whole card). A plan review sets
        # no pending block — reason=needs_operator with a planned
        # status already says which checkpoint it is.
        'pending': (
            {
                'kind': 'image_confirm',
                **compact_image_card(pending_image),
            }
            if pending_image
            else None
        ),
    }


# How long review_plan(approve) waits for the approval to take effect.
# The route launches the executor in the BACKGROUND, and the
# PLANNED → RUNNING transition happens in there — so when the route
# returns, the task is still 'planned'.
APPROVAL_SETTLE_SECONDS = 5.0
APPROVAL_POLL_SECONDS = 0.05


async def _status_after_approval(ctx: McpContext, task_id: str) -> str:
    """The task's status once the approval has taken effect.

    ``execute_plan`` hands the executor to the background, so the task
    is still 'planned' when the route returns. An agent that called
    ``wait_task`` straight afterwards would be told "a plan is ready
    for review" about the plan it had JUST approved — a wrong answer
    that sends it back to a checkpoint it already passed.

    So the approval waits (bounded) for the task to leave 'planned'
    and reports where it got to. If it does not move, the tool says
    so rather than pretending.
    """
    # Release the request session before polling: the route's work is
    # committed, and a second session on the same connection while the
    # request session still holds it is how two sessions end up
    # fighting over one connection (the wait_task poll does the same).
    await ctx.db.rollback()
    deadline = time.monotonic() + APPROVAL_SETTLE_SECONDS
    while True:
        async with database.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                raise ToolError(f'Task not found: {task_id}')
            status = task.status
        if status != TaskStatus.PLANNED or time.monotonic() >= deadline:
            return status
        await asyncio.sleep(APPROVAL_POLL_SECONDS)


@tool(
    'get_pending_questions',
    'Get the question the agent is waiting on, if any. Pass the answers '
    'to answer_question.',
    schema(
        {'task_id': {'type': 'string', 'description': 'The task id.'}},
        required=['task_id'],
    ),
)
async def get_pending_questions(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    live = await call_route(
        conversation_route.get_pending_questions, task_id, _user=ctx.user
    )
    if live.get('pending'):
        return {
            'pending': True,
            'source': 'live',
            'request_id': live.get('request_id'),
            'questions': live.get('questions', []),
        }
    task = await ctx.db.get(Task, task_id)
    if not task:
        raise ToolError(f'Task not found: {task_id}')
    parked = stored_pending_question(task)
    if parked:
        return {
            'pending': True,
            'source': 'parked',
            'request_id': parked.get('request_id'),
            'questions': parked.get('questions', []),
        }
    return {'pending': False}


@tool(
    'answer_question',
    'Answer the question the agent asked. Key each answer by the '
    "question's full text (the header or a 1-based index also work) — "
    'the server maps it onto the text the agent reads.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'answers': {
                'type': 'object',
                'description': (
                    'Question key -> answer. The answer is the label of '
                    'the chosen option, or free text.'
                ),
                'additionalProperties': {'type': 'string'},
            },
            'request_id': {
                'type': ['string', 'null'],
                'description': (
                    'The request id from get_pending_questions. Omit to '
                    'use the current one.'
                ),
            },
        },
        required=['task_id', 'answers'],
    ),
)
async def answer_question(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    raw_answers = args.get('answers')
    if not isinstance(raw_answers, dict) or not raw_answers:
        raise ToolError('answers is required and must be a non-empty object')

    task = await ctx.db.get(Task, task_id)
    if not task:
        raise ToolError(f'Task not found: {task_id}')

    live = await call_route(
        conversation_route.get_pending_questions, task_id, _user=ctx.user
    )
    questions = list(live.get('questions') or [])
    request_id = args.get('request_id') or live.get('request_id')
    if not questions:
        parked = stored_pending_question(task)
        if parked:
            questions = list(parked.get('questions') or [])
            request_id = request_id or parked.get('request_id')
    if not request_id:
        raise ToolError(
            'No pending question for this task — call '
            'get_pending_questions first.'
        )

    try:
        mapped = map_answers(raw_answers, questions)
    except UnknownQuestionKey as exc:
        raise ToolError(str(exc)) from exc

    result = await call_route(
        conversation_route.answer_question,
        task_id,
        {'request_id': request_id, 'answers': mapped},
        db=ctx.db,
        _user=ctx.user,
    )
    fresh = await ctx.db.get(Task, task_id)
    return {
        **(await guidance_for(ctx, fresh) if fresh else {}),
        'ok': True,
        'request_id': request_id,
        'answers': mapped,
        'resumed': bool((result or {}).get('resumed')),
    }


@tool(
    'review_plan',
    'Approve a plan the agent produced, or reject it with feedback so '
    'the agent revises it. Only meaningful while the task is waiting '
    'for plan review. A PLAN IS A HUMAN CHECKPOINT: show the plan to '
    'your own user and approve it only with their go-ahead — unless '
    'they already told you to proceed without asking.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'approve': {
                'type': 'boolean',
                'description': (
                    'true executes the plan; false sends it back for revision.'
                ),
            },
            'feedback': {
                'type': ['string', 'null'],
                'description': (
                    'Required when rejecting: what the agent should change.'
                ),
            },
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'AI profile to use. Omitted → your default profile, '
                    'as the web UI does; pass profile_id to choose '
                    'another.'
                ),
            },
        },
        required=['task_id'],
    ),
)
async def review_plan(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    approve = args.get('approve')
    if not isinstance(approve, bool):
        raise ToolError('approve (true/false) is required')
    approved = approve

    if approved:
        result = await call_route(
            tasks_route.execute_plan, task_id, db=ctx.db, _user=ctx.user
        )
        # Return only once the task has actually left 'planned' (see
        # _status_after_approval) — an approval whose effect is still
        # pending must not look like a plan awaiting review.
        status = await _status_after_approval(ctx, task_id)
        fresh = await ctx.db.get(Task, task_id)
        approved_result = {'action': 'approve', 'status': status}
        if status == TaskStatus.PLANNED:
            approved_result['note'] = (
                'The approval was accepted but the task has not left '
                "'planned' yet — the executor is still starting it."
            )
        return {
            **(result or {}),
            **approved_result,
            # The guidance LAST: it is the task's own status read
            # fresh, and it must not be overwritten by a route's
            # older 'status' field.
            **(await guidance_for(ctx, fresh) if fresh else {}),
        }

    feedback = str(args.get('feedback') or '').strip()
    if not feedback:
        raise ToolError('feedback is required when rejecting a plan')
    body: dict[str, Any] = {
        'content': feedback,
        'profile_id': (args.get('profile_id') or callers_default_profile(ctx)),
    }
    result = await call_route(
        conversation_route.send_task_message,
        task_id,
        body,
        db=ctx.db,
        _user=ctx.user,
    )
    fresh = await ctx.db.get(Task, task_id)
    return {
        **(result or {}),
        'action': 'reject',
        **(await guidance_for(ctx, fresh) if fresh else {}),
    }


@tool(
    'send_message',
    'Send a follow-up message to a task. It reaches the running agent '
    'as its next turn; on a finished task it resumes the work, and on '
    'a waiting task it wakes it. send_message CONTINUES the same task '
    '— it keeps the plan, the transcript and the progress — so use it '
    'for a mid-way or transient error and for follow-ups; use '
    'retry_task when the approach or the inputs were wrong. '
    'WARNING: on a task parked at `planned` the message is a plan '
    'REJECTION — the text becomes the re-plan feedback (the route '
    'treats any incoming message as the reject verdict). Use '
    'review_plan there, never a clarification. '
    'attachments: files the agent should work on next (same shape and '
    'limits as create_task) — they reach the agent with this message, '
    'as an upload in the chat bar does.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'message': {
                'type': 'string',
                'description': 'The message.',
            },
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'AI profile to use. Omitted → your default profile, '
                    'as the web UI does; pass profile_id to choose '
                    'another.'
                ),
            },
            'attachments': {
                'type': 'array',
                'description': (
                    'Files to attach to this message: {filename, '
                    'mime_type, content_base64}, each up to 10 MB '
                    '(png, jpeg, gif, webp, pdf). They become visible '
                    'to the agent when the message is delivered.'
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'filename': {'type': 'string'},
                        'mime_type': {'type': 'string'},
                        'content_base64': {'type': 'string'},
                    },
                    'required': [
                        'filename',
                        'mime_type',
                        'content_base64',
                    ],
                    'additionalProperties': False,
                },
            },
        },
        required=['task_id'],
    ),
)
async def send_message(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    # The send bar sends text only, attachments only, or both — the
    # route accepts an empty content when attachment_ids is present.
    content = args.get('message')
    if not isinstance(content, str):
        content = ''
    content = content.strip()
    raw_attachments = args.get('attachments') or []
    if not isinstance(raw_attachments, list):
        raise ToolError('attachments must be an array')
    if not content and not raw_attachments:
        raise ToolError('message is required')
    uploads = [decode_upload(item) for item in raw_attachments]

    # Mirror the UI's chat flow (useChatUploads + sendChatMessage):
    # files are STAGED outside the agent's cwd first, and the message
    # carries their staged ids — the server promotes them into the
    # workspace at delivery, the one moment the agent sees them.
    staged_ids = []
    for filename, mime_type, payload in uploads:
        staged = await call_route(
            tasks_files_route.stage_task_file,
            task_id,
            as_upload_file(filename, mime_type, payload),
            _user=ctx.user,
        )
        staged_ids.append(staged.id)

    body: dict[str, Any] = {
        'content': content,
        'profile_id': (args.get('profile_id') or callers_default_profile(ctx)),
    }
    if staged_ids:
        body['attachment_ids'] = staged_ids
    result = await call_route(
        conversation_route.send_task_message,
        task_id,
        body,
        db=ctx.db,
        _user=ctx.user,
    )
    fresh = await ctx.db.get(Task, task_id)
    return {
        **(result or {'ok': True}),
        **(await guidance_for(ctx, fresh) if fresh else {}),
    }


@tool(
    'stop_task',
    'Stop the agent working on a task. The task ends as failed. On a '
    'task that is not running this is a no-op — the route still '
    'answers ok.',
    schema(
        {'task_id': {'type': 'string', 'description': 'The task id.'}},
        required=['task_id'],
    ),
)
async def stop_task(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    result = await call_route(
        tasks_route.stop_agent, task_id, db=ctx.db, _user=ctx.user
    )
    fresh = await ctx.db.get(Task, task_id)
    return {
        **(result or {'ok': True}),
        **(await guidance_for(ctx, fresh) if fresh else {}),
    }


@tool(
    'retry_task',
    'Run a task again from scratch: the plan, the transcript and the '
    'workspace are wiped and the task starts over. An omitted '
    'profile_id uses your default profile, as the web UI does. '
    'retry_task STARTS OVER — it keeps nothing of the previous run — '
    'so use send_message instead to continue the same task.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'AI profile to use. Omitted → your default profile, '
                    'as the web UI does; pass profile_id to choose '
                    'another.'
                ),
            },
        },
        required=['task_id'],
    ),
)
async def retry_task(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    # The UI always sends the picker's profile on retry; an omitted
    # profile_id resolves to the caller's default, exactly like the
    # picker's initial value. See callers_default_profile.
    body = RetryRequest(
        profile_id=args.get('profile_id') or callers_default_profile(ctx)
    )
    result = await call_route(
        conversation_route.retry_task, task_id, body, db=ctx.db, _user=ctx.user
    )
    fresh = await ctx.db.get(Task, task_id)
    return {
        **(result or {'ok': True}),
        **(await guidance_for(ctx, fresh) if fresh else {}),
    }


@tool(
    'delete_task',
    'Delete a task and everything under it: its steps, messages, '
    'files and on-disk workspace. A running task must be stopped '
    'first (stop_task). This is the same delete the web UI performs, '
    'so the same rules apply.',
    schema(
        {'task_id': {'type': 'string', 'description': 'The task id.'}},
        required=['task_id'],
    ),
)
async def delete_task(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    result = await call_route(
        tasks_route.delete_task, task_id, db=ctx.db, _user=ctx.user
    )
    return result or {'ok': True}
