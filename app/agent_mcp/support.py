"""Shared runtime for the MCP tools: the call context and the helpers
every tool adapter reaches for (argument reading, trimming, summaries,
and the one chokepoint every route call goes through).

Split out of ``tools.py`` when that module passed the repo's 800-line
limit. No tool is registered here.
"""

from dataclasses import dataclass
import inspect
import json

import fastapi.params
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.agent_mcp.lifecycle import schedule_fired, task_guidance
from app.browser.wrapper import store_slug
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.routers import (
    tasks_conversation as conversation_route,
    vision as vision_route,
)
from app.schemas.task import TaskResponse
from app.task_delete import active_in_subtree, collect_descendants
from app.task_states import ACTIVE, TaskStatus

# Longest text an agent is handed for a single field.
MAX_TEXT_CHARS = 4000
MAX_FILE_CHARS = 8000
# Longest summary of a task result in a LIST response.
MAX_SUMMARY_CHARS = 300
# list_tasks: the limit used when the caller sends none, and its
# ceiling. A bare "what is there?" call must not return the entire
# task history — on a long-lived server that is thousands of rows
# straight into an agent's context.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
# wait_task: default and ceiling, in seconds.
DEFAULT_WAIT_SECONDS = 45
MAX_WAIT_SECONDS = 120
WAIT_POLL_SECONDS = 1.0

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED}
# A plan is ready and waiting for review_plan — the ONE state that
# needs the operator to look at a plan.
NEEDS_OPERATOR_STATUSES = {TaskStatus.PLANNED}
# The task is parked: on child tasks, or on a condition it is waiting
# for. send_message wakes it.
WAITING_STATUSES = {TaskStatus.WAITING}
# The statuses a DELETE refuses while the task itself is in one.
# (The other half of that guard — a running task somewhere in the
# SUBTREE — is asked per task in subtree_has_active.)
_ACTIVE_STATUS_VALUES = {status.value for status in ACTIVE}
# The statuses wait_task returns INSTANTLY for: it wakes on a
# question, a finished task, a plan ready for review, or a task
# parked on a condition. The lifecycle table names wait_task only
# where waiting actually waits — the two are pinned together by
# tests/unit/test_agent_mcp_lifecycle.py. Built from the SAME sets the
# loop breaks on, so a new instant status cannot drift away from the
# table. ONE exception, mirrored in both places: a schedule-FIRED task
# at `planned` (schedule_id set, not is_plan_only) is PRE-execution —
# the queue auto-runs its frozen plan — so the poll does NOT return
# and the table still names wait_task there (see
# app/agent_mcp/lifecycle.py _SCHEDULE_FIRED_MEANINGS).
WAIT_TASK_INSTANT_STATUSES = frozenset(
    TERMINAL_STATUSES | NEEDS_OPERATOR_STATUSES | WAITING_STATUSES
)


def wait_task_instant(task) -> bool:
    """Whether wait_task returns immediately for this task's status.

    The loop's break condition, in one place: the sets above, minus
    the schedule-fired `planned` exception (that state is a
    pre-execution park, not an operator checkpoint — the wait should
    block until the queue starts the frozen plan).
    """
    if task is None:
        return False
    if task.status in NEEDS_OPERATOR_STATUSES and schedule_fired(task):
        return False
    return task.status in WAIT_TASK_INSTANT_STATUSES


class ToolError(Exception):
    """A tool-level failure.

    Reported to the agent as a successful JSON-RPC response carrying
    ``isError: true`` — the tool ran, its answer is "this did not
    work". A JSON-RPC error is reserved for protocol problems
    (unknown tool, malformed arguments).
    """


@dataclass
class McpContext:
    """What a tool needs: who is calling, a database session, and the
    HTTP request (a long-running tool checks ``request.is_disconnected``
    so a dropped client ends the wait instead of polling on)."""

    user: User
    db: AsyncSession
    request: Request


