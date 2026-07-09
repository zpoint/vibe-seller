"""Gate: a zero-impression campaign/target must not be told to 'maintain'.

Store-owner rule (2026-07-09): a campaign or target that has run the
full analysis window with ZERO impressions — hence zero clicks, zero
spend — is NOT "fine, hold". It simply is not being served. A bare
维持 / 保持 / Hold on such a row wastes another window doing nothing.
The only useful recommendations are:

  * RAISE the bid (提高/上调出价) — the bid is likely below the auction
    entry price, so it never wins an impression; or
  * CHECK AD ELIGIBILITY (检查/排查广告资格 — product suppressed / not
    eligible / campaign not approved), stated AS THE ACTION.

Either way the recommended action must not be "维持". Mentioning
eligibility in a note while the action stays 维持 does not satisfy the
rule — the action itself has to change (that was the exact defect: a
30-day zero-impression campaign carried "维持——…需人工排查").

Runs on the AD_AUDIT markdown at ``set_task_result``, folded into
``ad_completeness_review`` alongside the other bid-rule checks. No-op
for non-ads results (no bid table with a 建议 column).
"""

from __future__ import annotations

import re

from app.ai.stop_gates import GateDeny

GATE_NAME = 'ad_zero_impression'

# Hard correctness rule, but a parser edge case must never permanently
# trap a task — allow several denials before failing open.
MAX_DENIALS = 6

_SEP_RE = re.compile(r':?-{2,}:?')
_NUM_RE = re.compile(r'-?\d[\d,]*(?:\.\d+)?')
# Explicit zero-serving language anywhere in the row.
_ZERO_TEXT_RE = re.compile(
    r'零曝光|零展示|0\s*曝光|0\s*展示|zero\s*impressions?', re.IGNORECASE
)
_MAINTAIN_RE = re.compile(r'维持|保持|继续观察|观望|\bhold\b', re.IGNORECASE)
# Rows this rule does NOT apply to:
#   * a COLLAPSED zero-impression filler row ("其余 N 个… (0 展示)") — the
#     collapse rule explicitly permits aggregating genuinely-zero long-tail
#     keywords into one line; actioning each is neither possible nor useful.
#   * a genuinely JUST-CREATED campaign/group (新建 / 今日 / 刚启动) — per
#     output-spec, a reasonable-bid brand-new row legitimately warrants
#     "维持观察" until it has a window of data.
_SKIP_RE = re.compile(
    r'其余\s*\d+\s*个|另\s*\d+\s*个|新建|刚(?:创建|启动)|今日|当日新建',
    re.IGNORECASE,
)
# An acceptable action for a zero-impression row: raise the bid, or make
# eligibility the ACTION (not a footnote). Presence of either means the
# row is no longer a bare "maintain" and passes.
_FIX_RE = re.compile(
    r'提高|上调|加价|raise|increase|检查.{0,6}资格|排查.{0,6}资格'
    r'|广告资格|是否合格|未过审|审核状态|eligib',
    re.IGNORECASE,
)


def _cells(line: str) -> list[str] | None:
    s = line.strip()
    if not s.startswith('|'):
        return None
    return [c.strip() for c in s.strip('|').split('|')]


def _is_separator(cells: list[str]) -> bool:
    ne = [c.replace(' ', '') for c in cells if c.strip()]
    return bool(ne) and all(_SEP_RE.fullmatch(c) for c in ne)


def _zero(cell: str) -> bool:
    """True if a metric cell reads as zero / no-data ('0', '0.00', '—')."""
    s = cell.strip()
    if s in ('', '—', '-', '–', 'N/A', 'n/a'):
        return True
    m = _NUM_RE.search(s.replace(',', ''))
    return m is not None and float(m.group()) == 0.0


def check(
    result_text: str,
    rules: dict[str, float] | None = None,
) -> GateDeny | None:
    """Deny when a zero-impression row's recommendation is a bare maintain.

    ``rules`` is accepted for a uniform gate signature but unused (this
    is a categorical rule, not a threshold).
    """
    if not result_text or not isinstance(result_text, str):
        return None

    bad: list[str] = []
    col: dict[str, int] = {}
    in_table = False

    for line in result_text.splitlines():
        cells = _cells(line)
        if cells is None:
            in_table = False
            col = {}
            continue
        if _is_separator(cells):
            continue
        low = [c.lower() for c in cells]
        has_rec = any(h in ('建议', 'recommendation') for h in low)
        if has_rec and not in_table:
            col = {}
            for i, h in enumerate(low):
                if h in ('建议', 'recommendation'):
                    col['rec'] = i
                elif ('点击' in h) or (h == 'clicks'):
                    col.setdefault('clicks', i)
                elif ('曝光' in h) or ('展示' in h) or ('impress' in h):
                    col.setdefault('impr', i)
                elif ('花费' in h) or (h in ('spend', 'cost')):
                    col.setdefault('spend', i)
                elif (
                    ('关键词' in h)
                    or ('定向' in h)
                    or (h in ('keyword', 'target', 'targeting'))
                ):
                    col.setdefault('name', i)
            col.setdefault('name', 0)
            in_table = True
            continue
        if not in_table:
            continue
        rec_i = col.get('rec')
        if rec_i is None or rec_i >= len(cells):
            continue
        rec = cells[rec_i]
        # Only a bare maintain is a defect: a row already recommending a
        # raise or an eligibility check has an actionable next step.
        if not _MAINTAIN_RE.search(rec) or _FIX_RE.search(rec):
            continue
        # Collapsed filler and just-created rows are exempt (see _SKIP_RE).
        if _SKIP_RE.search(line):
            continue
        # Zero-serving? Explicit text in the row, else numeric: the
        # impressions column is 0 if present, otherwise BOTH clicks and
        # spend are 0 (a serving proxy when there's no impr column).
        zero_traffic = bool(_ZERO_TEXT_RE.search(line))
        if not zero_traffic:
            if 'impr' in col and col['impr'] < len(cells):
                zero_traffic = _zero(cells[col['impr']])
            elif 'clicks' in col and 'spend' in col:
                ci, si = col['clicks'], col['spend']
                zero_traffic = (
                    ci < len(cells)
                    and si < len(cells)
                    and _zero(cells[ci])
                    and _zero(cells[si])
                )
        if zero_traffic:
            name_i = col.get('name', 0)
            name = cells[name_i] if name_i < len(cells) else cells[0]
            bad.append(name[:40])

    if not bad:
        return None

    sample = '、'.join(f'「{n}」' for n in bad[:6])
    more = '' if len(bad) <= 6 else f'（共 {len(bad)} 行）'
    reason = (
        f'零曝光却建议「维持」：{len(bad)} 行在整个分析窗口内 0 曝光 / 0 点击 '
        f'/ 0 花费，建议却是维持/保持——{sample}{more}。零曝光不是表现好，'
        '而是根本没有被展示。这类行必须给出可执行动作：**提高出价**（出价'
        '很可能低于竞价入场价，永远竞不到展示）或**检查广告资格**（产品被'
        '抑制 / 不合格 / 活动未过审）。把动作从「维持」改为「提高出价至 X」'
        '或「检查广告资格」，再重新 set_task_result。'
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
