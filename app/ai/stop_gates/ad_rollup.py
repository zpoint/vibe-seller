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
# Spend was the only figure cross-checked, and that was too narrow. The
# LLM reviewer kept finding the same defect in the OTHER columns: one
# campaign's head row read sales 921 / 15 orders while its own 合计 row
# read 244 / 4, and a marketplace's summary row claimed 1,440 sales / 70
# orders over a table summing to 880 / 42 — spend matched in both cases,
# so nothing fired. Sales and orders are the same "one number written
# twice" invariant and cost the same to check.
_SALES_HEADER_CELLS = ('sales', '销售额', '总销售额', '销售')
_ORDERS_HEADER_CELLS = ('orders', '订单', '总订单', '订单数')
_EXTRA_METRICS = (
    ('sales', _SALES_HEADER_CELLS, '销售额'),
    ('orders', _ORDERS_HEADER_CELLS, '订单'),
)
# The document-level rollup: | amazon | SA | 12 | SAR 5,000.00 | …
_PLATFORMS = ad_scope.AD_PLATFORMS

# Displayed money is rounded to 2 decimals, so a sum of N displayed rows
# can sit up to N*0.005 from a total computed at full precision. Allow
# that, plus a floor for the 1-2 row case. Anything past it is a stale
# copy, not rounding — the live misses were a few units each.
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


def _header_index_prefix(
    cells: list[str], names: tuple[str, ...]
) -> int | None:
    """Header lookup that tolerates a currency suffix on the column name.

    The report's per-campaign tables label money columns with the market's
    currency — ``花费 (SAR)``, ``销售额 (AED)`` — so an exact match finds
    nothing and any check depending on it silently does nothing. That is
    exactly how the 合计-footer check below came out as a no-op on a block
    that plainly contradicted itself.

    PREFIX, not substring: ``花费`` must not match ``销售额``, and it must
    not match a column that merely mentions spend later in its name.
    """
    for i, c in enumerate(cells):
        lowered = c.strip().strip('*').lower()
        if any(lowered.startswith(n) for n in names):
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
# The targeting table's own 合计 footer — a THIRD statement of the same
# number, inside the same block. Live, a campaign carried a corrected
# figure in its table AND its reconciliation while the 合计 footer still
# held the old one: the row and
# the reconciliation agreed with each other, so the table-vs-drill check
# passed while the block openly contradicted itself two lines apart.
_TOTAL_ROW_RE = re.compile(
    r'(?m)^\|\s*\*{0,2}(?:合计|总计|汇总)\*{0,2}\s*\|(.+)$'
)


def _footer_with_header(block: str):
    """``(header_cells, 合计_cells)`` for the table that OWNS the footer.

    The header has to come from the SAME table as the 合计 row. Taking the
    block's first spend-bearing header instead pairs one table's column
    positions with another table's footer — and blocks really do carry
    more than one table: a live block opened with a small
    ``字段 | Spend | Revenue | Clicks | Orders | …`` summary above its
    targeting table, so a lookup for a column resolved against the wrong
    header and read that campaign's SPEND as its order count.

    The spend-only predecessor had the same flaw and escaped it by luck:
    the wrong column happened to hold an em dash, which parsed as None, so
    the check quietly did nothing instead of comparing wrong numbers.

    Walks the block once, remembering the header of the table currently
    being read, and returns it alongside the first 合计 row found inside
    that same table.
    """
    header = None
    for line in block.splitlines():
        cells = _cells(line)
        if not cells:
            header = None  # a blank line ends the table
            continue
        if set(''.join(cells)) <= set('-: '):
            continue  # |---|---| separator
        first = cells[0].strip().strip('*')
        if first in ('合计', '总计', '汇总'):
            if header is not None:
                return header, cells
            continue
        if header is None:
            header = cells  # first non-separator row of a table is its header
    return None, None


def _total_row_spend(block: str) -> float | None:
    """The campaign's spend as stated by its targeting table's 合计 row.

    Matched by COLUMN NAME against that table's own header — never by
    position, and never by "largest number on the row" (sales sits beside
    spend and normally exceeds it).
    """
    header, row = _footer_with_header(block)
    if header is None or row is None:
        return None
    i = _header_index_prefix(header, _SPEND_HEADER_CELLS)
    if i is None or i >= len(row):
        return None
    return _money(row[i])


def _head_row_metrics(section: str) -> dict[str, dict[str, float | None]]:
    """``{campaign_id: {sales, orders}}`` from the section's campaign table."""
    out: dict[str, dict[str, float | None]] = {}
    idx = None
    for line in section.splitlines():
        cells = _cells(line)
        if not cells:
            if idx is not None and out:
                break
            continue
        if set(''.join(cells)) <= set('-: '):
            continue
        if idx is None:
            id_i = _header_index(cells, _ID_HEADER_CELLS)
            if id_i is None:
                continue
            idx = {'id': id_i}
            for name, names, _l in _EXTRA_METRICS:
                j = _header_index(cells, names)
                if j is not None:
                    idx[name] = j
            continue
        if idx['id'] >= len(cells):
            continue
        cid = cells[idx['id']].strip().strip('*')
        if not cid or '总计' in cid:
            continue
        out[cid] = {
            n: (_money(cells[j]) if j < len(cells) else None)
            for n, j in idx.items()
            if n != 'id'
        }
    return out


def _footer_extra_metrics(block: str) -> dict[str, float | None]:
    """``{sales, orders}`` from the block's own 合计 row."""
    header, row = _footer_with_header(block)
    if header is None or row is None:
        return {}
    out: dict[str, float | None] = {}
    for name, names, _l in _EXTRA_METRICS:
        i = _header_index_prefix(header, names)
        out[name] = _money(row[i]) if i is not None and i < len(row) else None
    return out


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


