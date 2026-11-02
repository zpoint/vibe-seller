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

**Who owns "this request is dead"** — the decision is split by the
agent process state, because a DB timestamp alone cannot tell a
slow model from a dropped stream (both are minutes of silence):

- **Agent process alive and input-capable** (``session.running``):
  the CLI owns the in-flight request, and every spawn env arms the
  machinery that makes a dead stream LOUD inside the CLI
  (``ensure_stream_liveness_env``): the CLI's stream-idle watchdog
  aborts a stream that has produced no chunk for
  ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` (7 min default; time to first
  chunk counts — the timer arms before the first chunk — and every
  chunk resets it), the abort falls into the CLI's non-streaming
  fallback, and each failed attempt is announced on stdout as a
  ``system/api_retry`` line; partial messages
  (``CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES``) forward every raw SSE
  part as a stdout line, so a healthy compose bumps ``updated_at``
  per part instead of going silent. The session's turn watchdog
  owns the process (hard-idle stdin close → post-close kill). The
  reaper defers to that authority and only fires once true stdout
  silence has outlasted the watchdog bound plus recovery headroom
  (``_stream_silence_bound`` — arithmetic below). A slow first
  token is structurally inside that bound: observed live
  (MiniMax-M3), 3–4 min of silence after a large tool_result while
  the model composed a report — healthy, and reaped as stalled by
  the old flat 3-min rule.
- **No session / process exited** (or alive past its turn
  terminator — ``running`` is False once stdin is closed or
  ``stop()`` began, and the post-close kill escalation disposes of
  the process within its grace): nothing can bump ``updated_at``
  any more, so staleness is authoritative again and the fast 3-min
  rule applies.

The ``updated_at`` signal itself (every streamed event, tool call,
and hook callback bumps it — see ``claude_backend_stream.py``) is
kept as the *staleness clock* but no longer as the *death verdict*
while its owner is alive: the streaming heartbeat that refreshes
it runs inside the same reader task as the stream, so anything
that starves the reader (a blocked control-request handler, a DB
stall) freezes the timestamp of a perfectly healthy agent. The
reaper must not execute that agent on the frozen evidence.

Only RUNNING tasks are in scope. WAITING tasks are handled by the
waiting checker. DESIGNING tasks (plan-only) are handled by
``plan_reaper``. An outstanding AskUserQuestion is always skipped
(the operator may take hours).

See issue #141 for the full discovery context.
"""

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import select

from app.ai.claude_backend_manager import agent_manager
from app.ai.profiles import (
    ProfileManager,
    effective_api_timeout_ms,
    effective_stream_idle_timeout_ms,
    stream_watchdog_enabled,
)
from app.database import async_session
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

# Fast rule for tasks whose agent cannot progress any more (process
# gone, or past its turn terminator). Real agent work bumps
# ``updated_at`` every tool call / streamed event; the streaming
# heartbeat throttles bumps to once per 60 s, so a healthy task is
# always within ~60 s of fresh. Three minutes of silence from a
# dead process is well past "just thinking". Previously this flat
# rule also applied to LIVE agents — see the module docstring.
_STALL_THRESHOLD = timedelta(minutes=3)

# Slack added on top of the CLI's stream-idle watchdog while the
# agent process is alive. Derived from the CLI's OWN numbers
# (claude-code src/services/api/withRetry.ts + the backend's own
# cadences): the sum of all retry backoffs across the CLI's retry
# budget is <= ~200 s (min(500 ms · 2^(n-1), 32 s) over 10 retries
# = 159.5 s, +<=25 % jitter = ~200 s), one reaper poll interval is
# 60 s, and the heartbeat throttle window is 60 s — so a bump that
# is due but not yet committed can never tip the decision. The
# headroom also covers a post-watchdog non-streaming fallback
# request completing for an observed-worst healthy compose (3–4
# min): recovery lands ~W+4 min, inside W+6 min.
_RECOVERY_HEADROOM = timedelta(seconds=360)

# Degraded-config bound (watchdog disarmed by the profile): the
# CLI's request timeout + the same poll/heartbeat slack, which is
# the best bound available when nothing bounds body silence.
_ALIVE_MARGIN = timedelta(seconds=120)


def _stream_silence_bound(task: Task) -> timedelta:
    """When a silent-but-alive agent becomes reapable.

    With the stream-idle watchdog armed (the default every spawn
    env now carries), the bound is the CLI's own dead-stream
    declaration — ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` as the CLI was
    actually spawned with it (``ProfileManager.get_env_for_profile``
    — the same merge the spawn used, and it guarantees the key is
    present via ``ensure_stream_liveness_env``) — plus
    ``_RECOVERY_HEADROOM``. With presets that is 7 min + 6 min =
    **13 min** of true stdout silence, replacing the previous
    ``API_TIMEOUT_MS + 2 min`` (~52 min): the API timeout only
    bounds the headers phase (the SDK timeout does not cover the
    streaming body), so keying off it made the #141 shape —
    provider drops the stream, process alive — take ~52 min to end
    instead of ~3.

    Why not wait out the CLI's full recovery machinery? A watchdog
    abort falls into a non-streaming fallback whose per-attempt
    timeout READS ``API_TIMEOUT_MS`` (50 min with presets) — a
    dead provider would grind there silently for 50 min before its
    first ``api_retry``. Silence past W + headroom means either
    that grind (dead — reaping is the point) or a compose longer
    than any ever observed from a healthy provider (worst
    observed: 4 min, and W already gives it 7). The reaper takes
    the dead-read.

    Without the watchdog (a profile that explicitly disables it)
    nothing bounds body silence, so the bound degrades to the
    request-timeout form. Module-level indirection so tests can
    patch the bound.
    """
    env = ProfileManager.get_env_for_profile(task.ai_profile_id or 'default')
    if stream_watchdog_enabled(env):
        return (
            timedelta(milliseconds=effective_stream_idle_timeout_ms(env))
            + _RECOVERY_HEADROOM
        )
    return timedelta(milliseconds=effective_api_timeout_ms(env)) + _ALIVE_MARGIN


def _staleness(updated_at: str | None, now: datetime) -> timedelta:
    """Age of ``updated_at``; huge when unparseable (reap rather
    than trust a timestamp we cannot even read)."""
    if not updated_at:
        return timedelta.max
    try:
        return now - datetime.fromisoformat(updated_at)
    except ValueError:
        return timedelta.max


def _stall_error_message(minutes: int) -> str:
    return (
        f'Agent stream stalled — no events for {minutes}+ minutes. '
        'Upstream provider likely dropped the SSE stream without '
        'emitting a stop event. Task was force-failed by '
        'stall_reaper. Re-run to retry.'
    )


async def reap_stalled_running_tasks() -> None:
    """Terminate RUNNING tasks whose agent can no longer progress.

    Two thresholds, by authority (see the module docstring): a task
    whose agent process is alive and input-capable is only reapable
    once silence has outlasted its stream-silence bound (the CLI's
    own stream-idle watchdog value plus recovery headroom);
    everything else (no session, dead process, or a process past
    its turn terminator) uses the flat 3-min rule.

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

        # (task_id, terminal_status, stale_minutes) actually written
        # this pass — tasks the re-check skipped must NOT get events
        # emitted.
        outcomes: list[tuple[str, str, int]] = []

        now = datetime.now(UTC)
        for task in stalled:
            stale_for = _staleness(task.updated_at, now)
            # Authority check: an alive, input-capable agent owns its
            # in-flight request (CLI timeout + retries; turn
            # watchdog owns the process). Silence alone — even
            # silence that outlasted the heartbeat — is not death
            # until it also outlasts the request timeout bound.
            session = agent_manager.get_session(task.id)
            if agent_manager.is_process_alive(task.id) and getattr(
                session, 'running', False
            ):
                bound = _stream_silence_bound(task)
                if stale_for < bound:
                    logger.info(
                        'Task %s silent for %.0fs but agent process '
                        'is alive (request in flight) — deferring to '
                        'the CLI stream-idle bound of %.0fs',
                        task.id,
                        stale_for.total_seconds(),
                        bound.total_seconds(),
                    )
                    continue
                logger.warning(
                    'Task %s silent for %.0fs WITH the agent process '
                    'alive — past the stream-silence bound %.0fs '
                    "(the CLI's own watchdog should have made the "
                    'stream loud by now); reaping',
                    task.id,
                    stale_for.total_seconds(),
                    bound.total_seconds(),
                )

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
            stale_minutes = max(1, int(stale_for.total_seconds() // 60))
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
                fresh.error = _stall_error_message(stale_minutes)
                fresh.error_category = 'agent_stream_stalled'
            fresh.updated_at = now_iso
            fresh.completed_at = now_iso
            outcomes.append((task.id, fresh.status, stale_minutes))

        await db.commit()

    for task_id, status, stale_minutes in outcomes:
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': status},
        )
        if status == TaskStatus.FAILED:
            await event_bus.emit(
                'task_failed',
                {
                    'task_id': task_id,
                    'error': _stall_error_message(stale_minutes),
                    'error_category': 'agent_stream_stalled',
                },
            )
