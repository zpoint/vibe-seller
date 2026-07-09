"""Server-side AskUserQuestion answer helpers.

``default_answers`` is the auto-answer used when nobody responds within
the window (unattended/scheduled runs) so the agent proceeds instead of
hanging (F2). ``expand_free_text_answers`` handles the UI free-text
sentinel (issue #211).
"""

import pytest

from app.question_answers import default_answers, expand_free_text_answers


@pytest.mark.unit
class TestDefaultAnswers:
    def test_first_option_per_question(self):
        qs = [
            {
                'question': 'Which country?',
                'options': [{'label': 'SA'}, {'label': 'AE'}],
            },
        ]
        assert default_answers(qs) == {'Which country?': 'SA'}

    def test_multiselect_returns_list(self):
        qs = [
            {
                'question': 'Which?',
                'multiSelect': True,
                'options': [{'label': 'A'}, {'label': 'B'}],
            }
        ]
        assert default_answers(qs) == {'Which?': ['A']}

    def test_no_options_falls_back_to_text(self):
        qs = [{'question': 'Free?', 'options': []}]
        assert default_answers(qs) == {
            'Free?': 'Proceed with sensible defaults.'
        }

    def test_header_used_when_no_question_text(self):
        qs = [{'header': 'Scope', 'options': [{'label': 'X'}]}]
        assert default_answers(qs) == {'Scope': 'X'}

    def test_empty_and_malformed_ignored(self):
        assert default_answers([]) == {}
        assert (
            default_answers(['not a dict', {}, {'options': [{'label': 'x'}]}])
            == {}
        )


@pytest.mark.unit
class TestExpandFreeText:
    def test_sentinel_expands_to_each_question(self):
        qs = [{'question': 'Q1'}, {'question': 'Q2'}]
        out = expand_free_text_answers({'_free_text': 'go'}, qs)
        assert out == {'Q1': 'go', 'Q2': 'go'}

    def test_no_sentinel_unchanged(self):
        assert expand_free_text_answers({'Q1': 'A'}, [{'question': 'Q1'}]) == {
            'Q1': 'A'
        }
