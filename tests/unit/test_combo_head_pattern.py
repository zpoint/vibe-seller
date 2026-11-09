"""A ``## <Platform> <CC>`` heading is recognised by shape, never a list.

Marketplace codes come from each store's free-form ``platform_countries``
and Amazon alone sells in 20+ marketplaces; three hardcoded copies of the
list once drifted the same way (no ``AU``). The pattern is defined once
in ``ad_scope`` and used to recognise an audit report file
(``tasks_files.resolve_audit_deliverable``).
"""

import re

import pytest

from app.ai.stop_gates import ad_scope
from app.routers import tasks_files

COMBO_HEAD_RE = re.compile(ad_scope.COMBO_HEAD_PATTERN)


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
        assert COMBO_HEAD_RE.search(head), head

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
        assert not COMBO_HEAD_RE.search(head), head

    def test_captures_platform_and_country(self):
        m = COMBO_HEAD_RE.search('## Amazon AU 市场')
        assert m.group(1).lower() == 'amazon'
        assert m.group(2) == 'AU'

    def test_the_report_detector_builds_on_the_shared_pattern(self):
        """Copies of this regex drifted apart once (one lost ``AU``)."""
        assert (
            ad_scope.COMBO_HEAD_PATTERN in tasks_files._AUDIT_SECTION_RE.pattern
        )
        assert not re.search(
            r'sa\|ae\|mx\|us\|eg', tasks_files._AUDIT_SECTION_RE.pattern
        )
