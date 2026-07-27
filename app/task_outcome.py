"""What a task run produced — resolved once, from one place.

Terminal status used to be *derived* at the end of a run by reading two
independently-written nullable columns::

    if task.error:
        task.status = FAILED

``task.result`` was never consulted. Four actors could write that pair at
four different times (the result endpoint, the error endpoint, the
end-of-turn prose fallback, the stall reaper), so every guard against a
contradictory ending had to reconcile one writer against one other — a
pairwise patch, one per pair, and the pair nobody had written yet is how
a run shipped FAILED while holding a finished report.

This module replaces the derivation with a recorded fact. A run produces
exactly one :class:`TaskOutcome`; :func:`resolve_outcome` builds it from
the task row using a single ordered precedence rule, and
:func:`apply_outcome` writes it back. Terminal status is then a pure
function of ``outcome.kind`` — nothing else may decide it.

Precedence, best deliverable first:

1. ``accepted_result`` — a submission that cleared every gate.
   DELIVERED.
2. ``submitted_result`` — submitted, refused, retained. INCOMPLETE, with
   the unmet gaps carried as caveats.
3. ``transcript_tail`` — streamed prose. A legitimate deliverable when
   the chat output *is* the answer (a lookup, an answered question), and
   the reason this is a fallback rather than a ban: most tasks never
   call ``set_task_result`` at all.
4. nothing — FAILED.

An infra-detected error outranks all of it: if the browser never came
up, prose about trying to start it is not a deliverable. An
*agent-reported* error does not, because an agent that has produced
output and then explains its caveats has not failed — that is the
escape-hatch pattern the error channel kept being used for.
"""

from __future__ import annotations

import dataclasses
import enum
import json

from app.task_states import TaskStatus

# ``error_category`` written when the agent calls set_task_error itself.
# Distinguished from infra-detected categories (browser_launch_failed,
# agent_stream_stalled, stopped_by_user, ...) which are never demoted to
# a caveat — the agent's own account of a partial run is.
AGENT_REPORTED = 'agent_reported'


class OutcomeKind(enum.StrEnum):
    """What the run actually produced."""

    DELIVERED = 'delivered'
    INCOMPLETE = 'incomplete'
    FAILED = 'failed'


#: ``OutcomeKind`` → the status a task lands in. The ONLY mapping from
#: outcome to terminal state; no caller may special-case it. INCOMPLETE
#: completes: a partial deliverable with an honest gap list is a result
#: the user can act on, not a failure.
TERMINAL_STATUS: dict[OutcomeKind, TaskStatus] = {
    OutcomeKind.DELIVERED: TaskStatus.COMPLETED,
    OutcomeKind.INCOMPLETE: TaskStatus.COMPLETED,
    OutcomeKind.FAILED: TaskStatus.FAILED,
}


@dataclasses.dataclass(frozen=True)
class TaskOutcome:
    """The single verdict on a run.

    ``content`` is present for every kind except FAILED; ``reason`` only
    for FAILED. Making these one value rather than two loose columns is
    the point: "error recorded AND deliverable present" stops being a
    state that needs guarding, because it can no longer be constructed.
    """

    kind: OutcomeKind
    content: str | None = None
    caveats: tuple[str, ...] = ()
    reason: str | None = None
    category: str | None = None

    @property
    def status(self) -> TaskStatus:
        """Terminal status for this outcome."""
        return TERMINAL_STATUS[self.kind]

    def rendered(self) -> str | None:
        """``content`` with caveats appended as a markdown block."""
        if self.content is None:
            return None
        if not self.caveats:
            return self.content
        lines = '\n'.join(f'> - {c}' for c in self.caveats)
        return f'{self.content}\n\n> ⚠️ **未完成项 / Unmet**\n{lines}'


def _gaps(task) -> tuple[str, ...]:
    """Unmet gaps recorded by the last refusal, if any."""
    raw = getattr(task, 'review_gaps', None)
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(g) for g in parsed if str(g).strip())


