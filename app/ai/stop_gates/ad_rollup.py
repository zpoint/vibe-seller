"""Internal arithmetic consistency: drill block → combo table → 汇总.

The report states each campaign's spend THREE times — in the combo
table at the top of its section, in the campaign's own drill block, and
folded into the per-combo row of the 汇总 table. Those are not three
observations; they are one number written three times, so they must
agree by arithmetic alone. No live data, no platform quirk, nothing to
re-capture: a mismatch is always a stale copy the agent forgot to
propagate, and always fixable by editing a cell.

Which is exactly the correction the agent keeps missing. Live, on one
run: it re-captured a noon SA campaign via the full Export, updated the
drill block, and left the combo table on the old truncated figure — the
LLM reviewer caught it at iter 3. It then did the identical thing to a
noon AE campaign one round later (85.00 in the table, 90.00 in the
drill, rollup short by 5.00), and the reviewer caught THAT at iter 4,
calling it 「与 iter-3 镜像的同构缺陷」 — isomorphic to the mirror of
iter 3.

Two expensive LLM review rounds spent re-deriving a subtraction. The
invariant is decidable from the document alone, so it belongs in code:
checked every round, named precisely, and impossible to ship twice.
"""

from __future__ import annotations

import re

from app.ai.stop_gates import ad_scope

# A combo section header: "## Amazon SA", "## noon AE 市场".
_COMBO_HEAD_RE = re.compile(r'(?m)^##\s*' + ad_scope.COMBO_HEAD_PATTERN)
# The per-campaign table that opens a combo section. Located by its
# HEADER CELLS rather than a column index, so a section that adds or
# reorders a column is still read correctly instead of silently parsing
# the wrong cell as money.
_ID_HEADER_CELLS = ('id', '活动 id', '活动id', 'campaign id')
_SPEND_HEADER_CELLS = ('spend', '花费', '总花费')
# The document-level rollup: | amazon | SA | 12 | SAR 5,000.00 | …
_PLATFORMS = ad_scope.AD_PLATFORMS

# Displayed money is rounded to 2 decimals, so a sum of N displayed rows
# can sit up to N*0.005 from a total computed at full precision. Allow
# that, plus a floor for the 1-2 row case. Anything past it is a stale
# copy, not rounding — the live misses were 5.00 and 5.00.
_ROW_TOL = 0.02
_SUM_TOL_FLOOR = 0.05
_SUM_TOL_PER_ROW = 0.006


def _cells(line: str) -> list[str]:
    """Markdown table row → trimmed cells, without the outer pipes."""
    if '|' not in line:
        return []
    parts = line.strip().strip('|').split('|')
    return [p.strip() for p in parts]


def _money(cell: str) -> float | None:
    """First number in a money cell, currency-agnostic.

    Handles ``SAR 5,000.00``, ``A$100.00``, bare ``90.00``. Returns None
    for a placeholder (``—``, ``-``, ``n/a``, ``∞``, empty), which means
    "no figure stated" and must never read as 0.0 — a zero would make a
    placeholder row silently drag a rollup comparison off.
    """
    if not cell:
        return None
    m = re.search(r'(\d[\d,]*(?:\.\d+)?)', cell.replace('，', ','))
    if m is None:
        return None
    try:
        return float(m.group(1).replace(',', ''))
    except ValueError:
        return None


def _header_index(cells: list[str], names: tuple[str, ...]) -> int | None:
    for i, c in enumerate(cells):
        lowered = c.strip().strip('*').lower()
        if lowered in names:
            return i
    return None


