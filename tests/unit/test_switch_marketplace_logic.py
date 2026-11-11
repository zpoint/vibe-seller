"""Unit: the pure logic of amazon-shared/scripts/bh_switch_marketplace.py.

Target resolution, the per-domain base URL, and row choice from page
descriptors -- the three decisions that were wrong in the version that
failed live (verification read on the SOURCE host; finding matched the
label while clicking matched the whole button text).

The script is exec'd by browser-use with harness globals; importing it
without them defines the functions and runs nothing (see the guard at
its bottom).
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / 'app/skills_v2/amazon-shared/scripts/bh_switch_marketplace.py'
)


def _load():
    spec = importlib.util.spec_from_file_location('bh_switch', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load()

SA = 'A17E79C6D8DWNP'
AE = 'A2VIGQ35RCS4UG'


class TestResolveTarget:
    def test_country_code(self):
        assert m.resolve_target('sellercentral.amazon.ae', 'AE') == (
            'AE',
            AE,
        )

    def test_raw_marketplace_id(self):
        assert m.resolve_target('sellercentral.amazon.sa', SA) == (
            'SA',
            SA,
        )

    def test_uk_alias(self):
        cc, mid = m.resolve_target('x', 'UK')
        assert cc == 'GB' and mid == 'A1F83G8C2ARO7P'

    def test_empty_falls_back_to_host_domain(self):
        assert m.resolve_target('sellercentral.amazon.com.au', '') == (
            'AU',
            'A39IBJ37TRP1C6',
        )

    def test_unknown_everything(self):
        assert m.resolve_target('localhost:8000', '') == (None, None)


class TestTargetBase:
    """The per-domain pin: act and verify on the TARGET's own domain.

    Live 2026-11-11: `.sa` served AE while `.ae` served SA on one
    account -- reading the SOURCE host can never observe a switch,
    which is what made the old helper report ok:false on switches
    that had worked.
    """

    def test_bare_host_uses_target_domain_not_sc_host(self):
        assert (
            m.target_base('sellercentral.amazon.sa', 'AE')
            == 'https://sellercentral.amazon.ae'
        )

    def test_origin_sc_host_used_verbatim(self):
        assert (
            m.target_base('http://127.0.0.1:8123', 'AE')
            == 'http://127.0.0.1:8123'
        )

    def test_no_cc_keeps_host(self):
        assert (
            m.target_base('sellercentral.amazon.sg', None)
            == 'https://sellercentral.amazon.sg'
        )


def _row(i, label, current=False, expander=False):
    return {
        'i': i,
        'label': label,
        'current': current,
        'hasExpander': expander,
    }


class TestPickRow:
    NAME = 'United Arab Emirates'

    def test_open_row_picked_by_index(self):
        rows = [
            _row(0, 'acme (current)', current=True, expander=True),
            _row(1, 'United States (pending registration)'),
            _row(2, self.NAME),
        ]
        row, why = m.pick_row(rows, self.NAME)
        assert why == 'pick' and row['i'] == 2

    def test_pending_row_refused(self):
        """Selecting a (pending registration) row would START a
        registration -- refused in any language (the check is on the
        suffix being non-empty, never on its words)."""
        rows = [_row(0, self.NAME + ' (pending registration)')]
        row, why = m.pick_row(rows, self.NAME)
        assert why == 'pending'

    def test_pending_fullwidth_zh(self):
        rows = [_row(0, '阿拉伯联合酋长国（待注册）')]
        row, why = m.pick_row(rows, '阿拉伯联合酋长国')
        assert why == 'pending'

    def test_current_row_is_current_only(self):
        rows = [_row(0, self.NAME + ' (current)', current=True)]
        row, why = m.pick_row(rows, self.NAME)
        assert why == 'current-only'

    def test_account_row_never_a_candidate(self):
        """The account row shares the row selector; it is excluded by
        its expander icon even when its text starts with the name."""
        rows = [_row(0, 'United Arab Emirates Trading LLC', expander=True)]
        row, why = m.pick_row(rows, self.NAME)
        assert why == 'none'

    def test_first_open_wins_when_several(self):
        rows = [
            _row(0, self.NAME),
            _row(1, self.NAME),
        ]
        row, why = m.pick_row(rows, self.NAME)
        assert why == 'pick' and row['i'] == 0

    def test_missing_country(self):
        rows = [_row(0, 'Saudi Arabia')]
        row, why = m.pick_row(rows, self.NAME)
        assert row is None and why == 'none'
