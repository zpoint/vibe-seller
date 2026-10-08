"""An ad task's result is refused at most once per check, on any path.

Observed in an end-to-end run with the one-round rule in place:
``set_task_result`` refused once on ``ad_completeness_review`` and then
accepted — and the Stop hook went on refusing the very same gaps through
its own floor, a second round by another door. The two paths now share one
budget per check, spent where a refusal is actually made.
"""

import pytest

from app.ai import stop_gates
from app.ai.bash_safety import check_review_status
from app.ai.stop_gates import (
    AD_REVIEW_ROUNDS,
    review_round_left,
    take_review_round,
)

pytestmark = pytest.mark.unit

UNDERDRILLED = (
    '# 广告优化建议\n\n## Amazon US\n\n**进度**: drilled 4/31 active\n'
)


def test_looking_spends_nothing():
    for _ in range(5):
        assert review_round_left('t-look', 'some_gate')
    assert take_review_round('t-look', 'some_gate')


def test_one_refusal_then_through():
    assert AD_REVIEW_ROUNDS == 1
    assert take_review_round('t-once', 'some_gate') is True
    assert take_review_round('t-once', 'some_gate') is False
    assert review_round_left('t-once', 'some_gate') is False


def test_the_budget_survives_an_accepted_result():
    """reset_attempts runs on acceptance; the Stop hook runs after it."""
    take_review_round('t-acc', 'some_gate')
    stop_gates.reset_attempts('t-acc')
    assert review_round_left('t-acc', 'some_gate') is False


@pytest.fixture
def ad_task(tmp_path, monkeypatch):
    monkeypatch.setattr(
        'app.ai.bash_safety.recorded_skills', lambda _tid: {'amazon-ads'}
    )
    task = tmp_path / 'task-rounds-1'
    task.mkdir()
    (task / 'AD_AUDIT_2026-06-10.md').write_text(UNDERDRILLED, encoding='utf-8')
    return task


def test_the_stop_floor_refuses_while_the_round_is_unspent(ad_task):
    deny = check_review_status(ad_task)
    assert deny is not None and '4/31' in deny


def test_the_stop_floor_stands_down_once_submit_spent_the_round(ad_task):
    take_review_round(ad_task.name, 'ad_completeness_review')
    deny = check_review_status(ad_task)
    # The floor's gaps were already refused once; what is left is the
    # single reviewer pass, not the same drill gaps again.
    assert deny is None or '4/31' not in deny
