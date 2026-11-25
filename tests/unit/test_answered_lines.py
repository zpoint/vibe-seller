"""The transcript text for answers to an agent's questions."""

import pytest

from app.question_park import answered_lines

pytestmark = pytest.mark.unit


def _qs(*texts):
    return [{'question': t, 'options': [{'label': 'x'}]} for t in texts]


def test_one_question():
    assert answered_lines(_qs('Country?'), {'Country?': 'UAE'}) == (
        'Q: Country?\nA: UAE'
    )


def test_four_questions_options_and_custom_mixed():
    qs = _qs('Country?', 'Tone?', 'Length?', 'Emoji?')
    answers = {
        'Country?': 'UAE',
        'Tone?': 'warm but short, no slang',
        'Length?': 'One line',
        'Emoji?': 'none',
    }
    assert answered_lines(qs, answers) == (
        'Q: Country?\nA: UAE\n\n'
        'Q: Tone?\nA: warm but short, no slang\n\n'
        'Q: Length?\nA: One line\n\n'
        'Q: Emoji?\nA: none'
    )


def test_one_free_text_reply_to_all_questions_is_one_pair():
    qs = _qs('Country?', 'Tone?')
    assert answered_lines(qs, {'_free_text': ' Saudi, formal '}) == (
        'Q: Country?\nTone?\nA: Saudi, formal'
    )


def test_an_unanswered_question_keeps_its_place():
    assert answered_lines(_qs('A?', 'B?'), {'B?': 'yes'}) == (
        'Q: A?\nA: \n\nQ: B?\nA: yes'
    )