def trim(
    text: str | None, limit: int = MAX_TEXT_CHARS
) -> tuple[str | None, bool]:
    """Cut *text* to *limit* characters, marking that it was cut."""
    if text is None:
        return None, False
    if len(text) <= limit:
        return text, False
    return (
        text[:limit] + f'\n\n[truncated: {len(text)} characters total, '
        f'showing the first {limit}]',
        True,
    )


def require(args: dict, key: str) -> str:
    """Read a required string argument."""
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f'{key} is required')
    return value.strip()


def derive_title(description: str) -> str:
    """A task-list label derived from the task instruction.

    The REST contract requires a title, but an external agent thinks
    in instructions, not labels — so the title is optional here and
    falls back to the first line of the description.
    """
    for line in description.splitlines():
        stripped = line.strip().lstrip('#').strip()
        if stripped:
            return stripped[:100]
    return description.strip()[:100]


def stored_pending_question(task: Task | None) -> dict | None:
    """The pending question parked on a WAITING task, if any.

    A live session holds its pending question in memory; when the
    agent's process dies the question is parked on the task row so a
    later answer can resume it (see ``app/task_runner``).
    """
    if not task or not task.wait_condition:
        return None
    try:
        condition = json.loads(task.wait_condition)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(condition, dict):
        return None
    pending = condition.get('pending_question')
    if isinstance(pending, dict) and pending.get('request_id'):
        return pending
    return None


async def store_slug_for(ctx: McpContext, store_id: str) -> str:
    """The on-disk slug of a store (its ``stores/<slug>/`` folder)."""
    store = await ctx.db.get(Store, store_id)
    if not store:
        raise ToolError(f'Store not found: {store_id}')
    return store_slug(store.name, store.id)


def callers_default_profile(ctx: McpContext) -> str:
    """The profile the web UI would run under — the caller's default.

    The UI's profile picker starts from the signed-in user's
    ``default_profile_id`` and the composer ALWAYS sends it
    (``frontend/src/handlers/retryTask.ts`` and ``sendChatMessage.ts``
    post ``profile_id: <picker value>`` on every retry and follow-up).
    Mirroring that means an omitted ``profile_id`` resolves to this
    value — not to "whatever the task ran under last", which silently
    diverges from what a person clicking the same button would get.
    """
    return ctx.user.default_profile_id or 'default'


class RouteCallError(RuntimeError):
    """An adapter tried to call a route it cannot satisfy.

    Raised by :func:`call_route` — a wiring bug in the tool layer, not
    a tool failure, so it surfaces as an internal error (logged) rather
    than a plausible-looking tool message.
    """


def call_route(fn, *args, **kwargs):
    """Call a FastAPI route handler with every marker param supplied.

    The adapters call route functions directly (that is the design: one
    code path with the web UI). But a direct call bypasses FastAPI's
    dependency injection: if someone later adds a ``Depends(...)`` or
    ``Query(...)`` parameter to a route, the adapter's call would not
    fail — the parameter would silently receive the MARKER OBJECT as
    its value, and break somewhere far away, or worse, keep "working"
    while reading a default the caller never chose.

    So every adapter call goes through here, which inspects the
    signature and refuses the call when a parameter whose default is a
    FastAPI marker (or has no default at all) is not supplied. Plain
    defaults (``include_archived: bool = True``) are the route's own
    business and are honoured.

    The markers are ``Param`` (Query/Header/Path/Cookie), ``Depends``
    and ``Body`` — three separate classes, not one hierarchy:
    ``Depends`` is not a ``Param`` and ``Body`` is neither (Form and
    File subclass Body). All three are checked, or a route that grows
    a body parameter would slip past.
    """
    signature = inspect.signature(fn)
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError as exc:
        raise RouteCallError(
            f'{fn.__module__}.{fn.__qualname__} cannot be called with '
            f'these arguments: {exc}'
        ) from exc
    supplied = set(bound.arguments)
    for name, param in signature.parameters.items():
        if name in supplied or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        default = param.default
        if default is inspect.Parameter.empty:
            raise RouteCallError(
                f'{fn.__module__}.{fn.__qualname__} requires '
                f'{name!r}, which the adapter did not supply'
            )
        if isinstance(
            default,
            fastapi.params.Param | fastapi.params.Depends | fastapi.params.Body,
        ):
            raise RouteCallError(
                f'{fn.__module__}.{fn.__qualname__} takes {name!r} '
                f'from FastAPI ({default!r}); the adapter must supply '
                'an explicit value'
            )
    return fn(*args, **kwargs)


