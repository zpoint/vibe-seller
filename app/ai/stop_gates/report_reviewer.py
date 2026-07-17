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

from pathlib import Path
import re

# Ad skills whose tasks carry a Definition-of-Done reviewer contract.
AD_SKILLS = frozenset({'amazon-ads', 'noon-ads', 'qianniu-ads'})

# Subdir the prior turn's review verdicts are moved into at the start of a
# new execution turn (see ``rollover_reviews``).
PREV_TURNS_DIR = '.prev_turns'

# Match a ``Status:`` verdict anywhere it can legitimately appear — plain
# at line start (``Status: ok``) OR emphasised/indented (``**Status:
# incomplete**``, ``- Status: gaps``). A reviewer that stamps a leading
# ``ok`` but concludes ``incomplete`` in a bolded footer is a real failure
# mode; the gate must SEE both lines so it can fail-closed on the conflict
# (see ``reviewer_verdict``). Case-insensitive so ``status:`` also counts.
_REVIEW_STATUS_RE = re.compile(
    r'^[\s*_>#-]*Status:\s*(\w+)', re.MULTILINE | re.IGNORECASE
)
_REVIEW_ITER_RE = re.compile(r'_iter(\d+)\.md$')
# A review file carries ``review`` as a distinct token (case-insensitive,
# not a substring of another word). Accepts ``REVIEW_...``,
# ``<PRODUCT>_REVIEW_...``, lowercase ``review_...`` — a weak model names
# it inconsistently. Rejects incidental substring matches like
# ``PREVIEW.md``. ``EXEC_`` (phase-4 execution review) is excluded
# separately.
_REVIEW_NAME_RE = re.compile(r'(?:^|[^a-z])review(?:[^a-z]|$)', re.IGNORECASE)


def rollover_reviews(task_dir) -> None:
    """Move the PRIOR turn's review verdicts aside at the start of a new
    execution turn, so this turn is reviewed on a clean slate.

    Universal review-gate design (every review-bound task, not one
    skill). A task that completed one deliverable and then gets a
    follow-up ("now also export it" / "also list it on the other
    marketplace") is a NEW turn with NEW work. Its review must cover
    THAT work — not inherit the prior turn's verdict. Leaving the old
    ``REVIEW_*/EXEC_REVIEW_*`` files in place let a follow-up (a) pass on
    a stale ``iter5=incomplete``, and (b) continue the iter numbering
    (``iter6``) so ``incomplete`` was accepted on its first pass. Moving
    them out gives a clean slate: iter restarts at 1 and no prior verdict
    can satisfy or misdirect this turn's gate. Files are preserved (moved,
    not deleted) under ``.prev_turns/`` for post-mortem. Best-effort;
    called once per turn at the ``system/init`` event.
    """
    if task_dir is None:
        return
    try:
        d = Path(task_dir)
        stale = [p for p in d.glob('*.md') if _REVIEW_NAME_RE.search(p.name)]
        if not stale:
            return
        root = d / PREV_TURNS_DIR
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f'turn_{1 + sum(1 for _ in root.glob("turn_*"))}'
        dest.mkdir(parents=True, exist_ok=True)
        for p in stale:
            try:
                p.rename(dest / p.name)
            except OSError:  # pragma: no cover
                pass
    except OSError:  # pragma: no cover — best-effort
        pass


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
    '> ⚠️ **Unverified result.** This deliverable completed WITHOUT a '
    'passing reviewer verdict — the DoD review loop stalled without '
    'reaching ``Status: ok``. Treat it as UNVERIFIED and spot-check '
    'against the source of truth (live page / export / file) before '
    'acting on it.\n\n'
)


def partial_banner() -> str:
    """Banner prepended to a result that failed open past the stall cap."""
    return _PARTIAL_BANNER


