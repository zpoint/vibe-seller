"""Check that the result's prose is in the user's language.

The user's language is inferred from the task title + description
(same heuristic as ``detect_language_hint``). The result is "in the
user's language" when ≥ 85 % of the prose lingua-py confidently
attributes to one of our candidate languages falls in the right one.

Why lingua-py and not regex exemption lists: we used to strip
identifiers / metric names / numeric tokens with regex before
counting CJK vs Latin chars. That's rule-by-rule whack-a-mole. lingua
already segments mixed-language text and gives a per-segment
language attribution that's the same model the user's eyes apply —
"this run of characters is English; that run is Chinese." We just
ask which fraction it puts in the expected bucket.
"""

from __future__ import annotations

import re

from lingua import Language, LanguageDetectorBuilder

from app.ai.stop_gates import GateDeny

GATE_NAME = 'result_language'

# Minimum fraction of detected prose that must be in the user's
# expected language. Chosen to leave room for legitimately-unanglified
# fragments (technical identifiers, verbatim search terms, metric
# names) without letting whole-paragraph mistranslations through.
LANGUAGE_MIN_RATIO = 0.85

# Code blocks aren't prose. Strip them before language detection so
# Python / shell snippets don't get attributed to English and skew
# a Chinese audit's ratio.
_FENCED_CODE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_CODE_RE = re.compile(r'`[^`]+`')

# Two-language detector covers our deployed locales (zh / en).
# Extending to more is a matter of adding Language.* entries.
_DETECTOR = LanguageDetectorBuilder.from_languages(
    Language.ENGLISH, Language.CHINESE
).build()


def detect_expected_language(title: str, description: str | None) -> str:
    """Return 'zh' or 'en' for the user's expected output language.

    Matches the heuristic in ``detect_language_hint``: any CJK char
    in title/description means Chinese.
    """
    text = (title or '') + (description or '')
    if any('一' <= ch <= '鿿' for ch in text):
        return 'zh'
    return 'en'


def _strip_code(text: str) -> str:
    text = _FENCED_CODE_RE.sub(' ', text)
    text = _INLINE_CODE_RE.sub(' ', text)
    return text


def _language_ratio(text: str, expected: str) -> tuple[float, int, int]:
    """Compute (chars_in_expected / chars_in_any_known_language).

    Uses lingua-py's ``detect_multiple_languages_of`` to segment mixed
    text. Segments lingua can't confidently attribute (e.g., short
    alphanumeric identifiers, metric names) are simply ignored — they
    don't count toward either bucket. Returns (ratio, expected_chars,
    other_chars); ratio is 1.0 when nothing was attributable.
    """
    cleaned = _strip_code(text)
    if not cleaned.strip():
        return 1.0, 0, 0
    expected_lang = Language.CHINESE if expected == 'zh' else Language.ENGLISH
    in_lang = 0
    other = 0
    for result in _DETECTOR.detect_multiple_languages_of(cleaned):
        seg_len = result.end_index - result.start_index
        if result.language == expected_lang:
            in_lang += seg_len
        else:
            other += seg_len
    total = in_lang + other
    if total == 0:
        return 1.0, 0, 0
    return in_lang / total, in_lang, other


def check(
    result_text: str, title: str, description: str | None
) -> GateDeny | None:
    """Return a ``GateDeny`` when the result's prose ratio in the
    user's language is below the threshold; otherwise ``None``.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    expected = detect_expected_language(title, description)
    ratio, in_lang, other = _language_ratio(result_text, expected)
    if ratio >= LANGUAGE_MIN_RATIO:
        return None
    if expected == 'zh':
        reason = (
            f'The user wrote the task in Chinese, but only '
            f'{ratio * 100:.0f}% of your result is detected as '
            f'Chinese ({in_lang} chars vs {other} chars in another '
            'language). Re-write prose cells (e.g., Recommendation '
            'columns, analysis paragraphs) in Chinese. Keep '
            'identifiers (campaign IDs, SKUs, ASINs, verbatim search '
            'terms) and metric names (ROAS, ACOS, CTR, CVR) in their '
            'original form — those are exempt. Then call '
            'set_task_result again.'
        )
    else:
        reason = (
            f'The user wrote the task in English, but only '
            f'{ratio * 100:.0f}% of your result is detected as '
            f'English ({in_lang} chars vs {other} chars in another '
            'language). Re-write prose in English and call '
            'set_task_result again.'
        )
    return GateDeny(gate=GATE_NAME, reason=reason)