async def pending_question_for(ctx: McpContext, task) -> dict | None:
    """The question the agent is waiting on, live or parked — ONE source.

    A live session holds its question in memory; when the agent's
    process dies the question is parked on the task row. Every tool
    that reports a task asks HERE, so they cannot disagree about
    whether a question is outstanding — and ``wait_task``'s wake
    condition ("a question appeared") is the same fact.
    """
    if task is None:
        return None
    live = await call_route(
        conversation_route.get_pending_questions,
        task.id,
        _user=ctx.user,
    )
    if live.get('pending'):
        return {
            'request_id': live.get('request_id'),
            'questions': live.get('questions', []),
        }
    parked = stored_pending_question(task)
    if parked:
        return {
            'request_id': parked.get('request_id'),
            'questions': parked.get('questions', []),
        }
    return None


async def pending_image_for(ctx: McpContext, task) -> dict | None:
    """The image-confirm card parked on a task, if any — ONE source.

    The task agent's generate call blocks on a future until a human
    confirms or cancels (``app/routers/vision.py``); the card lives in
    memory and is recoverable through the same route the UI uses on
    reconnect (``GET /image/pending``). Every tool that reports a task
    asks here, so guidance, ``wait_task`` and ``image_request``
    cannot disagree about whether a card is showing.
    """
    if task is None:
        return None
    pending = await call_route(
        vision_route.get_pending_image_request,
        task.id,
        current_user=ctx.user,
    )
    return pending if pending.get('pending') else None


async def subtree_has_active(ctx: McpContext, task) -> bool:
    """Whether ANY task in this task's subtree is ACTIVE.

    The DELETE guard is a property of the TREE, not of the row:
    ``app/task_delete.py`` refuses while any task in the subtree is
    ACTIVE ("stop them first"). A parent that failed while a child
    still runs is a real shape (fanout batches, sub-task trees), and
    offering ``delete_task`` there promises a call the route refuses.

    The query is the ROUTE's own, so the guidance and the route
    cannot disagree about whether a delete is allowed.

    A task that is ACTIVE itself answers True without a query — its
    own status already blocks the delete.
    """
    if task.status in _ACTIVE_STATUS_VALUES:
        return True
    ids = await collect_descendants(ctx.db, task.id)
    if len(ids) == 1:
        # No descendants — the root's own status already answered it.
        return False
    return bool(await active_in_subtree(ctx.db, ids))


async def guidance_for(ctx: McpContext, task) -> dict:
    """The status/meaning/next-actions block every task result carries.

    ``status``, ``status_meaning`` and ``next_actions`` — the
    lifecycle table, with the pending-question flag resolved from
    the same source ``wait_task`` uses, the pending-image flag from
    the vision route's own recover-card endpoint, and the
    delete-allowed flag resolved with the delete route's own subtree
    query.

    A task that is gone (deleted mid-call) has no guidance to give:
    the empty block is the honest answer, and the callers that know
    the task is missing simply omit it.
    """
    if task is None:
        return {}
    pending = await pending_question_for(ctx, task)
    pending_image = await pending_image_for(ctx, task)
    return task_guidance(
        task,
        has_pending_question=pending is not None,
        has_active_descendant=await subtree_has_active(ctx, task),
        has_pending_image=pending_image is not None,
    )


def task_summary(task: TaskResponse) -> dict:
    """A list-sized view of a task — no plan, no result body."""
    result, _ = trim(task.result, MAX_SUMMARY_CHARS)
    return {
        'id': task.id,
        'title': task.title,
        'status': task.status,
        'store_id': task.store_id,
        'plan_mode': task.plan_mode,
        'ai_profile_id': task.ai_profile_id,
        'result_summary': result,
        'error': task.error,
        'created_at': task.created_at,
        'updated_at': task.updated_at,
    }
