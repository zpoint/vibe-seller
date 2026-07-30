"""Convergence accounting for the ad-completeness reviewer.

Split out of ``ad_completeness_review`` so both files stay under the
repo's per-file line limit — and because this is a distinct concern:
everything here answers "is the agent still making progress?", nothing
here reads a report.

The fail-open is keyed on STALL, not on a round count. Distance to done
(unmet gaps + campaigns still owed) is the one number that covers both
kinds of work: closing a rule gap drops the gap term, drilling a campaign
drops the deficit term.

The module-level dicts are per-process state keyed by task, mutated by
``ad_completeness_review.check`` each round and cleared by
``reset_progress`` on terminal success.
"""

import logging

logger = logging.getLogger(__name__)

# Fail-open is keyed on STALL, not a round count. A weak model that
# drills slowly (a few campaigns per round) still makes real progress
# every round — cutting it off after a fixed number of rounds would
# accept a half-finished report (e.g. noon 3/48) while D was still
# climbing.
#
# PROGRESS MEANS THE DISTANCE TO DONE SHRANK — where distance is
# ``unmet gaps + campaigns still owed``. Nothing else counts.
#
# This used to reset the counter whenever the drilled total climbed OR
# the report text moved by >=400 chars. The text-delta clause made the
# fail-open UNREACHABLE for any gap fixed by editing prose rather than
# by drilling — rule violations, format/parse gaps. Those never move
# the drilled total, but rewriting a suggestion column trivially moves
# far more than 400 characters, so every round reset the counter to
# zero. Observed: one run took 41 submissions and another 14, neither
# ever reaching STALL_CAP, both ending with no stored deliverable.
#
# Distance covers both kinds of work in one number: closing a rule gap
# drops the gap term, drilling a campaign drops the deficit term. A
# plain gap COUNT would not — an under-drilled combo emits exactly one
# gap entry at 3/48 and at 47/48 alike, so a slow driller would look
# stalled. An agent making either kind of progress keeps its budget; an
# agent rewriting the same report against a bar it cannot clear runs out.
STALL_CAP = 5

# Anti-regression: the highest drilled-count seen per (task_id, combo)
# across this task's rounds. The convergence loop must be MONOTONIC —
# each round adds, never loses prior drills. If a submission reports
# fewer drilled than a previous round (the model rewrote the whole
# report from compacted memory and clobbered earlier work — e.g. Amazon
# US 31/31 → 2/31), the reviewer rejects and tells it to restore +
# append. Cleared per task by ``reset_progress`` on terminal success.
_max_drilled: dict[tuple[str, str], int] = {}

# Stall tracking for the fail-open decision: the SMALLEST distance to
# done (unmet gaps + campaigns still owed) seen for a task so far, and
# how many consecutive rounds have failed to beat it. Updated by
# ``check`` each round; read by ``is_stalled``. Cleared by
# ``reset_progress``.
_min_distance: dict[str, int] = {}
_stall_rounds: dict[str, int] = {}

# Per-combo stall (D6): one frozen combo on a multi-combo task used to
# mask ongoing progress on the others — the global counter only fell
# when every combo moved at once. Track distance per (task_id, combo)
# so each combo's convergence is judged on its own. Global
# ``is_stalled`` only fires when every seen combo has hit STALL_CAP,
# which is what bounds a wholly-stuck agent; a single frozen combo
# fails open locally while the others keep their budget.
_combo_min_distance: dict[tuple[str, str], int] = {}
_combo_stall_rounds: dict[tuple[str, str], int] = {}
_seen_combos: dict[str, set[str]] = {}

