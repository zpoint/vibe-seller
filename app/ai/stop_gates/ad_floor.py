"""The ad report's coverage floor, as the Stop hook applies it.

The floor is ``ad_completeness_review``'s own check. It refuses at most
once per task across both completion paths: ``set_task_result`` spends
that refusal when it makes it, and here — a path that is also polled by
the idle watchdog — the budget is only looked at, never spent.
"""

from __future__ import annotations

from app.ai.stop_gates import ad_completeness_review, review_round_left


def drill_floor(audit_text: str, task_id: str) -> str | None:
    """The floor's refusal, or None when met or its round is spent."""
    floor = ad_completeness_review.drill_incomplete_reason(audit_text, task_id)
    if floor is not None and review_round_left(
        task_id, 'ad_completeness_review'
    ):
        return floor
    return None
