"""Submission and verdict — the two halves of ``set_task_result``.

A reviewer gate refuses by raising, and the raise used to happen before
``task.result`` was assigned, so a refusal DESTROYED the submission: N
refusals left the run holding nothing and the end-of-turn fallback had
only chat prose to offer. What the agent submitted and what the review
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
    SOFT_GATE_MAX_DENIALS,
    markdown_format as md_format_gate,
    record_attempt,
    report_reviewer,
    result_language as language_gate,
)
from app.config import VIBE_SELLER_DIR
from app.deliverables.gate import check_deliverables
from app.models.task import Task
from app.routers.tasks_files import (
    looks_like_result_path,
    recorded_skills,
    resolve_audit_deliverable,
    resolve_workspace_result_path,
)
from app.text_utils import sanitize_text


class SetTaskResultRequest(BaseModel):
    result: str
    # The agent's own account of what it could NOT finish. Does not
    # bypass a single gate — the submission is reviewed exactly as
    # before. What it buys is an honest way to say "here is what I got,
    # here is what is missing" without reaching for set_task_error,
    # which means unrecoverable failure and was being used as an
    # escape hatch because it was the only door out.
    incomplete: list[str] | None = None


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

    if resolved_content is None and (
        recorded_skills(task_id) & report_reviewer.AD_SKILLS
    ):
        deliverable = resolve_audit_deliverable(task_root, raw)
        if deliverable is not None:
            try:
                resolved_content = await asyncio.to_thread(
                    deliverable.read_text, encoding='utf-8'
                )
            except OSError:
                resolved_content = None
            if resolved_content is not None:
                logger.info(
                    'Task %s submitted narration (%d chars); grading its '
                    'audit deliverable %s (%d chars) instead',
                    task_id,
                    len(raw),
                    deliverable.name,
                    len(resolved_content),
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
) -> None:
    """The gates every task gets, regardless of which skills it loaded.

    Each gate gets at most ``SOFT_GATE_MAX_DENIALS`` refusals per task;
    past the cap the text is allowed through so a stubborn failure cannot
    trap the agent in a loop it has no way to exit. Run here rather than
    from the Stop hook because some agent backends never emit Stop.
    """
    # Deliverable check first: "you are missing three files" is more
    # actionable than "your table is malformed", and a run that has to
    # go back for a file will rewrite the prose anyway.
    denials = []
    deliverable_deny = await check_deliverables(
        db, task, (VIBE_SELLER_DIR / 'tasks' / task_id).resolve()
    )
    if deliverable_deny:
        denials.append(deliverable_deny)
    for gate_module, gate_args in (
        (md_format_gate, (final_result,)),
        (language_gate, (final_result, task.title, task.description)),
    ):
        deny = gate_module.check(*gate_args)
        if deny:
            denials.append(deny)

    for deny in denials:
        attempt = record_attempt(task_id, deny.gate)
        if attempt <= SOFT_GATE_MAX_DENIALS:
            await refuse(db, task, deny.reason, declared + deny.gaps)
        logger.warning(
            'Soft gate %s exceeded %d denials for task %s — allowing '
            'result through anyway. Reason: %s',
            deny.gate,
            SOFT_GATE_MAX_DENIALS,
            task_id,
            deny.reason[:200],
        )
