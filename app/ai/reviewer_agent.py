"""The product-defined DoD verification reviewer.

The stop gates promise an "``ads-report-review`` subagent" — before
this module that name existed ONLY in the gate's deny text. The main
agent was told to spawn ``subagent_type="general-purpose"`` and invent
the reviewer's brief from prose ("it signs off fast if there was
nothing to verify"), and the improvised subagent went spelunking
code, databases and transcripts for minutes on a turn with nothing to
verify — "done" was unbounded. A prose promise is not a contract.

The contract now lives in code, the way the CLI supports it: the task
launch registers a REAL agent type (``--agents`` JSON — prompt and
tools) whose brief is this module's ``REVIEWER_PROMPT``, and the
gate's demand names that type and feeds it a MANIFEST the server
builds (this turn's result text, its files, the declaration). The
reviewer verifies what the manifest names — bounded by construction —
and writes one verdict file.
"""

import json
from pathlib import Path

REVIEWER_AGENT_NAME = 'ads-report-review'

REVIEWER_PROMPT = """You are the DoD verification reviewer (the \
`ads-report-review` agent). The server built your input: a MANIFEST of \
exactly what this turn produced — the result text, the files written \
this turn, the ad declaration (if any), the DoD review loop file that \
governs the deliverable, and the absolute path your verdict must be \
written to.

Your job is bounded and complete in one pass:

1. Read the DoD review loop file the manifest names FIRST (usually \
`references/dod-review-loop.md` in the loaded skill, or \
`amazon-ads/references/reviewer-loop.md`). Apply its checklist to the \
deliverable in the manifest.
2. Where the loop demands verification against a live source of truth \
(a console page, an export, a file the report covers), open THAT \
source and cross-check the manifest's claims. Never a source the \
manifest does not name.
3. Write the verdict to the absolute path the manifest names \
(`REVIEW_<YYYY-MM-DD>_iter1.md`) with a line `Status: ok` (verified, \
or nothing substantive to verify — a lookup, a smoke test), \
`Status: gaps` (then list each gap), or `Status: incomplete` (you are \
blocked; say by what). Keep it short.

Your scope is the manifest and the one deliverable it covers. Do NOT
explore code, databases, transcripts, or any file the manifest does
not name — an unbounded search is a failure of this role, and it is
what this agent exists to prevent."""


EXEC_REVIEWER_NAME = 'ads-execution-review'

EXEC_REVIEWER_PROMPT = """You are the ads-execution reviewer (the `ads-execution-review` agent). The gate's demand named you and the sources to read; those named sources are your entire input.

Your job is bounded and complete in one pass: diff every applied Recommendation against the on-disk proof of the live state — the audit (the recommendations), the execution log (the claims and their read-back verification), the anchor rules file, and the per-campaign TSV rows with `applied_at`. Surface anything missing, claimed-but-unverified, or applied to the wrong value.

Write the verdict to the absolute path the demand names (`EXEC_REVIEW_<YYYY-MM-DD>_iter<N>.md`) with a line `Status: ok` (every recommendation applied and verified), `Status: gaps` (then list each gap), or `Status: incomplete` (you are blocked; say by what). Keep it short.

Your scope is the named sources and the one execution they cover. Do
NOT explore code, databases, transcripts, or any file the demand does
not name — an unbounded search is a failure of this role, and it is
what this agent exists to prevent."""


def reviewer_agents_payload() -> dict:
    """The ``--agents`` registration for a task launch.

    Every reviewer a stop gate names is a REAL type here — a gate
    demand naming a type that does not exist is the improvisation
    surface this module exists to close (the main agent made the
    reviewer's brief up and its work was unbounded). Each prompt is
    the contract and its tools are the ones a verifier needs: read the
    loop and the deliverable, and open the named source of truth.
    """
    return {
        REVIEWER_AGENT_NAME: {
            'description': (
                'DoD verification reviewer: cross-verifies one turn’s '
                'deliverable against its manifest and the source of '
                'truth the report covers, then writes a REVIEW_ verdict '
                'file.'
            ),
            'prompt': REVIEWER_PROMPT,
            'tools': ['Read', 'Bash', 'Grep', 'Glob'],
        },
        EXEC_REVIEWER_NAME: {
            'description': (
                'Ads-execution reviewer: diffs the audit’s applied '
                'Recommendations against the on-disk proof of the live '
                'state and writes an EXEC_REVIEW_ verdict file.'
            ),
            'prompt': EXEC_REVIEWER_PROMPT,
            'tools': ['Read', 'Bash', 'Grep', 'Glob'],
        },
    }


def write_agents_file(task_id: str, task_dir) -> Path:
    """Write the launch's ``--agents`` file; return its path.

    ``--print`` launches want a FILE path, so the registration is
    written beside the session's other launch artifacts and passed
    once: the reviewer type exists for every subagent spawn in the
    session, with the prompt above and no improvisation.
    """
    # Lazy: import cycle — claude_backend_utils -> bash_safety ->
    # report_reviewer -> here. noqa with the reason is the house
    # pattern for a genuine cycle (see app/vision.py).
    from app.ai.claude_backend_utils import (  # noqa: PLC0415
        system_prompt_file,
    )

    path = system_prompt_file(task_id, task_dir).with_suffix('.agents.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(reviewer_agents_payload(), ensure_ascii=False),
        encoding='utf-8',
    )
    return path
