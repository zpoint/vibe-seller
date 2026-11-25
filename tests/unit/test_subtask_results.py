"""A parent's aggregated result: one readable section per sub-task."""

from types import SimpleNamespace

import pytest

from app.subtask_results import MAX_CHILD_CHARS, aggregate_child_results
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


def _kid(title, status, result=None, error=None):
    return SimpleNamespace(
        id='0123456789ab',
        title=title,
        status=status,
        result=result,
        error=error,
    )


def test_each_subtask_is_its_own_section():
    parent = SimpleNamespace(result=None)
    aggregate_child_results(
        parent,
        [
            _kid(
                'Open orders — store-a',
                TaskStatus.COMPLETED,
                result='store-a has 12 open orders.',
            ),
            _kid(
                'Open orders — store-b',
                TaskStatus.FAILED,
                error='Not signed in to the seller center.',
            ),
        ],
    )
    sections = parent.result.split('\n\n### ')
    assert len(sections) == 2
    assert sections[0] == (
        '### ✓ Open orders — store-a\n\nstore-a has 12 open orders.'
    )
    assert sections[1] == (
        '✗ Open orders — store-b\n\nNot signed in to the seller center.'
    )


def test_a_short_result_is_kept_whole():
    # The answer is often after a sentence of context; a 200-char cut
    # used to drop it.
    text = 'Checked the orders page after signing in. ' * 8 + 'Total: 42.'
    assert len(text) < MAX_CHILD_CHARS
    parent = SimpleNamespace(result=None)
    aggregate_child_results(parent, [_kid('a', TaskStatus.COMPLETED, text)])
    assert parent.result.endswith('Total: 42.')


def test_a_long_result_is_cut_at_a_word_with_a_pointer():
    text = 'word ' * MAX_CHILD_CHARS
    parent = SimpleNamespace(result=None)
    aggregate_child_results(parent, [_kid('a', TaskStatus.COMPLETED, text)])
    body = parent.result.split('\n\n', 1)[1]
    assert body.endswith('word … (full result in the subtask)')
    assert len(body) < MAX_CHILD_CHARS + 50
