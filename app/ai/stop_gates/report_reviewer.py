"""Shared ads-report reviewer-verdict gate.

An ads-report task is not *done* just because the deterministic coverage
floor passed — a shallow-but-covering report (every campaign named, none
drilled to the word level) sails through the floor. The active
``ads-report-review`` subagent OPENS the live console / export and
cross-verifies the report, writing ``REVIEW_<date>_iter<N>.md`` with a
``Status:`` line. This module is the *enforcement*: completion is denied
until that verdict says ``ok`` (or ``incomplete`` at iter ≥ MAX).

It is called from BOTH completion paths so the reviewer can't be skipped
by choosing one:

- the Stop hook / ending-turn (``bash_safety.check_review_status``), and
- the ``set_task_result`` MCP call (``routers/tasks.py``).

Some backends never emit a Stop event and finish by calling
``set_task_result`` directly; gating only the Stop hook let those runs
complete a shallow report with the reviewer never spawned. Keeping the
verdict a precondition on *every* path is the design fix — the reviewer
is invoked because the task cannot end without its sign-off, not because
the agent chose to spawn it.

The deterministic floor (``ad_completeness_review``) stays separate and
runs first on each path; this module assumes the floor already passed
and only gates on the reviewer artifact.
"""

from __future__ import annotations

import re

# Ad skills whose tasks carry a Definition-of-Done reviewer contract.
AD_SKILLS = frozenset({'amazon-ads', 'noon-ads', 'qianniu-ads'})

_REVIEW_STATUS_RE = re.compile(r'^Status:\s*(\w+)', re.MULTILINE)
_REVIEW_ITER_RE = re.compile(r'_iter(\d+)\.md$')

# Max iterations before ``incomplete`` is accepted as terminal (matches
# the loop cap in ``amazon-ads/references/reviewer-loop.md``).
REVIEW_MAX_ITERS = 5

# Fail-open cap on the set_task_result path: after this many reviewer
# denials for one task, let the result through so a weak-but-stuck model
# is not trapped — but the result is banner-marked UNVERIFIED, never
# silently "done". Named to match the stall design in
# ``ad_completeness_review``.
REVIEWER_STALL_CAP = 5

_PARTIAL_BANNER = (
    '> ⚠️ **Unverified ad report.** This report completed WITHOUT a '
    'passing reviewer verdict — the ``ads-report-review`` loop stalled '
    'without reaching ``Status: ok``. Treat every finding as UNVERIFIED '
    'and spot-check against the live console before acting on it.\n\n'
)


def partial_banner() -> str:
    """Banner prepended to a result that failed open past the stall cap."""
    return _PARTIAL_BANNER


def reviewer_verdict(task_dir) -> str | None:
    """Deny reason if the reviewer hasn't signed off; else ``None``.

    Called for EVERY ads-skill-bound task (the caller established the
    binding and that any deterministic floor already passed). Gates
    purely on the ``*REVIEW*.md`` verdict artifact — the reviewer itself
    decides what "done" means: on a real report it drills and
    cross-checks; on a task with nothing substantive to verify (a quick
    metric lookup) it signs off ``Status: ok`` fast. The server never
    pre-judges "report vs lookup"; it only requires the verdict.
    """
    if task_dir is None:
        return None

    # Accept ANY ``*REVIEW*.md`` except an EXEC_ (phase-4) one — a weak
    # model often names it ``<PRODUCT>_REVIEW_<date>.md``; the Status line
    # gates, not the exact filename.
    try:
        review_files = [
            p
            for p in task_dir.glob('*REVIEW*.md')
            if not p.name.startswith('EXEC_')
        ]
    except OSError:
        review_files = []
    if not review_files:
        return (
            'Reviewer never ran. Before finalizing, spawn the '
            '``ads-report-review`` subagent (subagent_type='
            '"general-purpose") — it OPENS the live console/export and '
            'cross-verifies your report per '
            '``amazon-ads/references/reviewer-loop.md`` (if there was '
            'nothing substantive to review, it signs off fast) — and '
            'write its result to ``REVIEW_<YYYY-MM-DD>_iter1.md`` in '
            'this workspace. Re-run reviewer until Status: ok or until '
            f'iter {REVIEW_MAX_ITERS} with Status: incomplete.'
        )

    def _iter_of(p):
        m = _REVIEW_ITER_RE.search(p.name)
        return int(m.group(1)) if m else 0

    # Pick the most recently WRITTEN review file, not the highest iter
    # number. A workspace can accumulate REVIEW files from several audit
    # cycles; choosing by iter number would gate today's audit against a
    # stale higher-iter verdict from yesterday. mtime tracks the current
    # audit correctly.
    try:
        latest = max(review_files, key=lambda p: p.stat().st_mtime)
    except OSError:
        latest = max(review_files, key=_iter_of)
    try:
        content = latest.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return f'{latest.name} could not be read; rewrite the review file.'

    match = _REVIEW_STATUS_RE.search(content)
    if not match:
        return (
            f'{latest.name} has no ``Status:`` line. The reviewer '
            'output must begin with one of: ``Status: ok`` | '
            '``Status: gaps`` | ``Status: incomplete``. See '
            '``amazon-ads/references/reviewer-loop.md`` for the '
            'canonical format.'
        )

    status = match.group(1).lower()
    iter_num = _iter_of(latest)
    if status == 'ok':
        return None
    if status == 'incomplete' and iter_num >= REVIEW_MAX_ITERS:
        return None  # accept as terminal; gaps are on-disk for post-mortem
    if status == 'gaps':
        return (
            f'Reviewer iter {iter_num} found gaps in the audit. '
            f'Read {latest.name} for the list, fix the audit in '
            f'place (Edit tool, not re-drill), then spawn the '
            'reviewer again to write '
            f'``REVIEW_*_iter{iter_num + 1}.md``. Repeat until '
            f'Status: ok or iter {REVIEW_MAX_ITERS} with '
            'Status: incomplete.'
        )
    if status == 'incomplete' and iter_num < REVIEW_MAX_ITERS:
        return (
            f'``Status: incomplete`` only valid at iter '
            f'{REVIEW_MAX_ITERS}+. Current is iter {iter_num} — '
            'keep iterating.'
        )
    return (
        f'Unknown reviewer status {status!r} in {latest.name}. '
        'Must be one of: ok | gaps | incomplete.'
    )
