"""Shared agent-session lifecycle helpers.

Used by every task orchestrator (`auto_run_task`,
`execute_planned_task`, `execute_woken_task`,
`finalize_followup_session`) to wait for a session to end and
transparently retry-without-resume when Claude Code rejects a stale
``--resume`` session_id.

Single owner per task: by living in the orchestrator's coroutine
(not in `claude_backend_manager`), the wait + retry + finalize
sequence runs sequentially in one coroutine, eliminating the race
where the manager's hidden retry could either miss a finalize or
let the orchestrator finalize on prior-run residue.
"""

import asyncio
from datetime import UTC, datetime
import logging

from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.models.task import Task

logger = logging.getLogger(__name__)


async def _wait_for_session_end(task_id: str, my_session) -> bool:
    """Block until `my_session` finishes (or is superseded).

    Event-driven — does not poll `is_running()` or sleep on a timer.
    Waits on two `asyncio.Event`s on the session object:

    * ``done`` — idempotent end-of-session signal. Typically set
      from `_stream_output`'s ``finally`` (real backend) or
      `_do_work`'s ``finally`` (FakeAgent), with defensive fallback
      sets in both backends' ``stop()`` paths. Multiple sets are
      safe — waiters only observe the first.
    * ``plan_saved_event`` — interactive plan mode only; set when
      the agent commits a plan and is waiting for user approval.
      Breaking out here hands ownership to ``execute_planned_task``
      while the session stays alive.

    The previous 1-second polling loop exposed a race under load:
    a waiter could break via the ``is_running()`` path (without
    flagging ``session_replaced``) between the subprocess dying and
    a retry registering a fresh session under the same ``task_id``,
    then fall through and clobber the retry's state.  Collapsing to
    a single event signal eliminates that window — the waiter wakes
    the instant ``done`` fires, and the supersession check below
    uses the registry state at that exact moment.

    No time-based backstop: ``_stream_output``'s ``finally`` is a
    Python language guarantee, and ``AgentSession.stop()`` bounds
    subprocess-exit latency via its own signal escalation
    (``INTERRUPT_TIMEOUT`` → ``SIGINT`` → ``SIGTERM`` → ``kill`` →
    ``DRAIN_TIMEOUT``, on the order of seconds).  A hard cap would
    falsely truncate legitimate long-running sessions (multi-hour
    catalog syncs / browser flows) while providing no real
    recovery — a returned-False here just causes the caller to bail
    without finalizing, leaving the task in RUNNING until server
    restart.  ``app/scheduler/task_queue.py::_recover_from_db`` IS
    the absolute backstop: it marks any RUNNING/DESIGNING task
    FAILED on startup with ``error_category='server_restart'``.

    Returns True if the caller should proceed to finalize the task,
    False if the session was replaced (meaning a newer pipeline owns
    the task) or already absent.
    """
    if my_session is None:
        return False

    done: asyncio.Event | None = getattr(my_session, 'done', None)
    plan_saved: asyncio.Event | None = getattr(
        my_session, 'plan_saved_event', None
    )

    waiters: list[asyncio.Task] = []
    if done is not None:
        waiters.append(asyncio.create_task(done.wait()))
    # Auto-approved sessions execute straight through, so plan-save
    # isn't a stopping point — we wait for actual session end.
    if plan_saved is not None and not getattr(
        my_session, 'auto_approve_plan', False
    ):
        waiters.append(asyncio.create_task(plan_saved.wait()))

    if waiters:
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()
            # Drain cancellations so pending-task warnings don't fire.
            # Narrow the suppressed types: CancelledError is expected
            # from the cancel() above; anything else is a real bug in
            # the awaited coroutine and should be logged loudly rather
            # than silently lost.
            for w in waiters:
                try:
                    await w
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        'Unexpected error draining session-wait task for %s',
                        task_id,
                    )

    # A retry / server-restart may have registered a new session
    # under this task_id while we were parked on the event.  The
    # real backend doesn't pop on natural exit, so `cur is my_session`
    # remains True in that case — only a true supersession flips it.
    return agent_manager.get_session(task_id) is my_session


def _is_resume_failure(session) -> bool:
    """Detect the ``--resume`` failure pattern: the agent
    subprocess exited non-zero, was a resume attempt, and produced
    no result text. Used by orchestrators to decide whether to
    retry the same task fresh (no resume).

    Heuristic: any rc!=0 startup with `resume_session_id` set and
    no `_result_text` triggers a retry. This catches the
    `No conversation found with session ID:` case (the common
    one — happens when the Claude Code transcript was GC'd or
    the server restarted long enough that the session aged out)
    plus any other startup failure with no progress made.
    A future refinement is to surface a typed
    ``session.resume_rejected`` flag from `claude_backend_stream`
    after parsing stderr, so the trigger is exact rather than
    inferred.
    """
    if session is None:
        return False
    proc = getattr(session, '_proc', None)
    if proc is None or proc.returncode is None or proc.returncode == 0:
        return False
    if not getattr(session, 'resume_session_id', None):
        return False
    return not getattr(session, '_result_text', None)


async def _maybe_retry_without_resume(task_id: str, my_session):
    """If ``my_session`` ended as a resume failure, clear stale
    task state and start a fresh attempt (no ``--resume``). Owned
    by the orchestrator so it runs in the same coroutine as the
    finalizer — eliminating the race where the manager's hidden
    retry started a second session while the orchestrator
    finalized based on prior-run residue.

    Returns the new session (orchestrator should re-await it) or
    ``None`` if no retry was performed.
    """
    # Diagnostic: log the input state every call so we can see why
    # _is_resume_failure returns False on a real failed-resume case.
    logger.info(
        'RETRY_CHECK task=%s session=%s rc=%s resume_id=%s result_len=%s',
        task_id[:8],
        'set' if my_session else 'None',
        getattr(getattr(my_session, '_proc', None), 'returncode', 'n/a'),
        bool(getattr(my_session, 'resume_session_id', None)),
        len(getattr(my_session, '_result_text', '') or ''),
    )
    if not _is_resume_failure(my_session):
        return None

    rc = my_session._proc.returncode  # type: ignore[union-attr]
    logger.warning(
        'Session resume failed for %s (rc=%d), retrying without resume',
        task_id,
        rc,
    )

    # Clear stale state so the post-retry finalizer sees only this
    # attempt's outcome. Without this, prior `task.result` from
    # earlier rounds misclassifies the retry as success.
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task:
                task.session_id = None
                task.result = None
                task.error = None
                task.error_category = None
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
    except Exception:
        logger.debug(
            'Failed to clear stale state before retry for %s',
            task_id,
            exc_info=True,
        )

    started = await agent_manager.retry_without_resume(task_id)
    if not started:
        return None
    return agent_manager.get_session(task_id)


async def wait_for_session_with_retry(task_id: str, session):
    """Block on ``session`` end, then transparently retry-without-resume
    if Claude Code rejected the session_id.

    Combines ``_wait_for_session_end`` + ``_maybe_retry_without_resume``
    + a second ``_wait_for_session_end`` so every orchestrator
    (``auto_run_task``, ``execute_planned_task``, ``execute_woken_task``,
    ``finalize_followup_session``) gets retry handling from one
    callsite. Returns the session that actually finished (may be the
    retry session) or ``None`` on supersession / abort — caller should
    treat ``None`` exactly like the old `if not _wait_for_session_end(...)`.
    """
    if session is None:
        return None
    if not await _wait_for_session_end(task_id, session):
        return None
    retry = await _maybe_retry_without_resume(task_id, session)
    if retry is None:
        return session
    if not await _wait_for_session_end(task_id, retry):
        return None
    return retry
