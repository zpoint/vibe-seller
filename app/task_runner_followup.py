"""Follow-up agent spawn helper (off the request path).

Extracted from ``routers/tasks_conversation.py`` (which owns the HTTP
handlers and the fresh-session context-rot guard) so the router stays
within the module line cap. Sibling of ``task_runner_auto`` /
``task_runner_exec`` — this is the lifecycle piece for user-initiated
follow-up turns.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging

from app.ai.claude_backend_manager import agent_manager
from app.ai.external_config import (
    ExternalConfigOverrideError,
    assert_profile_compatible,
)
from app.browser.manager import store_slug as _store_slug
from app.database import async_session
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.task_runner_auto import finalize_followup_session
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)


async def spawn_followup_agent(
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
    resume: bool = True,
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
                resume=resume,
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
                # The conversation router already persisted the user's
                # message before spawning us — persisting the prompt
                # again in AgentSession.start() duplicated every
                # follow-up message (fresh-session and dead-session
                # resume paths both start with empty message_history).
                persist_prompt=False,
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
