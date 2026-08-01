"""Does the report agree with what this phase declared it would do?

Two checks, both cheap, both previously impossible because nothing
recorded the task's intent.

**The declaration is a precondition.** A report presenting marketplace
coverage with no declaration behind it is refused. Without one, nothing
downstream knows how much the report owes or how much of it the user
should be shown — and the old answer was to guess from the prose, which
is the guess that turned a two-campaign task into a 51-campaign console.

**The declaration binds the output.** A phase that declared one market
and reported five has either under-declared or over-reached. Either way
someone must look, and only the user may widen a scope.

Note what this module reuses: the ``## <platform> <CC>`` heading match
that used to DECIDE whether a report was an audit. It still runs — but
demoted from decider to auditor. It no longer creates an obligation; it
only catches a report disagreeing with the commitment made before the
work started.
"""

from __future__ import annotations

from app.ai import ad_declaration
from app.ai.stop_gates.ad_completeness_rules import _COMBO_HEADER_RE

MISSING_DECLARATION_GAP = (
    '[基线] 这份广告报告没有声明任务范围。开工前必须先调用 '
    '`vibe_seller_declare_ad_task` 说明这次是 audit / create / execute / '
    'investigate，以及涉及哪些市场和活动——声明决定了本次要覆盖多少、'
    '以及用户会看到怎样的复核台。声明不能事后补写，所以现在请在结果里'
    '说明这次实际做了什么，下一轮开工前先声明。'
)


def reported_combos(parts: list[str]) -> list[tuple[str, str, str]]:
    """``(label, platform, country)`` for each combo section in the report.

    ``parts`` is ``result_text`` already split on level-2 headers, with
    ``parts[0]`` being the preamble — the caller has done that work.
    """
    out: list[tuple[str, str, str]] = []
    for part in parts[1:]:
        if not part.strip():
            continue
        m = _COMBO_HEADER_RE.search(part.splitlines()[0])
        if m:
            platform, country = m.group(1).lower(), m.group(2).upper()
            out.append((f'{platform} {country}', platform, country))
    return out


def declaration_gaps(parts: list[str], decl: dict | None) -> list[str]:
    """Gaps from comparing the report against its phase's declaration."""
    reported = reported_combos(parts)
    if not reported:
        # No marketplace coverage claimed — an execution summary, a
        # question answered. Nothing to compare, and demanding a
        # declaration of it would deny work that never claimed to audit.
        return []
    if decl is None:
        return [MISSING_DECLARATION_GAP]

    outside = sorted({
        label
        for label, platform, country in reported
        if not ad_declaration.combo_in_scope(
            decl.get('scope'), platform, country
        )
    })
    if not outside:
        return []
    return [
        f'[基线] 本次声明的范围是「'
        f'{ad_declaration.scope_summary(decl.get("scope"))}」，'
        f'但报告里出现了范围外的市场：{"、".join(outside)}。'
        '请把范围外的内容从报告里去掉；如果用户确实需要这些市场，'
        '在结果里说明，由用户再发一条消息来扩大范围。'
    ]
