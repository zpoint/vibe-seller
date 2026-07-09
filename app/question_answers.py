"""Translate the QuestionBanner free-text sentinel into the
per-question answers shape the agent's AskUserQuestion tool renders.

Issue #211: the 'Type freely instead' mode in
``frontend/src/components/QuestionBanner.tsx`` submits
``{'_free_text': '<text>'}`` instead of the
``{question_text: selected_label}`` map produced by the preset-option
and 'Other' paths.

The agent (claude-code) renders the AskUserQuestion tool result by
iterating the asked *questions* and looking each answer up **by
question text** — verified against the
``mapToolResultToToolResultBlockParam`` body in the bundled
``@anthropic-ai/claude-code`` binary. There is no top-level ``response``
field. An un-translated
sentinel therefore matches no question and renders empty:

    Your questions have been answered: .

Expanding the sentinel server-side onto every asked question routes
free text through the exact path preset options / 'Other' use, which
has always worked across claude-code versions. This is the contract
boundary, so the translation lives here (server-side) rather than in
prompts or the frontend — see CLAUDE.md "Fix from design".
"""

FREE_TEXT_KEY = '_free_text'


def default_answers(questions: list[dict]) -> dict:
    """Server-side default answers for an AskUserQuestion nobody answered.

    Used when no human responds within the answer window (unattended /
    scheduled runs) so the agent proceeds instead of hanging. Picks the
    FIRST option per question — the ``AskUserQuestion`` convention puts
    the recommended option first — as a list for ``multiSelect``; falls
    back to a generic "proceed with defaults" text for a question with no
    options. Keyed by question text (the shape the agent renders by),
    matching ``expand_free_text_answers``.
    """
    out: dict = {}
    for q in questions:
        if not isinstance(q, dict):
            continue
        key = q.get('question') or q.get('header') or ''
        if not key:
            continue
        options = q.get('options') or []
        if options:
            first = options[0]
            label = (
                first.get('label', '')
                if isinstance(first, dict)
                else str(first)
            )
            out[key] = [label] if q.get('multiSelect') else label
        else:
            out[key] = 'Proceed with sensible defaults.'
    return out


def expand_free_text_answers(answers: dict, questions: list[dict]) -> dict:
    """Return *answers* with the free-text sentinel expanded.

    If *answers* carries the ``_free_text`` sentinel, replace it with
    the operator's text keyed by every asked question's text (so the
    agent renders it on the proven per-question path). Any answers the
    operator already keyed by question text are preserved. When the
    question list is unavailable (e.g. a resume payload that lost it),
    the text is preserved under the sentinel key so downstream
    rendering can still surface it rather than dropping it.

    Returns *answers* unchanged when there is no usable sentinel.
    """
    if not isinstance(answers, dict):
        return answers
    free = answers.get(FREE_TEXT_KEY)
    if not isinstance(free, str) or not free.strip():
        return answers
    free = free.strip()

    expanded = {k: v for k, v in answers.items() if k != FREE_TEXT_KEY}
    q_texts = [
        q['question']
        for q in questions
        if isinstance(q, dict) and q.get('question')
    ]
    for q_text in q_texts:
        expanded.setdefault(q_text, free)
    if not q_texts and not expanded:
        # No questions to key against — keep the text so the resume
        # prefix can still render it instead of losing it entirely.
        expanded[FREE_TEXT_KEY] = free
    return expanded