# Stop-path backstop: how many times we've blocked an end-of-turn while
# the audit was still under-drilled, per task. Bounds the stop-path deny
# so a genuinely-stuck agent (can't drill more) isn't trapped forever —
# fails open after STALL_CAP blocks, mirroring the set_task_result stall.
_stop_blocks: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop the per-task progress/stall state (call on terminal success)."""
    for key in [k for k in _max_drilled if k[0] == task_id]:
        _max_drilled.pop(key, None)
    for key in [k for k in _combo_min_distance if k[0] == task_id]:
        _combo_min_distance.pop(key, None)
    for key in [k for k in _combo_stall_rounds if k[0] == task_id]:
        _combo_stall_rounds.pop(key, None)
    _seen_combos.pop(task_id, None)
    _min_distance.pop(task_id, None)
    _stall_rounds.pop(task_id, None)
    _stop_blocks.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """True after ``STALL_CAP`` rounds that got no closer to done.

    The fail-open signal: the report still has gaps and the agent has
    not reduced the distance to done — unmet gaps plus campaigns still
    owed — for ``STALL_CAP`` consecutive rounds, so further "what's
    missing" replies won't help. Callers use this to accept the best
    report instead of denying forever.

    With per-combo tracking (D6) the global signal fires only when
    EVERY seen combo has stalled — a single frozen combo on a multi-
    combo task fails open locally while the rest keep their budget.
    A task with no combo sections seen yet falls back to the legacy
    global counter so narrow single-combo audits still fail open.
    """
    seen = _seen_combos.get(task_id)
    if not seen:
        return _stall_rounds.get(task_id, 0) >= STALL_CAP
    return all(
        _combo_stall_rounds.get((task_id, c), 0) >= STALL_CAP for c in seen
    )


def drill_incomplete_reason(
    result_text: str,
    task_id: str | None = None,
) -> str | None:
    """Stop-path backstop: deny reason if the audit isn't complete.

    Unifies the two completion paths. ``set_task_result`` runs the full
    :func:`check`; but an agent can also finish by simply ENDING ITS TURN,
    which persists the streaming result WITHOUT that gate (the 3/24 bypass
    — see ``claude_backend_stream._save_result``). The Stop hook calls
    this so ending the turn is gated by the SAME contract as
    ``set_task_result``.

    Delegates to :func:`check` with ``track=False`` — passing ``task_id``
    so the AUDIT_SCOPE ground-truth (#1/#2) still loads, but suppressing
    the ``_max_drilled`` / stall mutation, so calling it on every Stop
    attempt can't perturb the ``set_task_result`` convergence accounting.
    This enforces the FULL contract (authoritative combo + active-id
    coverage, two-layer per-campaign completeness), closing the hole where
    a report with ``进度 D==A`` but missing campaigns/layers slipped
    through the count-only check on the ending-turn path.

    Bounded: after ``STALL_CAP`` blocks for a task it fails open, so an
    agent that genuinely cannot finish is not trapped. Returns None when
    :func:`check` passes, or once this task has been blocked ``STALL_CAP``
    times.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    # Imported here, not at module scope: ``ad_completeness_review``
    # imports this module for the shared state, so a top-level import
    # would be circular.
    from app.ai.stop_gates.ad_completeness_review import (  # noqa: PLC0415
        check,
    )

    deny = check(result_text, task_id, None, track=False)  # no mutation
    if deny is None:
        return None
    if task_id is not None:
        n = _stop_blocks.get(task_id, 0) + 1
        _stop_blocks[task_id] = n
        # ``>=``, matching ``is_stalled``. These two paths bound the
        # same contract and must agree: while the stop path fell open
        # one round later than the submit path, an agent could end its
        # turn but never get a submission accepted — which is how a run
        # reached a terminal state holding no stored deliverable.
        if n >= STALL_CAP:
            return None  # fail open — don't trap a stuck agent
    return (
        '还不能结束：审计报告尚未完成（未 drill 完所有 active campaign，'
        '或部分 campaign 缺少定向/搜索词层）。请补齐下列缺口后再结束；'
        '不要留待“下一轮/下次审计”：\n' + deny.reason
    )


def record_round(
    task_id: str | None,
    track: bool,
    gaps: list[str],
    round_deficit: int,
    combo_gaps: dict[str, list[str]],
    combo_deficit: dict[str, int],
) -> None:
    """Fold this round's result into the stall accounting.

    Progress = the DISTANCE TO DONE shrank, where distance is unmet gaps
    plus campaigns still owed. It falls for either kind of real work:
    closing a rule/format gap drops the first term, drilling a campaign
    drops the second. Text churn moves neither.

    A gap COUNT alone would be wrong — an under-drilled combo emits ONE
    gap entry whether it sits at a handful of campaigns or nearly all, so
    a slow driller would look stalled. The metric this replaced had the
    opposite bug: it reset on any sizeable edit, so an agent rewriting
    prose against a bar it could not clear was never stalled and looped.

    Per-combo, so one frozen marketplace cannot mask progress on the
    others. The global counter stays as a fallback for tasks that never
    wrote a ``## <platform> <country>`` section (narrow single-ad audits),
    which is what keeps those failing open.
    """
    if task_id is None or not track:
        return
    seen = _seen_combos.get(task_id)
    if seen:
        for label in seen:
            combo_distance = len(combo_gaps.get(label, [])) + combo_deficit.get(
                label, 0
            )
            key = (task_id, label)
            best = _combo_min_distance.get(key)
            if best is None or combo_distance < best:
                _combo_min_distance[key] = combo_distance
                _combo_stall_rounds[key] = 0
            else:
                _combo_stall_rounds[key] = _combo_stall_rounds.get(key, 0) + 1
        return
    distance = len(gaps) + round_deficit
    best = _min_distance.get(task_id)
    if best is None or distance < best:
        _min_distance[task_id] = distance
        _stall_rounds[task_id] = 0
    else:
        _stall_rounds[task_id] = _stall_rounds.get(task_id, 0) + 1


def clear_all() -> None:
    """Drop EVERY task's progress state.

    For tests, which need a clean slate between cases. A public function
    rather than seven module globals a fixture has to list by name: the
    fixtures used to enumerate them, so any new counter added here would
    have leaked between tests until someone noticed a spurious pass.
    """
    for store in (
        _max_drilled,
        _min_distance,
        _stall_rounds,
        _combo_min_distance,
        _combo_stall_rounds,
        _seen_combos,
        _stop_blocks,
    ):
        store.clear()