def effective_status(content: str) -> tuple[str | None, list[str]]:
    """The most-conservative ``Status:`` verdict stated in a review body.

    A review may (wrongly) state more than one ``Status:`` line — e.g. a
    leading ``ok`` with a bolded ``**Status: incomplete**`` conclusion.
    A DoD gate must never be fooled by the stray ``ok``, so this returns
    the verdict LEAST likely to pass: ``gaps`` (never passes) >
    ``incomplete`` (passes only at max iter) > ``ok`` — an unrecognised
    token is surfaced verbatim so the caller rejects it. Returns
    ``(status, raw_statuses)``; ``status`` is ``None`` when no ``Status:``
    line exists, and ``len(set(raw_statuses)) > 1`` signals a conflict.
    Shared by BOTH review gates (this module and the execution-review
    check in ``bash_safety``) so the fail-closed rule has one home.
    """
    statuses = [s.lower() for s in _REVIEW_STATUS_RE.findall(content)]
    if not statuses:
        return None, statuses
    if 'gaps' in statuses:
        return 'gaps', statuses
    if 'incomplete' in statuses:
        return 'incomplete', statuses
    if set(statuses) <= {'ok'}:
        return 'ok', statuses
    known = {'ok', 'gaps', 'incomplete'}
    return next(s for s in statuses if s not in known), statuses


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

    # Accept any ``.md`` whose name carries ``review`` as a token
    # (case-insensitive) except an EXEC_ (phase-4) one — a weak model
    # often names it ``<PRODUCT>_REVIEW_<date>.md`` or lowercase
    # ``review_...``; the Status line gates, not the exact filename. The
    # token match avoids incidental substrings (e.g. ``PREVIEW.md``).
    try:
        review_files = [
            p
            for p in task_dir.glob('*.md')
            if _REVIEW_NAME_RE.search(p.name)
            and not p.name.upper().startswith('EXEC_')
        ]
    except OSError:
        review_files = []
    # Prior-turn verdicts are moved to .prev_turns/ at turn start
    # (rollover_reviews), so a plain top-level glob already sees only
    # THIS turn's reviews — a follow-up can't inherit an earlier turn's
    # verdict or iter count.
    if not review_files:
        return (
            'Reviewer never ran. Before finalizing, spawn the DoD '
            'verification reviewer (the ``ads-report-review`` subagent, '
            'subagent_type="general-purpose") — it '
            'OPENS the live source of truth (console / page / export / '
            'file) and cross-verifies your deliverable per your skill'
            "'s DoD review loop (its ``references/dod-review-loop.md``, "
            'or ``amazon-ads/references/reviewer-loop.md`` for ads). If '
            'there was nothing substantive to review, it signs off fast. '
            'Write its result to ``REVIEW_<YYYY-MM-DD>_iter1.md`` in this '
            'workspace; re-run until Status: ok or iter '
            f'{REVIEW_MAX_ITERS} with Status: incomplete.'
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

    # Fail-closed on a self-contradictory review: a reviewer that stamps
    # a leading ``Status: ok`` but concludes ``incomplete``/``gaps`` in
    # its body (observed live — a mis-added total the reviewer caught yet
    # still top-stamped ``ok``) must be read at its MOST-CONSERVATIVE
    # verdict, never the stray ``ok``. ``effective_status`` owns that rule.
    status, statuses = effective_status(content)
    if status is None:
        return (
            f'{latest.name} has no ``Status:`` line. The reviewer '
            'output must begin with one of: ``Status: ok`` | '
            '``Status: gaps`` | ``Status: incomplete``. See '
            '``amazon-ads/references/reviewer-loop.md`` for the '
            'canonical format.'
        )

    iter_num = _iter_of(latest)
    conflict = len(set(statuses)) > 1
    conflict_note = (
        f' NOTE: {latest.name} states conflicting Status lines '
        f'{sorted(set(statuses))} — a review must reach ONE verdict. '
        'Rewrite it so the top ``Status:`` line matches your conclusion.'
        if conflict
        else ''
    )

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
            f'Status: incomplete.{conflict_note}'
        )
    if status == 'incomplete' and iter_num < REVIEW_MAX_ITERS:
        return (
            f'``Status: incomplete`` only valid at iter '
            f'{REVIEW_MAX_ITERS}+. Current is iter {iter_num} — '
            f'keep iterating.{conflict_note}'
        )
    return (
        f'Unknown reviewer status {status!r} in {latest.name}. '
        'Must be one of: ok | gaps | incomplete.'
    )