# Two blocks, two ids, ONE campaign. Amazon gives a Sponsored Brands
# campaign both a numeric Campaign ID (what the bulk export uses) and an
# entity-style `A…` id (what the console shows), so an agent that captures
# the same campaign from both sources writes it twice. Live: two blocks
# named identically with identical totals — one id in the export, the
# other absent from it — double-counting that campaign in the combo table,
# the drilled count and the rollup.
#
# The existing duplicate check catches the same ID twice; this catches the
# same CAMPAIGN twice. Requires BOTH an identical name and an identical
# 合计 spend: sellers do reuse names across campaigns, but two genuinely
# different campaigns do not also spend the same amount to the cent.
def _duplicate_campaigns(section: str) -> list[str]:
    by_key: dict[tuple, list[str]] = {}
    for block in section.split('\n### ')[1:]:
        head = block.splitlines()[0]
        bits = [b.strip().strip('*') for b in head.split('|')]
        cid = bits[0]
        name = bits[1] if len(bits) > 1 else ''
        if not cid or not name or name == cid:
            continue
        total = _total_row_spend(block)
        if total is None:
            continue
        by_key.setdefault((name.lower(), round(total, 2)), []).append(cid)
    return [
        f'「{name}」花费 {spend:.2f} 同时挂在 {" 和 ".join(ids)} 两个 id 下'
        for (name, spend), ids in by_key.items()
        if len(ids) > 1
    ]


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

        # 1z) the same campaign written twice under two ids
        dupes = _duplicate_campaigns(section)
        if dupes:
            gaps.append(
                f'[重复活动] 「{label}」有 {len(dupes)} 个活动在报告里出现了'
                '两次，用了两个不同的 id：'
                + '；'.join(dupes[:3])
                + '。同名且花费分毫不差，说明是同一个活动被记录了两遍——'
                'Amazon 的 Sponsored Brands 活动同时有一个数字 Campaign ID'
                '（bulk 导出用的）和一个 `A…` 实体 id（控制台显示的），从两'
                '个来源各抓一次就会变成两个块。删掉其中一个（**保留 bulk '
                '导出里那个 id**，服务端按它核对花费），并把 AUDIT_SCOPE 的 '
                'active_ids、进度行的 D/A、组合表和汇总行一起改小——现在这'
                '个活动的花费在汇总里被算了两遍。'
            )

        # 1b) the head row vs the 合计 footer on SALES and ORDERS. Spend
        #     alone was checked, and every divergence the LLM reviewer
        #     reported sat in these two columns instead.
        head_rows = _head_row_metrics(section)
        off: list[str] = []
        for block in section.split('\n### ')[1:]:
            cid = block.splitlines()[0].split('|')[0].strip().strip('*')
            head = head_rows.get(cid)
            foot = _footer_extra_metrics(block)
            if not head or not foot:
                continue
            for name, _names, metric_label in _EXTRA_METRICS:
                h, f = head.get(name), foot.get(name)
                if h is None or f is None:
                    continue
                if abs(h - f) > max(_ROW_TOL, abs(h) * 0.001):
                    off.append(
                        f'「{cid}」{metric_label} 表内 {h:g} vs 合计行 {f:g}'
                    )
        if off:
            gaps.append(
                f'[汇总一致] 「{label}」有 {len(off)} 处销售额/订单对不上：'
                + '；'.join(off[:4])
                + '。花费对得上不代表整行就是对的——销售额和订单同样是同一个'
                '数字写了两遍。多半是改花费时只改了一半，或者 合计 行是修正前'
                '的旧快照。组合表那一行和 合计 行要一起对齐。'
            )

        # 1a) the block's 合计 footer vs its own reconciliation line —
        #     both inside one block, so a mismatch is the block
        #     contradicting itself.
        self_contradictory = []
        for block in section.split('\n### ')[1:]:
            cid = block.splitlines()[0].split('|')[0].strip().strip('*')
            if not cid:
                continue
            recon = _RECON_RE.search(block)
            total = _total_row_spend(block)
            if recon is None or total is None:
                continue
            rv = _money(recon.group(1))
            if rv is None or rv == 0:
                continue
            if abs(total - rv) > max(_ROW_TOL, rv * 0.001):
                self_contradictory.append(
                    f'「{cid}」合计行 {total:.2f} vs 对账行 {rv:.2f}'
                )
        if self_contradictory:
            gaps.append(
                f'[汇总一致] 「{label}」有 {len(self_contradictory)} 个活动'
                '块自己前后矛盾：定向表的 合计 行跟同一块里的 对账 行对不上：'
                + '；'.join(self_contradictory[:4])
                + '。这两个数说的是同一件事（该活动定向层 30 天总花费），'
                '改了一个必须改另一个。**这个活动在 bulk 导出里有 Campaign '
                '行的话，就以导出那个数为准**（服务端也按它核对，见 '
                '[花费核对]）；导出里没有该活动时，才用你重新核过的那个。'
                '定好之后，组合表那一行、合计 行、对账 行**三处同时**改成'
                '同一个数——每轮只改一处会让另两处继续报错，来回好几轮都收'
                '不了尾。'
            )

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


def combo_table_spend(section: str) -> dict[str, float | None]:
    """``{campaign_id: spend}`` from a combo section's campaign table.

    Exposed for the export cross-check in ``ad_completeness_review``: the
    combo table is where the report states each campaign's own spend, so
    it is what a platform figure should be compared against.
    """
    return dict(_combo_table(section))
