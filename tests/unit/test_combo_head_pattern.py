"""A marketplace must never be exempt from the audit gates by omission.

The country list was hardcoded in three separate regexes
(``ad_completeness_review``, ``bash_safety``, ``tasks_files``) and all
three had drifted the same way: no ``AU``, while a live store was
configured for ``{"amazon": ["AE", "AU", "SA"], …}``.

The consequence was not cosmetic. ``_COMBO_HEADER_RE`` is a PRE-FILTER
that ``continue``s, so ``## Amazon AU`` skipped every per-campaign check
in the reviewer — reconciliation, aggregate-row, targeting-table, name.
Four AU campaigns shipped with unparseable reconciliation lines
(``点击 —``), one of them at 1.05× over the physical ceiling, and no gap
was ever raised because the code that raises it never ran on them.

Marketplace codes are not enumerable in this codebase: they come from
each store's free-form ``platform_countries``, and Amazon alone sells in
20+ marketplaces. So the pattern matches the SHAPE of a code and is
defined ONCE.
"""

import re

import pytest

from app.ai import bash_safety
from app.ai.stop_gates import ad_completeness_review as acr, ad_rollup, ad_scope
from app.routers import tasks_files


@pytest.mark.unit
class TestComboHeadPattern:
    @pytest.mark.parametrize(
        'head',
        [
            'Amazon SA',
            'Amazon AE',
            'Amazon AU',
            'noon SA',
            'noon AE',
            'AMAZON MX',  # platform is case-insensitive
            'Amazon JP',
            'Amazon UK',
            'Amazon DE',
            'Amazon BR',
            'Amazon SG',
            'Amazon COM',
            'noon EG',
            'Amazon SA — 广告审核',  # trailing prose
        ],
    )
    def test_matches_any_marketplace(self, head):
        assert ad_scope.COMBO_HEAD_RE.search(head), head

    @pytest.mark.parametrize(
        'head',
        [
            '汇总建议',
            '关键观察',
            '最高影响建议（按潜在节省/提升排序）',
            'Amazon Ad Manager',  # 'Ad' is not an upper-case code
            'noon 广告后台',
            '汇总 vs 行级一致性',
        ],
    )
    def test_rejects_prose_headings(self, head):
        assert not ad_scope.COMBO_HEAD_RE.search(head), head

    def test_captures_platform_and_country(self):
        m = ad_scope.COMBO_HEAD_RE.search('## Amazon AU 市场')
        assert m.group(1).lower() == 'amazon'
        assert m.group(2) == 'AU'

    def test_every_consumer_shares_one_definition(self):
        """Three copies drifted apart once; they must not be able to again."""
        for pat in (
            acr._COMBO_HEADER_RE.pattern,
            ad_rollup._COMBO_HEAD_RE.pattern,
        ):
            assert ad_scope.COMBO_HEAD_PATTERN in pat, pat
        # The H2-anchored variants (bash_safety / tasks_files) build on the
        # same string.
        for pat in (
            bash_safety._SERVER_REVIEWED_RE.pattern,
            tasks_files._AUDIT_SECTION_RE.pattern,
        ):
            assert ad_scope.COMBO_HEAD_PATTERN in pat, pat

    def test_no_hardcoded_country_list_survives(self):
        """The literal allowlist must be gone, not merely supplemented."""
        stale = re.compile(r'sa\|ae\|mx\|us\|eg')
        for pat in (
            acr._COMBO_HEADER_RE.pattern,
            ad_rollup._COMBO_HEAD_RE.pattern,
        ):
            assert not stale.search(pat), pat


_AU_SECTION = """## Amazon AU

**进度**: drilled 1/1 active (1 total, 1 pages)

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| A00000001AAAAAAAAAAAA | A00000001AAAAAAAAAAAA | Auto | A$10.00 | A$30.00 | 2 | 33% | 3.0 |

### A00000001AAAAAAAAAAAA | A00000001AAAAAAAAAAAA | Auto

| 关键词/定向 | 匹配 | 出价 (A$) | 点击 | 花费 (A$) | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget | Broad | 1.00 | 10 | 10.00 | 3.0 | 维持 |
| **合计** | — | — | 10 | 10.00 | 3.0 | — |
搜索词对账: 定向花费 A$10.00 / 点击 10 = 搜索词花费 A$10.00 / 点击 10 (✓)

## 汇总建议

各 combo 花费/销售/ROAS 总览与按影响排序的行动清单。
"""