def _combo_table(section: str) -> list[tuple[str, float | None]]:
    """``[(campaign_id, spend), …]`` from a section's campaign table."""
    rows: list[tuple[str, float | None]] = []
    id_i = spend_i = None
    for line in section.splitlines():
        cells = _cells(line)
        if not cells:
            if id_i is not None and rows:
                break  # table ended
            continue
        if id_i is None:
            i, s = (
                _header_index(cells, _ID_HEADER_CELLS),
                _header_index(cells, _SPEND_HEADER_CELLS),
            )
            if i is not None and s is not None:
                id_i, spend_i = i, s
            continue
        if set(''.join(cells)) <= set('-: '):
            continue  # |---|---| separator
        if max(id_i, spend_i) >= len(cells):
            continue
        cid = cells[id_i].strip().strip('*')
        # The grand-total row is not a campaign.
        if not cid or cid.startswith('总计') or '总计' in cid:
            continue
        rows.append((cid, _money(cells[spend_i])))
    return rows


# The drill block's own authoritative figure: the targeting side of the
# reconciliation line, which IS the campaign's spend for the window.
_RECON_RE = re.compile(r'搜索词对账[^\n]*?定向花费[^\d\n]*([\d,]+(?:\.\d+)?)')


def _drill_spends(section: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for block in section.split('\n### ')[1:]:
        cid = block.splitlines()[0].split('|')[0].strip().strip('*')
        m = _RECON_RE.search(block)
        if not cid or m is None:
            continue
        v = _money(m.group(1))
        if v is not None:
            out.setdefault(cid, v)
    return out


def _rollup_totals(text: str) -> dict[tuple[str, str], float]:
    """``{(platform, country): 总花费}`` from the 汇总 table."""
    out: dict[tuple[str, str], float] = {}
    for line in text.splitlines():
        cells = _cells(line)
        if len(cells) < 4:
            continue
        plat = cells[0].strip().strip('*').lower()
        if plat not in _PLATFORMS:
            continue
        country = cells[1].strip().strip('*').upper()
        if not re.fullmatch(r'[A-Z]{2,3}', country):
            continue
        # …| 活跃活动数 | 总花费 | — count then money. Take the first
        # cell after the count that carries a figure.
        for cell in cells[3:]:
            v = _money(cell)
            if v is not None:
                out[(plat, country)] = v
                break
    return out


def check_rollups(text: str) -> list[str]:
    """Gap strings for every stale copy of a number, else ``[]``."""
    if not text or not isinstance(text, str):
        return []
    gaps: list[str] = []
    heads = list(_COMBO_HEAD_RE.finditer(text))
    rollups = _rollup_totals(text)
    for n, m in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(text)
        section = text[m.start() : end]
        plat, country = m.group(1).lower(), m.group(2).upper()
        label = f'{m.group(1)} {country}'
        rows = _combo_table(section)
        if not rows:
            continue
        drills = _drill_spends(section)

        # 1) combo table row vs the campaign's own drill block
        stale = [
            f'「{cid}」表内 {row_spend:.2f} vs drill {drills[cid]:.2f}'
            for cid, row_spend in rows
            if row_spend is not None
            and cid in drills
            and abs(row_spend - drills[cid]) > _ROW_TOL
        ]
        if stale:
            gaps.append(
                f'[汇总一致] 「{label}」组合表里有 {len(stale)} 个活动的花费与'
                '它自己的 drill block 对不上：'
                + '；'.join(stale[:4])
                + '。这不是要重新抓数——同一个数字在报告里写了两遍，drill '
                '改了、表没跟着改。以 drill block 的数字为准，改表内单元格'
                '（顺带重算该行 ACOS / ROAS），并把下面 汇总 表那一行的总花费'
                '一起更新。'
            )

        # 2) sum of the combo table vs the 汇总 row for this combo
        stated = rollups.get((plat, country))
        summable = [s for _cid, s in rows if s is not None]
        if stated is None or not summable:
            continue
        total = sum(summable)
        tol = max(_SUM_TOL_FLOOR, _SUM_TOL_PER_ROW * len(summable))
        if abs(total - stated) > tol:
            gaps.append(
                f'[汇总一致] 「{label}」汇总表写的总花费 {stated:.2f} 与组合表'
                f'{len(summable)} 行相加得到的 {total:.2f} 不一致（差 '
                f'{abs(total - stated):.2f}）。同一批数字的两次呈现必须相等：'
                '改完某个活动的花费后，汇总行要一起改。'
            )
    return gaps
