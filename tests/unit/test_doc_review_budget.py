"""The doc-review call must leave room for the model's thinking.

GLM bills thinking against ``max_tokens``. At the old 4096 budget the
thinking alone filled it on docs-heavy PRs, the answer never arrived,
and doc-review failed on every large PR with "no text block". These
tests pin the budget the call sends and that a budget-exhausted answer
is reported as such (not as a parse error).
"""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / '.github' / 'scripts')
)
import doc_review  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.unit


class _FakeMessages:
    def __init__(self, message):
        self.message = message
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.message


def _client(message):
    return SimpleNamespace(messages=_FakeMessages(message))


def test_budget_covers_thinking_plus_answer():
    answer = '{"summary": "ok", "missing_docs": [], "comments": []}'
    msg = SimpleNamespace(
        stop_reason='end_turn',
        content=[
            SimpleNamespace(type='thinking', thinking='x' * 100),
            SimpleNamespace(type='text', text=answer),
        ],
    )
    client = _client(msg)
    result = doc_review.call_llm_api(client, 'glm', 'diff', 'docs')
    assert result['summary'] == 'ok'
    # Measured: a 1k-line docs PR used ~3.6k tokens of thinking + answer;
    # 4096 is what broke. Keep real headroom above that.
    assert client.messages.kwargs['max_tokens'] >= 16000


def test_exhausted_budget_is_reported_as_budget():
    msg = SimpleNamespace(
        stop_reason='max_tokens',
        content=[SimpleNamespace(type='thinking', thinking='x' * 100)],
    )
    with pytest.raises(RuntimeError, match='output budget'):
        doc_review.call_llm_api(_client(msg), 'glm', 'diff', 'docs')
