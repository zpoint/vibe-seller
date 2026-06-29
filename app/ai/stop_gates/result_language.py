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

# Minimum fraction of NON-TABLE prose that must be in the user's
# expected language. Set deliberately lenient: ad-audit reports are
# inherently bilingual — Chinese analysis interleaved with mandatory
# English tokens (metric names ROAS/ACOS/CTR, campaign/ASIN identifiers,
# the machine-readable "drilled D/A active" progress line, English
# campaign-type labels). Demanding a high ratio put this gate in direct
# conflict with the data-table format and the progress-line contract and
# trapped the agent oscillating between gates. The gate's real job is
# only to stop a wholly-English report; 0.35 over non-table prose
# catches that (a real English report scores near 0) while letting a
# genuine Chinese-narrative report through. Table rows are excluded from
# the ratio entirely (they are data, not prose).
LANGUAGE_MIN_RATIO = 0.35

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


def _alpha_len(text: str) -> int:
    """Count letters + CJK ideographs — the chars that carry language.

    Digits, punctuation, currency symbols and whitespace are ignored so
    a numeric-heavy table row doesn't dilute the per-line attribution.
    """
    return sum(1 for c in text if c.isalpha() or ('一' <= c <= '鿿'))


def _language_scan(
    text: str, expected: str
) -> tuple[float, int, int, list[str]]:
    """Measure adherence to the expected language, line by line.

    We classify each prose *line* with lingua's ``detect_language_of``
    (single best language) rather than running
    ``detect_multiple_languages_of`` over the whole document. The
    whole-document segmenter merges adjacent lines, so in a table-heavy
    audit (English metric rows interleaved with Chinese headers and
    notes) it attributes the Chinese runs to the dominant English text
    and reports ~0 % Chinese for a report that visibly has Chinese
    headers and paragraphs — a false positive that pushed agents into
    workarounds. Per-line keeps a Chinese header from being swallowed by
    the English table beneath it.

    Each line is weighted by its letter/CJK char count. Lines lingua
    can't classify (too short, pure identifiers/numbers) don't count
    toward either bucket. The mechanism is symmetric: for a zh task the
    "other" lines are the stray-English ones; for an en task they are
    the stray-Chinese ones. Offending lines are collected verbatim so
    the deny message can point the agent at the exact text to translate.

    Returns (ratio, expected_chars, other_chars, sample_offenders).
    ratio is 1.0 when nothing was attributable.
    """
    cleaned = _strip_code(text)
    if not cleaned.strip():
        return 1.0, 0, 0, []
    expected_lang = Language.CHINESE if expected == 'zh' else Language.ENGLISH
    in_lang = 0
    other = 0
    offenders: list[str] = []
    for line in cleaned.splitlines():
        raw = line.strip()
        # Skip markdown TABLE ROWS entirely. A table row is structured
        # DATA — campaign IDs, ASINs, verbatim keywords, numbers, metric
        # values — that is legitimately non-Chinese. Counting it would
        # penalize the required data-table report format and put this
        # gate in direct conflict with the completeness reviewer (which
        # demands per-campaign tables). The language signal lives in the
        # narrative prose: section headers, analysis paragraphs, bullets.
        if raw.startswith('|'):
            continue
        stripped = raw
        n = _alpha_len(stripped)
        # Lines with too few language-bearing chars (separators, lone
        # numbers, single identifiers) aren't reliably attributable.
        if n < 2:
            continue
        lang = _DETECTOR.detect_language_of(stripped)
        if lang is None:
            continue
        if lang == expected_lang:
            in_lang += n
        else:
            other += n
            offenders.append(' '.join(stripped.split())[:60])
    total = in_lang + other
    if total == 0:
        return 1.0, 0, 0, []
    # De-dup while preserving order; keep the longest few as examples.
    seen: set[str] = set()
    uniq = [f for f in offenders if not (f in seen or seen.add(f))]
    uniq.sort(key=len, reverse=True)
    return in_lang / total, in_lang, other, uniq[:5]


def check(
    result_text: str, title: str, description: str | None
) -> GateDeny | None:
    """Return a ``GateDeny`` when the result's prose ratio in the
    user's language is below the threshold; otherwise ``None``.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    expected = detect_expected_language(title, description)
    ratio, in_lang, other, offenders = _language_scan(result_text, expected)
    if ratio >= LANGUAGE_MIN_RATIO:
        return None
    # Surface the actual stray-language fragments lingua found so the
    # agent can translate exactly those (symmetric: English fragments
    # for a zh task, Chinese fragments for an en task). No hardcoded
    # vocabulary — the detector names the offenders.
    examples = (
        (
            '检测到的非中文片段示例：'
            if expected == 'zh'
            else 'Detected non-English fragments: '
        )
        + '; '.join(f'“{o}”' for o in offenders)
        if offenders
        else ''
    )
    if expected == 'zh':
        reason = (
            f'The user wrote the task in Chinese, but only '
            f'{ratio * 100:.0f}% of your result is detected as '
            f'Chinese ({in_lang} chars vs {other} chars in another '
            'language). Re-write the flagged prose in Chinese '
            '(decision words like 加投/维持投入/留·不处理/删除/暂缓删除/'
            '不管/新建计划 must be Chinese). Keep identifiers (campaign '
            'IDs, SKUs, ASINs, verbatim search terms) and metric '
            'abbreviations (ROAS, ACOS, CTR, CVR, ROI, GMV) as-is — '
            f'those are exempt. {examples} Then call set_task_result '
            'again.'
        )
    else:
        reason = (
            f'The user wrote the task in English, but only '
            f'{ratio * 100:.0f}% of your result is detected as '
            f'English ({in_lang} chars vs {other} chars in another '
            f'language). Re-write the flagged prose in English. '
            f'{examples} Then call set_task_result again.'
        )
    return GateDeny(gate=GATE_NAME, reason=reason)
