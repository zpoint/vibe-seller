"""Map an operator's answers onto the FULL question text.

The agent renders an ``AskUserQuestion`` result by looking each answer
up **by question text** — there is no top-level ``response`` field, and
the header is only a label. An answer keyed by the header (or by an
index, or by a paraphrase) therefore matches nothing and the agent
receives an empty answer ("Your questions have been answered: .").

The web UI never hits this because it is the browser that builds the
map, keyed by ``q.question``. An external agent has no such browser:
it reads the questions as prose and answers with whatever key it finds
natural. So the mapping has to happen **server-side**, in the tool,
before the answer reaches the agent — see ``app/question_answers.py``
for the same class of problem on the free-text path.

Accepted keys for a question:

* the full question text (always correct),
* the question's ``header`` (the short label),
* ``q1`` / ``1`` / ``question 1`` (1-based position).

With exactly one pending question any single answer is unambiguous,
so an unrecognised key is accepted for it rather than bounced back at
an agent that cannot guess our key format. With several questions an
unrecognised key is an error that names the valid keys, because
guessing which question it meant would silently answer the wrong one.
"""

from app.question_answers import FREE_TEXT_KEY


class UnknownQuestionKey(ValueError):
    """An answer key that matches none of the pending questions."""


def _normalise_index(key: str) -> int | None:
    """Return the 1-based question index *key* names, if any."""
    token = key.strip().lower()
    for prefix in ('question ', 'question', 'q'):
        if token.startswith(prefix):
            token = token[len(prefix) :].strip()
            break
    if token.isdigit():
        return int(token)
    return None


def question_keys(questions: list[dict]) -> dict[str, str]:
    """Map every accepted key for a question to its full question text.

    The returned mapping is keyed by the *caller's* key and valued by
    the full question text — i.e. it is the inverse of what the agent
    needs, ready to be applied.
    """
    keys: dict[str, str] = {}
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        text = question.get('question')
        if not text:
            continue
        keys[text] = text
        header = question.get('header')
        if header:
            keys.setdefault(header, text)
        keys.setdefault(f'q{index}', text)
        keys.setdefault(str(index), text)
        keys.setdefault(f'question {index}', text)
    return keys


def map_answers(
    answers: dict,
    questions: list[dict],
) -> dict[str, str]:
    """Return *answers* keyed by the full text of each asked question.

    Raises :class:`UnknownQuestionKey` when a key matches no question
    and the choice is ambiguous (more than one question pending).
    """
    if not isinstance(answers, dict):
        raise UnknownQuestionKey('answers must be an object')
    if not answers:
        raise UnknownQuestionKey('answers must not be empty')

    # The UI free-text sentinel is expanded downstream (live sessions
    # by the AskUserQuestion hook, parked sessions by the resume
    # prompt). Pass it through untouched — expanding it here would
    # need the same question list the sentinel exists to bypass.
    if FREE_TEXT_KEY in answers:
        return {str(k): str(v) for k, v in answers.items()}

    keys = question_keys(questions)
    unique_texts = list(dict.fromkeys(keys.values()))

    mapped: dict[str, str] = {}
    unknown: list[str] = []
    for key, value in answers.items():
        text = keys.get(str(key))
        if text is None:
            unknown.append(str(key))
            continue
        mapped[text] = '' if value is None else str(value)

    if unknown:
        if len(unique_texts) == 1 and len(answers) == 1:
            # One question, one answer: the key is irrelevant.
            mapped[unique_texts[0]] = str(next(iter(answers.values())))
        else:
            raise UnknownQuestionKey(
                'Unknown question key(s): '
                + ', '.join(repr(k) for k in unknown)
                + '. Valid keys: '
                + ', '.join(repr(k) for k in keys)
            )
    return mapped
