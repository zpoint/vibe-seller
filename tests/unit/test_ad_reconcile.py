"""The search-term reconciliation line reads in both languages.

Observed in CI: an English audit wrote the line in English to satisfy
the language gate, the completeness gate (Chinese-only) refused it as
unparseable, the agent switched back, the language gate refused that --
and the run timed out between two checks that could not both be met.
One shared grammar (``ad_reconcile``) now serves both readers.
"""

import pytest

from app.ai.stop_gates import (
    ad_completeness_campaign_blocks as blocks,
    ad_reconcile,
    ad_rollup,
)

pytestmark = pytest.mark.unit

ZH = '搜索词对账: 定向花费 USD 200.00 / 点击 200 = 搜索词花费 USD 200.00 / 点击 200 (✓)'
EN = (
    'Search-term reconciliation: targeting spend USD 200.00 / clicks 200 '
    '= search-term spend USD 200.00 / clicks 200 (✓)'
)
BILINGUAL = (
    '**搜索词对账 (search-term reconciliation)**: targeting spend USD 200.00 '
    '/ clicks 200 = search-term spend USD 195.50 / clicks 190 (✓)'
)


@pytest.mark.parametrize(
    ('line', 'groups'),
    [
        (ZH, ('200.00', '200', '200.00', '200')),
        (EN, ('200.00', '200', '200.00', '200')),
        (BILINGUAL, ('200.00', '200', '195.50', '190')),
        (
            'search term reconciliation: Targeting Spend 80.00 = '
            'Search Term Spend 80.00 (✓)',
            ('80.00', None, '80.00', None),
        ),
    ],
)
def test_both_languages_parse_to_the_same_numbers(line, groups):
    m = ad_reconcile.RECONCILE_RE.search(line)
    assert m and m.groups() == groups


def test_both_gates_share_one_grammar():
    assert blocks._RECONCILE_RE is ad_reconcile.RECONCILE_RE
    assert ad_rollup._RECON_RE is ad_reconcile.RECONCILE_RE


@pytest.mark.parametrize(
    'line',
    [
        'Search-term reconciliation: needs re-export',  # no figures
        'targeting spend USD 200.00 = search-term spend USD 200.00',  # no label
        'reconciliation: targeting spend 1 = search-term spend 1',
    ],
)
def test_lines_without_the_label_or_figures_do_not_parse(line):
    assert ad_reconcile.RECONCILE_RE.search(line) is None


def test_an_unparseable_english_line_is_reported_as_format_not_missing():
    """The label alone marks the line as written; the gap must then say
    'format', and name the English form as accepted."""
    assert ad_reconcile.LABEL_RE.search('Search-term reconciliation: TBD')
