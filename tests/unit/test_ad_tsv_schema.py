"""The per-campaign TSVs must have a header a stranger can read.

They are the machine-readable half of an audit — the console renders from
them, an execution task acts on them, the next audit diffs against them —
and the spec named their paths and purpose but never their columns. So
every run invented a header: 22 different targeting headers and 12
search-term headers across one store, including two delimited with PIPES,
and currency folded into column names so ``spend`` sits in a different
column per marketplace. The agent's own summary script mis-summed the
layer because of it.

This pins the header only. Values are already covered by the
reconciliation, rollup and export cross-checks; what was missing was any
guarantee a consumer can FIND a column.
"""

import pytest

from app.ai.stop_gates.ad_tsv_schema import (
    SEARCHTERM_COLUMNS,
    TARGETING_COLUMNS,
    check_tsv_header,
)


def _write(tmp_path, name, header, delim='\t'):
    p = tmp_path / name
    p.write_text(delim.join(header) + '\n', encoding='utf-8')
    return p


@pytest.mark.unit
class TestTsvHeader:
    def test_correct_targeting_header_passes(self, tmp_path):
        p = _write(tmp_path, 'c.tsv', TARGETING_COLUMNS)
        assert check_tsv_header(p, 'targeting') is None

    def test_correct_searchterm_header_passes(self, tmp_path):
        p = _write(tmp_path, 'c.searchterms.tsv', SEARCHTERM_COLUMNS)
        assert check_tsv_header(p, 'searchterm') is None

    def test_case_and_bom_are_tolerated(self, tmp_path):
        """A BOM or capitalised header is still readable; don't nitpick."""
        cols = [c.upper() for c in TARGETING_COLUMNS]
        cols[0] = '﻿' + cols[0]
        p = _write(tmp_path, 'c.tsv', cols)
        assert check_tsv_header(p, 'targeting') is None

    def test_pipe_delimited_is_reported_as_such(self, tmp_path):
        """Read as a TSV a pipe file is ONE column and every figure is lost.

        Two live files were written this way, which is why the delimiter is
        sniffed rather than assumed.
        """
        p = _write(tmp_path, 'c.tsv', TARGETING_COLUMNS, delim='|')
        reason = check_tsv_header(p, 'targeting')
        assert reason is not None
        assert '竖线' in reason

    def test_currency_baked_into_the_name_is_named(self, tmp_path):
        """The most common live variant, and the least obvious."""
        cols = [
            'target',
            'match',
            'entity',
            'bid',
            'spend_SAR',
            'clicks',
            'cpc',
            'orders',
            'sales_SAR',
            'ACOS',
            'ROAS',
            'impressions',
        ]
        p = _write(tmp_path, 'c.tsv', cols)
        reason = check_tsv_header(p, 'targeting')
        assert reason is not None
        assert 'spend_SAR' in reason
        assert 'currency' in reason

    def test_missing_columns_are_listed(self, tmp_path):
        p = _write(tmp_path, 'c.tsv', ['target', 'bid', 'spend', 'currency'])
        reason = check_tsv_header(p, 'targeting')
        assert reason is not None
        assert 'ad_group' in reason

    def test_reordered_columns_are_reported(self, tmp_path):
        cols = list(TARGETING_COLUMNS)
        cols[0], cols[1] = cols[1], cols[0]
        p = _write(tmp_path, 'c.tsv', cols)
        reason = check_tsv_header(p, 'targeting')
        assert reason is not None
        assert '列序' in reason

    def test_absent_file_is_not_a_failure(self, tmp_path):
        """Its absence is already a gap from the drill-block cross-check.

        Reporting it here too would double-count the same missing file.
        """
        assert check_tsv_header(tmp_path / 'nope.tsv', 'targeting') is None

    def test_empty_file_is_not_a_failure(self, tmp_path):
        p = tmp_path / 'c.tsv'
        p.write_text('', encoding='utf-8')
        assert check_tsv_header(p, 'targeting') is None

    def test_the_two_layers_have_different_contracts(self):
        """A search-term row hangs off a target; a target row has a state."""
        assert 'search_term' in SEARCHTERM_COLUMNS
        assert 'source_target' in SEARCHTERM_COLUMNS
        assert 'state' in TARGETING_COLUMNS
        assert 'currency' in TARGETING_COLUMNS
        assert 'currency' in SEARCHTERM_COLUMNS
        # ad_group is the grouping level on both (SKU on noon).
        assert TARGETING_COLUMNS[0] == SEARCHTERM_COLUMNS[0] == 'ad_group'
