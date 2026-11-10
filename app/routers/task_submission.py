"""Submission and verdict — the two halves of ``set_task_result``.

A result gate refuses by raising, and the raise used to happen before
``task.result`` was assigned, so a refusal DESTROYED the submission and
a run that ended after it had only chat prose to offer. What the agent
submitted and what the checks said about it are separate facts,
recorded separately here; a submission becomes ``accepted_result`` once
every check has had its one round.

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

from app.ai.stop_gates import contradiction_banner, record_attempt
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


async def gate_denials(
    db: AsyncSession,
    task: Task,
    skill_gates,
    final_result: str,
    rules,
) -> list:
    """What every check found on this submission: the report-deliverable
    check every task gets, then the gates the loaded skills declared."""
    denies = []
    deny = await check_deliverables(
        db, task, (VIBE_SELLER_DIR / 'tasks' / task.id).resolve()
    )
    if deny:
        denies.append(deny)
    for _gate_name, gate in skill_gates:
        deny = await asyncio.to_thread(gate.check, final_result, task.id, rules)
        if deny:
            denies.append(deny)
    return denies


async def apply_round(
    db: AsyncSession,
    task: Task,
    denies: list,
    final_result: str,
    declared: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """One round per check, one refusal per submit: ``(result, caveats)``.

    If any check reports for the first time on this task, the submit is
    refused once, listing everything every check found — so the agent
    sees it all at once instead of one check per submit. A check that
    already had its round never refuses again: on the next submit the
    result goes through with the findings as caveats. Findings that
    contradict each other (or a change the user did not approve) are
    also marked with a banner at the top — on the retained submission
    too, so a run that ends after the refusal still ships the warning.
    """
    if not denies:
        return final_result, ()
    fresh = {d.gate for d in denies if record_attempt(task.id, d.gate) == 1}
    gaps = tuple(g for d in denies for g in (d.gaps or (d.reason,)))
    contradictions = tuple(c for d in denies for c in d.contradictions)
    for d in denies:
        send_result_gated(
            task.id,
            d.gate,
            # Only the check that is spending its round is a refusal.
            # The others had theirs already: what is left of them is a
            # caveat (banner-marked when it contradicts), and counting
            # them as refusals made one fresh check look like every
            # check refusing at once.
            GateOutcome.REFUSED
            if d.gate in fresh
            else GateOutcome.BANNER_MARKED
            if d.contradictions
            else GateOutcome.FAILED_OPEN,
            attempt=1 if d.gate in fresh else 2,
            gap_count=len(d.gaps or (d.reason,)),
        )
    if contradictions:
        final_result = contradiction_banner(contradictions) + final_result
    if fresh:
        task.submitted_result = final_result
        await refuse(
            db, task, '\n\n'.join(d.reason for d in denies), declared + gaps
        )
    logger.warning(
        'Checks %s had their round for task %s — shipping %d finding(s) '
        'as caveats. First: %s',
        ', '.join(sorted({d.gate for d in denies})),
        task.id,
        len(gaps),
        ' | '.join(g[:110] for g in gaps[:4]),
    )
    return final_result, gaps