def _text(value) -> str | None:
    """Non-blank string or None."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def resolve_outcome(task) -> TaskOutcome:
    """Decide what *task* produced. Pure — does not mutate.

    Reads ``result`` / ``submitted_result`` / ``transcript_tail`` /
    ``error`` / ``error_category`` and applies the module precedence.
    Safe to call more than once and safe on a partially-migrated row
    (the new columns are read with ``getattr``).
    """
    error = _text(getattr(task, 'error', None))
    category = getattr(task, 'error_category', None)
    agent_reported = bool(error) and category == AGENT_REPORTED

    # Infra failures are terminal regardless of what prose exists.
    if error and not agent_reported:
        return TaskOutcome(
            kind=OutcomeKind.FAILED, reason=error, category=category
        )

    # The agent's own error becomes a caveat once there is something to
    # attach it to — it is an account of a partial run, not a verdict.
    agent_caveat = (error,) if agent_reported else ()

    accepted = _text(getattr(task, 'accepted_result', None))
    if accepted:
        return TaskOutcome(
            kind=OutcomeKind.DELIVERED,
            content=accepted,
            # A clean accept has no gaps; a stall-fail-open or an
            # agent-declared `incomplete=[...]` does, and those ride
            # along rather than vanishing into a log line.
            caveats=_gaps(task) + agent_caveat,
        )

    submitted = _text(getattr(task, 'submitted_result', None))
    if submitted:
        return TaskOutcome(
            kind=OutcomeKind.INCOMPLETE,
            content=submitted,
            caveats=_gaps(task) + agent_caveat,
        )

    prose = _text(getattr(task, 'transcript_tail', None))
    if prose and not error:
        return TaskOutcome(kind=OutcomeKind.DELIVERED, content=prose)

    # Nothing to ship. ``reason`` stays None when the run recorded no
    # error at all — "produced nothing" is NOT the same as "failed", and
    # the finalizer still has to distinguish e.g. incomplete todos
    # (→ WAITING for input) from a genuinely empty run (→ FAILED).
    # Synthesising an error here would preempt that branch and turn
    # every awaiting-input task into a failure.
    return TaskOutcome(
        kind=OutcomeKind.FAILED, reason=error or None, category=category
    )


def clear_run_state(task, *, reset_submissions: bool = False) -> None:
    """Drop every input this module resolves from. Turn-scoped.

    A follow-up or a retry starts a new run, so no prior submission,
    verdict or prose may be re-resolved as the new run's deliverable.
    Callers used to clear ``result``/``error`` by hand at three sites;
    centralising it here means a future input cannot be added to the
    resolver and forgotten at one of them.

    ``reset_submissions`` also zeroes the submit counter — true for a
    retry (a genuinely fresh run), false for a follow-up turn, where
    the count is a running total for the task.
    """
    task.result = None
    task.accepted_result = None
    task.submitted_result = None
    task.review_gaps = None
    task.transcript_tail = None
    task.error = None
    task.error_category = None
    if reset_submissions:
        task.submission_count = 0


def apply_outcome(task, outcome: TaskOutcome) -> None:
    """Write *outcome* onto *task*. Does NOT set status or commit.

    Callers set status from ``outcome.status`` so the transition stays
    under ``assert_transition`` at the call site. On a non-FAILED
    outcome the error pair is cleared: the caveat now lives inside the
    rendered result, and leaving ``task.error`` populated would let the
    old ``if task.error:`` reading resurface anywhere downstream.
    """
    if outcome.kind is OutcomeKind.FAILED:
        # Only record an error the run actually reported. Inventing one
        # for "produced nothing" would make the caller's
        # ``FAILED and task.error`` test self-fulfilling and swallow the
        # WAITING-for-input branch.
        if outcome.reason is not None:
            task.error = outcome.reason
            task.error_category = outcome.category
        return
    task.result = outcome.rendered()
    # Persist the caveats we just consumed. An agent-reported error is
    # one of them, and clearing the error below would otherwise drop it
    # on the next resolve — finalizers run more than once (end-of-turn,
    # then cleanup), so a caveat that lives only in ``error`` survives
    # the first pass and vanishes on the second.
    task.review_gaps = (
        json.dumps(list(outcome.caveats), ensure_ascii=False)
        if outcome.caveats
        else None
    )
    task.error = None
    task.error_category = None
