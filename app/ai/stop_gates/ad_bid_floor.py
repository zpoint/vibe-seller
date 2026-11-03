"""Gate: never lower a keyword bid when its ACOS is below the floor.

Business rule (store owner, 2026-06-08): a keyword with a measurable
ACOS **below 30%** may only have its bid **raised or held — never
lowered**. ACOS < 30% means the keyword converts efficiently enough
that cutting its bid throws away profitable volume; a high
``current_bid / suggested`` ratio is NOT a reason to trim (the bid is a
ceiling, not spend — the second-price auction already charges far
less). Bid *lowering* is reserved for genuinely inefficient keywords
(ACOS ≥ 30%); zero-order search-term waste is handled by negation, not
a bid cut on a converting keyword.

noon Ad Manager tables report ROAS instead of ACOS; ROAS > 3.33 is the
exact equivalent of ACOS < 30%, so the same lock applies there.

This runs on the AD_AUDIT report markdown at ``set_task_result``. It
parses each keyword/target bid table (a table that has both a bid
column and an ACOS-or-ROAS column) and flags any body row whose
recommendation lowers the bid while ACOS < 30 % (ROAS > 3.33). It is a
no-op for non-ads results (no such table). See
``amazon-ads/references/tuning-thresholds.md § ACOS bid-direction
lock`` for the contract the agent is held to.

Scope note: this gate enforces ONLY the ACOS-direction lock above. The
*second* hard bid constraint — when a trim IS allowed (ACOS ≥ 30 %), the
new bid must not drop to/below the row's actual CPC×1.1 floor — is a
distinct rule enforced by ``ad_explicit_actions`` (its ``bad_cpc_floor``
check / ``_DENY_CPC_FLOOR`` denial). The two live in separate gates on
purpose (different trigger conditions, different parse), so don't read
this file's silence on the CPC floor as that rule being unenforced.
"""

from __future__ import annotations

import re

from app.ai import ad_action_vocab as vocab
from app.ai.ad_action_vocab import Col
from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

GATE_NAME = 'ad_bid_floor'

# Default ACOS threshold lives in the single source of truth
# (``ad_rules.DEFAULT_RULES['acos_no_lower']``); a per-store notes.md may
# override it. At/above it a bid trim is permitted; below it only
# raise/hold. ``check`` takes the resolved value via ``rules``.

# Allow several denials before failing open — this is a hard
# correctness rule, but a parser edge case must never permanently trap
# a task. Higher than the generic soft-gate cap (1) so enforcement is
# meaningful.
MAX_DENIALS = 6

# Code blocks aren't tables.
_FENCED_CODE_RE = re.compile(r'```.*?```', re.DOTALL)

# Verbs that mean "lower this keyword's bid". Deliberately excludes
# raise (提高/上调/raise), hold (保持/Hold), and negate (否定/negate —
# a search-term action, not a converting-keyword bid cut).
_LOWER_RE = vocab.LOWER_VERB_RE

# Negated mentions of lowering ("do not lower", 不可下调): a Hold whose
# NOTE explains why it must NOT be lowered is not a lower directive.
_NEGATED_LOWER_RE = vocab.NEGATED_LOWER_RE

_SEP_RE = re.compile(r':?-{2,}:?')
_PCT_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*%\s*$')
_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def _cells(line: str) -> list[str] | None:
    s = line.strip()
    if not s.startswith('|'):
        return None
    return [c.strip() for c in s.strip('|').split('|')]


def _is_separator(cells: list[str]) -> bool:
    nonempty = [c.replace(' ', '') for c in cells if c.strip()]
    return bool(nonempty) and all(_SEP_RE.fullmatch(c) for c in nonempty)


def _acos_from(cells: list[str], col: dict[str, int]) -> float | None:
    """Return the row's ACOS in percent, from an ACOS or ROAS column."""
    if 'acos' in col and col['acos'] < len(cells):
        m = _PCT_RE.match(cells[col['acos']])
        if m:
            return float(m.group(1))
    if 'roas' in col and col['roas'] < len(cells):
        m = _NUM_RE.search(cells[col['roas']].replace(',', ''))
        if m:
            roas = float(m.group())
            if roas > 0:
                return 100.0 / roas
    return None


def check(
    result_text: str, rules: dict[str, float] | None = None
) -> GateDeny | None:
    """Deny when the report lowers a bid on a row with ACOS below the
    no-lower threshold.

    ``rules`` carries the resolved thresholds (defaults + per-store
    notes.md override); falls back to ``DEFAULT_RULES``.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    threshold = (rules or DEFAULT_RULES)['acos_no_lower']
    text = _FENCED_CODE_RE.sub(' ', result_text)

    violations: list[tuple[str, float]] = []
    col: dict[str, int] = {}
    in_table = False

    for line in text.splitlines():
        cells = _cells(line)
        if cells is None:
            in_table = False
            col = {}
            continue
        if _is_separator(cells):
            continue
        low = [c.lower() for c in cells]
        has_rec = vocab.has_rec_col(cells)
        has_bid = vocab.find_col(cells, Col.BID) is not None
        has_metric = any(h in ('acos', 'roas') for h in low)
        if has_rec and has_bid and has_metric:
            # header row of a keyword/target bid table
            col = {}
            for i, h in enumerate(low):
                if h == 'acos':
                    col['acos'] = i
                elif h == 'roas':
                    col.setdefault('roas', i)
                elif vocab.is_col(h, Col.REC):
                    col['rec'] = i
                elif vocab.is_col(h, Col.KEYWORD):
                    col.setdefault('name', i)
            col.setdefault('name', 0)
            in_table = True
            continue
        if not in_table:
            continue
        rec_i = col.get('rec', len(cells) - 1)
        if rec_i >= len(cells):
            continue
        # Strip negated mentions ("不可下调", "do not lower") so a Hold
        # that merely *explains* it won't be lowered isn't mistaken for
        # a lower directive, then test for a real lowering verb.
        rec = _NEGATED_LOWER_RE.sub(' ', cells[rec_i])
        if not _LOWER_RE.search(rec):
            continue
        acos = _acos_from(cells, col)
        if acos is None:
            continue  # no measurable ACOS/ROAS — rule N/A (0-order rows)
        if acos < threshold:
            name_i = col.get('name', 0)
            name = cells[name_i] if name_i < len(cells) else cells[0]
            violations.append((name, acos))

    if not violations:
        return None

    t = f'{threshold:g}'
    sample = '；'.join(f'「{n}」ACOS {a:.1f}%' for n, a in violations[:6])
    more = '' if len(violations) <= 6 else f'（共 {len(violations)} 行）'
    reason = (
        f'硬性规则违规：{len(violations)} 个关键词 ACOS 低于 {t}% 却被建议'
        f'「下调出价」。ACOS < {t}% 的关键词正在盈利并贡献订单，只能 '
        '**维持/Hold 或提高出价/Raise bid**，绝不能下调——出价只是竞价上限，二价拍卖'
        '的实际花费远低于出价，下调会丢失已盈利的流量。请把这些行的建议'
        f'改为 维持/Hold 或 提高/Raise（按报告语言写其一；仅 ACOS ≥ {t}% 才允许下调），再重新 '
        f'set_task_result。违规行：{sample}{more}'
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
