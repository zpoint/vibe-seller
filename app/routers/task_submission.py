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
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.stop_gates import contradiction_banner, record_attempt
from app.config import VIBE_SELLER_DIR
from app.deliverables.gate import check_deliverables
from app.models.task import Task
from app.routers.tasks_files import (
    resolve_declared_result_path,
    resolve_workspace_result_path,
)
from app.telemetry_events import GateOutcome
from app.telemetry_harness import send_result_gated
from app.text_utils import SafeStr


class SetTaskResultRequest(BaseModel):
    """A submission: the deliverable TEXT, or a DECLARED file holding it.

    ``result`` and ``result_file`` are alternatives — exactly one must be
    sent. Which of the two an agent means is its own statement; the
    server no longer guesses from the string's shape (no-space + ``.md``
    used to be refused as a "dangling pointer", which punished plain
    text results like ``report.md`` or ``/2025-Q3`` with a 400 and no
    round limit).
    """

    result: SafeStr | None = None
    # Declared result file, relative to the task workspace (or absolute
    # inside it). Read server-side; its contents become the result.
    result_file: SafeStr | None = None
    # The agent's own account of what it could NOT finish. Does not
    # bypass a single gate — the submission is reviewed exactly as
    # before. What it buys is an honest way to say "here is what I got,
    # here is what is missing" without reaching for set_task_error,
    # which means unrecoverable failure and was being used as an
    # escape hatch because it was the only door out.
    incomplete: list[SafeStr] | None = None

    @model_validator(mode='after')
    def _exactly_one_source(self):
        """Reject a submission that names neither or both deliverables."""
        if (self.result is None) == (self.result_file is None):
            raise ValueError(
                'pass exactly one of `result` (the text itself) or '
                '`result_file` (a file inside the task workspace) — '
                'not both, not neither'
            )
        return self


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


async def _read_off_loop(target) -> str:
    """Read a workspace file without blocking the event loop."""
    return await asyncio.to_thread(target.read_text, encoding='utf-8')


async def resolve_submitted_result(
    raw: str | None,
    result_file: str | None,
    task_root,
) -> tuple[str, bool]:
    """``(text_to_grade, came_from_a_file)``.

    The second element is not cosmetic: the endpoint reports it as
    ``resolved_from_file`` so the agent can tell whether its pointer was
    followed, and a contract test pins that key.

    Two ways to hand over a deliverable, and the server never has to
    guess which one was meant:

    * ``result_file`` — the agent DECLARES that its deliverable is a file
      in the task workspace. Read it (off the event loop: a report can be
      hundreds of KB). A declaration that names no such file is refused
      with the fix spelled out, because the agent already stated its
      intent — nothing is being inferred.
    * ``result`` — the text itself, always accepted as text.

    LEGACY pointer mode stays read-only-compatible: a ``result`` that
    happens to be a path to a real file inside the workspace is still
    read, because skill docs and prompts have told agents to pass
    ``"./AD_AUDIT_<date>.md"`` as ``result`` for as long as the tool has
    existed. It is never refused: the old shape test (no spaces + a
    document extension = "dangling pointer" → 400) punished a plain text
    result like ``report.md`` or ``/2025-Q3`` for LOOKING like a path,
    which is exactly the output-shape inference this codebase forbids.
    Whether a submission points at a file is now something the agent
    states.
    """
    if result_file is not None:
        target = resolve_declared_result_path(result_file, task_root)
        if target is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'result_file names no file inside this task '
                    f'workspace: {result_file!r}. Write the report first '
                    '(built-in Write/Edit, in your task workspace), then '
                    'pass its path as result_file — or send the whole '
                    'text as `result` and drop result_file.'
                ),
            )
        try:
            return await _read_off_loop(target), True
        except OSError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'result_file {result_file!r} exists but could not '
                    'be read — re-save it and submit again.'
                ),
            ) from None

    target = resolve_workspace_result_path(raw or '', task_root)
    if target is not None:
        try:
            return await _read_off_loop(target), True
        except OSError:
            # Not a refusal: the text stays what the agent submitted.
            pass
    return raw or '', False


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
