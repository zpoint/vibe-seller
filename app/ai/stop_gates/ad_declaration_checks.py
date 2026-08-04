"""Does the report agree with what this phase declared it would do?

Three checks, all cheap, all previously impossible because nothing
recorded the task's intent.

**The declaration is a precondition.** A report presenting marketplace
coverage with no declaration behind it is refused. Without one, nothing
downstream knows how much the report owes or how much of it the user
should be shown — and the old answer was to guess from the prose, which
is the guess that turned a two-campaign task into a 51-campaign console.

**The declaration binds the output.** A phase that declared one market
and reported five has either under-declared or over-reached. Either way
someone must look, and only the user may widen a scope.

**A phase that said it would only read may not hand out decisions.**
`investigate` owes no marketplace coverage and opens no console, so a
report that declares it and then delivers a table of bid changes leaves
the user holding recommendations with no way to approve them. Unlike the
two above, this one does NOT depend on the report carrying
per-marketplace sections — decisions are decisions however the markdown
is arranged, and conditioning it on headings is what let a bid review
declared `investigate` sail through CI untouched.

Note what this module reuses: the ``## <platform> <CC>`` heading match
that used to DECIDE whether a report was an audit. It still runs — but
demoted from decider to auditor. It no longer creates an obligation; it
only catches a report disagreeing with the commitment made before the
work started.
"""

from __future__ import annotations

import re

from app.ai import ad_declaration
from app.ai.stop_gates.ad_completeness_rules import _COMBO_HEADER_RE
from app.models.ad_declaration import KIND_INVESTIGATE

# A table row telling someone to MOVE money: the shape that becomes an
# actionable decision row in the review console. Same vocabulary the
# cooldown gate grades on.
_MOVE_RE = re.compile(
    r'提高至|下调至|降至|下调到|提高出价|降低出价|加投|减投|暂停|否定'
)

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


def actionable_rows(parts: list[str]) -> int:
    """Table rows that tell someone to move a bid, pause, or negate.

    Counted on rows rather than prose: a sentence noting that ROAS looks
    low is an observation, while a table row carrying 下调至 1.80 is a
    decision someone is expected to act on.

    Scans EVERY part including the preamble. A decision is a decision
    wherever it sits, and a report with no ``## <platform> <CC>``
    sections at all is one single part — skipping ``parts[0]`` meant such
    a report could carry nothing but decisions and count zero.
    """
    n = 0
    for part in parts:
        for line in part.splitlines():
            stripped = line.strip()
            if stripped.startswith('|') and _MOVE_RE.search(stripped):
                n += 1
    return n


def declaration_gaps(parts: list[str], decl: dict | None) -> list[str]:
    """Gaps from comparing the report against its phase's declaration."""
    gaps: list[str] = []

    # A phase that said it was only READING must not come back with a
    # table of bid changes. Sharpening the investigate/audit line in the
    # skill (a question about figures is `investigate`) opens exactly one
    # hole if left unguarded: declare `investigate`, owe no marketplace
    # coverage, get no console — and hand the user recommendations they
    # have no way to act on. If the work produced decisions, it was an
    # audit and the user is owed the console.
    #
    # Checked BEFORE the marketplace-coverage question and independently
    # of it. It used to sit behind "does this report carry
    # ``## <platform> <CC>`` sections?", which has nothing to do with
    # whether decisions were handed out — a report against a console on
    # some other host, or one that simply wrote its findings without
    # per-marketplace headings, escaped the check entirely. Observed in
    # CI: `investigate` declared for a bid review, decisions delivered,
    # no gate raised, no console for the user.
    #
    # This is not the forbidden "is this an audit?" heuristic wearing a
    # new hat. That one INFERRED a declaration from prose and WIDENED
    # what the phase owed. This one requires a declaration to already
    # exist and only refuses a report that contradicts it — the auditor
    # role, not the decider role.
    if decl is not None and decl['kind'] == KIND_INVESTIGATE:
        n = actionable_rows(parts)
        if n:
            gaps.append(
                f'[基线] 本次声明是 investigate（只读数据、不提改动），但报告里'
                f'有 {n} 行给出了调价/暂停/否定这类可执行建议。二选一：把这些'
                '建议去掉、只回答问题；或者承认这本来就是一次 audit——那样'
                '用户才会拿到复核台去逐行确认。选后者就再调用一次 '
                '`vibe_seller_declare_ad_task`，kind 改成 "audit"，'
                'scope 保持不变或更窄（市场只能不变或变少、活动只能不变或'
                '变少）——这是本轮唯一允许的改 kind 动作，因为它只会让你'
                '承担更多、不会让范围变宽。'
            )

    reported = reported_combos(parts)
    if not reported:
        # No marketplace coverage claimed — an execution summary, a
        # question answered. Nothing to compare, and demanding a
        # declaration of it would deny work that never claimed to audit.
        return gaps
    if decl is None:
        # ``gaps`` is necessarily empty here (the check above needs a
        # declaration), but adding rather than replacing keeps that a
        # property of the code instead of a fact a reader must re-derive.
        return gaps + [MISSING_DECLARATION_GAP]

    outside = sorted({
        label
        for label, platform, country in reported
        if not ad_declaration.combo_in_scope(
            decl.get('scope'), platform, country
        )
    })
    if not outside:
        return gaps
    return gaps + [
        f'[基线] 本次声明的范围是「'
        f'{ad_declaration.scope_summary(decl.get("scope"))}」，'
        f'但报告里出现了范围外的市场：{"、".join(outside)}。'
        '请把范围外的内容从报告里去掉；如果用户确实需要这些市场，'
        '在结果里说明，由用户再发一条消息来扩大范围。'
    ]
