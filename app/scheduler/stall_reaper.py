"""Periodic reaper for stalled RUNNING agent sessions.

Complements ``plan_reaper`` (which only covers plan-only tasks).
When an agent's upstream SSE stream goes silent with no stop event
(observed with Z.AI GLM-4.7 after a successful tool_result), the
Task row stays in ``status=RUNNING`` indefinitely because nothing
else ever writes a terminal status. This reaper scans for that
state and flips the Task to a terminal status so clients + tests
observe one within bounded time.

The terminal status depends on WHEN the stream died:

- **After the deliverable was declared** (the agent set a result
  via ``vibe_seller_set_task_result`` with no error recorded, or
  the CLI emitted its final ``result`` event) → ``COMPLETED``.
  The stall is a transport failure, not a task failure — the work
  is done and presenting it as FAILED ("Re-run to retry") tells
  the user to redo a finished job. (Observed: a daily ads audit
  whose md + review + PDF + reflection all completed, then the
  provider dropped the SSE stream post-completion.)
- **Mid-work** (no declared result) → ``FAILED``, preserving the
  last substantial assistant message as a PARTIAL result.

The signal we key off is ``Task.updated_at``: every streamed event,
tool call, and hook callback already bumps it (see
``app/ai/claude_backend_stream.py`` + ``claude_backend_hooks.py``),
so a stalled stream naturally lets ``updated_at`` go stale.

Only RUNNING tasks are in scope. WAITING tasks are handled by the
waiting checker. DESIGNING tasks (plan-only) are handled by
``plan_reaper``.

See issue #141 for the full discovery context.
"""

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import select

from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

# Real agent work bumps ``updated_at`` every tool call / streamed
# event. The streaming-delta heartbeat in ``claude_backend_stream``
# throttles bumps to once per 60 s, so a healthy task is always
# within 60 s of fresh. Three minutes of silence is well past
# "just thinking" and strongly indicates the stream died upstream.
# Previously this was 5 min, which combined with the 1 min poll
# interval gave a 6 min worst-case detection window — too tight for
# the catalog-sync e2e test's 900 s budget when GLM-4.7 emits a
# late-arriving degenerate response followed by silence. 3 min keeps
# ample headroom over the heartbeat cadence while halving the gap
# between "stalled" and "reaped".
_STALL_THRESHOLD = timedelta(minutes=3)

_STALL_ERROR_MESSAGE = (
    'Agent stream stalled — no events for 3+ minutes. Upstream '
    'provider likely dropped the SSE stream without emitting a '
    'stop event. Task was force-failed by stall_reaper. Re-run to '
    'retry.'
)


async def reap_stalled_running_tasks() -> None:
    """Terminate RUNNING tasks whose last activity is past the cutoff.

    COMPLETED when the deliverable was already declared (result set
    with no error, or a final ``result`` event recovered from the
    message log); FAILED otherwise — see the module docstring.

    Idempotent and race-safe:

    - Re-checks ``task.status`` inside the session before flipping,
      so a finalizer that already transitioned the task in-between
      the query and the write wins.
    - Best-effort ``agent_manager.stop(task.id)`` BEFORE the status
      flip so any still-listening stream iterator doesn't try to
      write a conflicting status after us.
    - Emits ``task_update`` (+ ``task_failed`` for the FAILED
      outcome) SSE so subscribed clients (tests, UI) see the
      terminal status without polling — only for tasks this pass
      actually flipped.
    """
    cutoff = datetime.now(UTC) - _STALL_THRESHOLD
    cutoff_iso = cutoff.isoformat()

    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.status == TaskStatus.RUNNING,
                Task.updated_at < cutoff_iso,
            )
        )
        stalled = list(result.scalars().all())

        if not stalled:
            return

        # An outstanding AskUserQuestion parks the agent in
        # ``_answer_events[...].wait()`` with no stream activity, so
        # ``updated_at`` goes stale even though the subprocess is
        # healthy. Skip those — the operator could legitimately take
        # hours to answer. (Same session-manager state machine the
        # ``/questions/answer`` router relies on.)
        stalled = [
            t for t in stalled if not agent_manager.get_pending_questions(t.id)
        ]

        if not stalled:
            return

        # (task_id, terminal_status) actually written this pass —
        # tasks the re-check skipped must NOT get events emitted.
        outcomes: list[tuple[str, str]] = []

        for task in stalled:
            logger.warning(
                'Reaping stalled task %s (updated_at=%s, threshold=%s)',
                task.id,
                task.updated_at,
                cutoff_iso,
            )
            # Kill the stream first — if the session is still
            # somehow alive, this prevents it from racing our
            # status write.
            try:
                await agent_manager.stop(task.id)
            except Exception:
                logger.exception('agent_manager.stop(%s) raised', task.id)

            # Re-read under the current session — the finalizer may
            # have already marked this task terminal in the window
            # between the query and here.
            fresh = await db.get(Task, task.id)
            if fresh is None or fresh.status != TaskStatus.RUNNING:
                continue

            # Did the agent explicitly declare its deliverable done
            # before the stream died? vibe_seller_set_task_result's
            # contract: setting a result declares success (failures
            # must call set_task_error). An error recorded alongside
            # wins — that's the documented partial-output-on-failure
            # combination.
            deliverable_done = bool(fresh.result) and not fresh.error

            # Preserve the last substantial assistant message as
            # the result so the user's work isn't lost.
            if not fresh.result:
                msg_row = await db.execute(
                    select(TaskMessage)
                    .where(
                        TaskMessage.task_id == task.id,
                        TaskMessage.role.in_(['assistant', 'result']),
                    )
                    .order_by(TaskMessage.seq.desc())
                    .limit(1)
                )
                last_msg = msg_row.scalar_one_or_none()
                if (
                    last_msg
                    and last_msg.content
                    and len(last_msg.content) > 100
                ):
                    # If the agent already emitted a structured
                    # `result` event, the deliverable is complete —
                    # only the stop hook went missing. Save it
                    # verbatim so the user sees the final report
                    # rather than a misleading PARTIAL banner.
                    if last_msg.role == 'result':
                        fresh.result = last_msg.content
                        deliverable_done = not fresh.error
                    else:
                        fresh.result = (
                            '[PARTIAL — stream stalled before '
                            'completion]\n\n' + last_msg.content
                        )

            now_iso = datetime.now(UTC).isoformat()
            if deliverable_done:
                # Post-completion stream drop: the work is done, the
                # transport died. COMPLETED — never tell the user to
                # re-run a finished job.
                logger.warning(
                    'Task %s stalled AFTER its deliverable was '
                    'declared — marking COMPLETED (stream drop, not '
                    'a task failure)',
                    task.id,
                )
                fresh.status = TaskStatus.COMPLETED
            else:
                fresh.status = TaskStatus.FAILED
                fresh.error = _STALL_ERROR_MESSAGE
                fresh.error_category = 'agent_stream_stalled'
            fresh.updated_at = now_iso
            fresh.completed_at = now_iso
            outcomes.append((task.id, fresh.status))

        await db.commit()

    for task_id, status in outcomes:
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': status},
        )
        if status == TaskStatus.FAILED:
            await event_bus.emit(
                'task_failed',
                {
                    'task_id': task_id,
                    'error': _STALL_ERROR_MESSAGE,
                    'error_category': 'agent_stream_stalled',
                },
            )