@pytest.mark.unit
class TestMarketplaceIsNotExempt:
    def test_au_section_reaches_the_per_campaign_checks(self, monkeypatch):
        """Regression: AU used to fail the pre-filter and be skipped whole."""
        seen = []
        real = acr._check_campaign_blocks

        def spy(part, head, tol, gaps, **kw):
            seen.append(head)
            return real(part, head, tol, gaps, **kw)

        monkeypatch.setattr(acr, '_check_campaign_blocks', spy)
        acr.check(_AU_SECTION, task_id=None, track=False)
        assert any('AU' in h for h in seen), seen

    def test_declared_combo_wins_over_the_shape_fallback(self, monkeypatch):
        """A DECLARED marketplace is recognised however it is capitalised.

        The store is the authority on its own countries, so the scope match
        (case-insensitive) decides first; the upper-case shape regex is only
        the fallback for a task with no scope. Otherwise `## amazon us`
        would be skipped for the very reason `## Amazon AU` was.
        """
        seen = []
        real = acr._check_campaign_blocks

        def spy(part, head, tol, gaps, **kw):
            seen.append(head)
            return real(part, head, tol, gaps, **kw)

        monkeypatch.setattr(acr, '_check_campaign_blocks', spy)
        lowered = _AU_SECTION.replace('## Amazon AU', '## amazon us')
        assert not ad_scope.COMBO_HEAD_RE.search('amazon us')  # shape says no
        acr.check(
            lowered,
            task_id=None,
            scope={
                'combos': [
                    {
                        'platform': 'amazon',
                        'country': 'US',
                        'active_ids': ['A00000001AAAAAAAAAAAA'],
                        'total_active': 1,
                        'total_active_source': 'chip:Live 1',
                    }
                ]
            },
            track=False,
        )
        assert any('us' in h.lower() for h in seen), seen


def _section(pairs):
    """A combo section whose campaigns are (id, name) pairs."""
    rows = '\n'.join(
        f'| {cid} | {name} | Auto | A$10.00 | A$30.00 | 2 | 33% | 3.0 |'
        for cid, name in pairs
    )
    blocks = ''.join(
        f"""
### {cid} | {name} | Auto

| 关键词/定向 | 匹配 | 出价 (A$) | 点击 | 花费 (A$) | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget | Broad | 1.00 | 10 | 10.00 | 3.0 | 维持 |
| **合计** | — | — | 10 | 10.00 | 3.0 | — |
搜索词对账: 定向花费 A$10.00 / 点击 10 = 搜索词花费 A$10.00 / 点击 10 (✓)
"""
        for cid, name in pairs
    )
    return (
        f'## Amazon AU\n\n**进度**: drilled {len(pairs)}/{len(pairs)} active '
        f'({len(pairs)} total, 1 pages)\n\n'
        '| id | name | type | spend | sales | orders | ACOS | ROAS |\n'
        '|---|---|---|---|---|---|---|---|\n'
        + rows
        + '\n'
        + blocks
        + '\n## 汇总建议\n\n各 combo 花费/销售/ROAS 总览与行动清单。\n'
    )


_IDS = [f'A0000000{i}AAAAAAAAAAAA' for i in range(1, 7)]


@pytest.mark.unit
class TestCampaignNameCaptured:
    """``name == id`` means the name column was never read.

    The ad console supplies a name for every campaign and output-spec
    requires it in both the combo table and each drill heading — so the id
    repeated in the name column is missing data, not a nameless campaign.
    Live: nearly all, every Amazon campaign in the run; the LLM reviewer
    flagged it twice as 「次要」 and it survived every round.
    """

    def _gaps(self, text):
        deny = acr.check(text, task_id=None, track=False)
        return [g for g in (deny.gaps if deny else []) if '[名称]' in g]

    def test_all_unnamed_is_a_gap(self):
        gaps = self._gaps(_section([(i, i) for i in _IDS[:4]]))
        assert len(gaps) == 1
        assert '4/4' in gaps[0]

    def test_named_campaigns_pass(self):
        text = _section([
            (i, f'widget campaign {n}') for n, i in enumerate(_IDS[:4])
        ])
        assert self._gaps(text) == []

    def test_minority_unnamed_is_tolerated(self):
        # A seller really can name one campaign after a SKU number; that
        # must not indict the combo.
        pairs = [(_IDS[0], _IDS[0])] + [
            (i, f'widget {n}') for n, i in enumerate(_IDS[1:5])
        ]
        assert self._gaps(_section(pairs)) == []

    def test_small_combo_is_not_indicted_by_fraction_alone(self):
        # 2/2 unnamed is 100% but only two campaigns — below NAME_MIN_UNNAMED.
        assert self._gaps(_section([(i, i) for i in _IDS[:2]])) == []

    def test_majority_unnamed_in_a_larger_combo(self):
        pairs = [(i, i) for i in _IDS[:4]] + [
            (i, f'widget {n}') for n, i in enumerate(_IDS[4:6])
        ]
        gaps = self._gaps(_section(pairs))
        assert len(gaps) == 1
        assert '4/6' in gaps[0]
