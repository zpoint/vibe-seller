"""Submission and verdict — the two halves of ``set_task_result``.

A result gate refuses by raising, and the raise used to happen before
``task.result`` was assigned, so a refusal DESTROYED the submission: N
refusals left the run holding nothing and the end-of-turn fallback had
only chat prose to offer. What the agent submitted and what the gates
said about it are separate facts, recorded separately here, and only a
submission that clears every gate becomes ``accepted_result``.

Split out of ``routers/tasks.py`` (over the per-file line limit) as one
cohesive unit rather than by cutting lines to fit.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.stop_gates import (
    CONTRADICTION_MAX_DENIALS,
    SOFT_GATE_MAX_DENIALS,
    contradiction_banner,
    record_attempt,
    review_rounds_spent,
    take_review_round,
)
from app.config import VIBE_SELLER_DIR
from app.deliverables.gate import check_deliverables
from app.models.task import Task
from app.routers.tasks_files import (
    looks_like_result_path,
    resolve_workspace_result_path,
)
from app.telemetry_events import GateOutcome
from app.telemetry_harness import send_result_gated
from app.text_utils import SafeStr


class SetTaskResultRequest(BaseModel):
    result: SafeStr
    # The agent's own account of what it could NOT finish. Does not
    # bypass a single gate — the submission is reviewed exactly as
    # before. What it buys is an honest way to say "here is what I got,
    # here is what is missing" without reaching for set_task_error,
    # which means unrecoverable failure and was being used as an
    # escape hatch because it was the only door out.
    incomplete: list[SafeStr] | None = None


logger = logging.getLogger(__name__)


async def retain_submission(db: AsyncSession, task: Task, text: str) -> None:
    """Record what the agent submitted, before any gate can refuse it.

    Committed immediately so the submission survives the ``raise`` that
    a refusal is delivered by.
    """
    task.submitted_result = text
    task.submission_count = (task.submission_count or 0) + 1
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()


def declared_gaps(body: SetTaskResultRequest) -> tuple[str, ...]:
    """Agent-declared unfinished items, normalised."""
    return tuple(
        str(g).strip() for g in (body.incomplete or ()) if str(g).strip()
    )


async def refuse(
    db: AsyncSession,
    task: Task,
    reason: str,
    gaps: tuple[str, ...] = (),
) -> None:
    """Annotate the retained submission with this refusal, then 400.

    Never returns. The gaps land on the task so that if the run ends
    without an accepted submission, the finalizer can still ship the
    best attempt with an honest list of what it is missing rather than
    nothing at all.
    """
    # A gate with no natural item list still has to leave a trace: if
    # this run never gets an accepted submission, these gaps are the
    # only account of WHY the retained deliverable is partial. Fall back
    # to the prose, capped so one refusal can't swamp the result.
    recorded = list(gaps) or [reason[:500]]
    task.review_gaps = json.dumps(recorded, ensure_ascii=False)
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    raise HTTPException(status_code=400, detail=reason)


async def resolve_submitted_result(
    raw: str,
    task_id: str,
    task_root,
) -> tuple[str, bool]:
    """``(text_to_grade, came_from_a_file)``.

    The second element is not cosmetic: the endpoint reports it as
    ``resolved_from_file`` so the agent can tell whether its pointer was
    followed, and a contract test pins that key.

    Three shapes arrive here and only one is content:

    * A POINTER into the task workspace — read the file. An agent saving a
      multi-100KB report would block the event loop, so the read is
      offloaded.
    * A DANGLING pointer — rejected, never demoted to content. A path-like
      string that resolves to nothing would sail through every content
      gate vacuously (no ad sections to check) and complete the task with
      a literal path as its "result"; that bypass let a quoted pointer end
      an audit that had drilled a handful of campaigns.
    * NARRATION where a file was required — the mirror of the dangling
      case: the file exists, the agent just described it instead of
      pointing at it. Both end with the gates grading a string that is not
      the report. An ad audit's deliverable is a file by contract, so when
      the task's bound skills make it an ad task and the workspace holds a
      report the submission is not, the FILE is what gets graded.

    Raises ``HTTPException`` for the dangling case, with the fix spelled
    out — the agent is the caller and has to know what to do next.
    """
    resolved_content: str | None = None
    target = resolve_workspace_result_path(raw, task_root)
    if target is not None:
        try:
            resolved_content = await asyncio.to_thread(
                target.read_text, encoding='utf-8'
            )
        except OSError:
            resolved_content = None

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

    if resolved_content is not None:
        return resolved_content, True
    return raw, False


async def apply_soft_gates(
    db: AsyncSession,
    task: Task,
    task_id: str,
    final_result: str,
    declared: tuple[str, ...],
) -> tuple[str, ...]:
    """The gates every task gets, regardless of which skills it loaded.

    Each gate gets at most ``SOFT_GATE_MAX_DENIALS`` refusals per task;
    past the cap the text is allowed through so a stubborn failure cannot
    trap the agent in a loop it has no way to exit — and its gaps are
    returned, to ship as the result's caveats rather than vanish into a
    log line. Run here rather than from the Stop hook because some agent
    backends never emit Stop.
    """
    denials = []
    deliverable_deny = await check_deliverables(
        db, task, (VIBE_SELLER_DIR / 'tasks' / task_id).resolve()
    )
    if deliverable_deny:
        denials.append(deliverable_deny)

    allowed_gaps: list[str] = []
    for deny in denials:
        attempt = record_attempt(task_id, deny.gate)
        gaps = deny.gaps or (deny.reason,)
        refused = attempt <= SOFT_GATE_MAX_DENIALS
        send_result_gated(
            task_id,
            deny.gate,
            GateOutcome.REFUSED if refused else GateOutcome.ALLOWED,
            attempt=attempt,
            gap_count=len(gaps),
        )
        if refused:
            await refuse(db, task, deny.reason, declared + deny.gaps)
        allowed_gaps.extend(gaps)
        logger.warning(
            'Soft gate %s exceeded %d denials for task %s — allowing '
            'result through anyway. Reason: %s',
            deny.gate,
            SOFT_GATE_MAX_DENIALS,
            task_id,
            deny.reason[:200],
        )
    return tuple(allowed_gaps)


async def apply_skill_gates(
    db: AsyncSession,
    task: Task,
    skill_gates,
    final_result: str,
    rules,
    declared: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Skill-declared domain gates: ``(final_result, gaps_to_ship)``.

    Refuses by raising (see ``refuse``). What survives — a fail-open on
    a stall, or a banner-marked contradiction — comes back as gaps that
    ship as the result's caveats.
    """
    task_id = task.id
    stalled_gaps: list[str] = []
    for gate_name, gate in skill_gates:
        deny = await asyncio.to_thread(gate.check, final_result, task_id, rules)
        if not deny:
            continue
        # Fail open only on STALL when the gate tracks one (see the
        # review-collect skill's review_completeness_review gate for
        # the stall design).
        # And at most AD_REVIEW_ROUNDS refusals per gate in any case: the
        # fix is asked for once, then what is still unmet ships as caveats.
        is_stalled = getattr(gate, 'is_stalled', None)
        if (
            is_stalled is None or not is_stalled(task_id)
        ) and take_review_round(task_id, gate_name):
            send_result_gated(
                task_id,
                gate_name,
                GateOutcome.REFUSED,
                attempt=review_rounds_spent(task_id, gate_name),
                gap_count=len(deny.gaps or (deny.reason,)),
            )
            await refuse(db, task, deny.reason, declared + deny.gaps)
        # A CONTRADICTION is not the kind of gap the stall exists to
        # forgive. The fail-open is there so a weak model is never trapped
        # by work it cannot finish; accepting a figure that cannot be true
        # is a different act, because the number ships into decisions.
        # Keep refusing past the stall — the agent always has a legal move
        # (fix it, or mark the campaign untrustworthy), so this cannot
        # trap. Bounded anyway: past the cap the banner below makes the
        # contradiction impossible to miss instead of impossible to pass.
        if deny.contradictions:
            n = record_attempt(task_id, f'{gate_name}:contradiction')
            send_result_gated(
                task_id,
                gate_name,
                GateOutcome.REFUSED
                if n <= CONTRADICTION_MAX_DENIALS
                else GateOutcome.BANNER_MARKED,
                attempt=n,
                gap_count=len(deny.contradictions),
            )
            if n <= CONTRADICTION_MAX_DENIALS:
                await refuse(
                    db, task, deny.reason, declared + deny.contradictions
                )
            logger.warning(
                'Gate %s: %d contradiction(s) unresolved after %d refusals '
                'for task %s — banner-marking the result. First: %s',
                gate_name,
                len(deny.contradictions),
                n,
                task_id,
                deny.contradictions[0][:160],
            )
            final_result = (
                contradiction_banner(deny.contradictions) + final_result
            )
            stalled_gaps.extend(deny.contradictions)
        # Log the GAPS, not ``deny.reason`` — the reason opens with a
        # fixed instruction banner and appends the gap list at the END,
        # so a truncated prefix of it is pure boilerplate. Six of these
        # lines in one run read byte-identical while the underlying gaps
        # went 14 → 8 → 7, which is worse than silence: it looks like a
        # run repeating itself when it is converging. Say "failing open"
        logger.warning(
            'Gate %s stalled for task %s — failing open with %d unmet gap(s). '
            'Gaps: %s',
            gate_name,
            task_id,
            len(deny.gaps or ()),
            ' | '.join(g[:110] for g in (deny.gaps or ())[:4])
            or deny.reason[:160],
        )
        send_result_gated(
            task_id,
            gate_name,
            GateOutcome.FAILED_OPEN,
            attempt=review_rounds_spent(task_id, gate_name),
            gap_count=len(deny.gaps or (deny.reason,)),
        )
        # Failed open on a stall: the result ships, but the unmet gaps
        # ride along as caveats instead of vanishing into a log line.
        stalled_gaps.extend(deny.gaps or (deny.reason,))
    return final_result, tuple(stalled_gaps)
