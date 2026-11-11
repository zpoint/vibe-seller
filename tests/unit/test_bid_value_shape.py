"""The pre-live bid-value guard (the 10x overspend bug).

The Shadow-DOM bid input does not reliably clear, so a 1.30 target can
become 11.3 or 3.002.27. ``check_bid_value_shape`` denies what the
command itself proves wrong — a concatenation (two decimal points in
one typed value) — and, in an explicit bid context, a value the task's
FROZEN APPROVAL does not hold. There is no fixed ceiling: JPY/INR bids
are legitimately 50-300, and without a frozen approval there is nothing
to check magnitude against; the ``ad_changes`` exit gate reconciles the
execution log with the approval when the result ships.
"""

import uuid

import pytest

from app.ai.bash_safety import check_bid_value_shape, first_bash_deny
from app.ai.stop_gates import ad_changes
from app.ai.stop_gates.ad_tables import CHANGE_COLUMNS
from tests.unit.ad_fixtures import change, write_tsv

pytestmark = pytest.mark.unit

TASK_ID = str(uuid.uuid4())

BID_FORMS = (
    'browser-use input 47 "{v}"  # the bid cell',
    'fill_input("input.bid-input", "{v}")',
    'type_text("{v}")  # into the bid cell',
    "js(\"q('.bid-input').value = '{v}'\")",
)


@pytest.fixture
def task_dir(tmp_path, monkeypatch):
    """A session task dir plus a private APPROVED_DIR for the freeze."""
    monkeypatch.setattr(ad_changes, 'APPROVED_DIR', tmp_path / 'approved')
    d = tmp_path / 'tasks' / TASK_ID
    d.mkdir(parents=True)
    return d


def freeze(bids, name=''):
    """Write one frozen approval holding the given target bids."""
    rows = [change(target_bid=b) for b in bids]
    write_tsv(
        ad_changes.APPROVED_DIR / f'{TASK_ID}{name}.tsv',
        CHANGE_COLUMNS,
        rows,
    )


@pytest.mark.parametrize(
    ('value', 'denied'),
    [
        ('1.30', False),
        ('4.00', False),
        ('24.38', False),  # a legit high product-targeting bid
        ('11.3', False),  # plausible single-decimal; ad_changes reconciles
        ('3.002.27', True),  # concatenation (two decimal points)
        ('1.301.30', True),  # concatenation
    ],
)
def test_shape_check_is_context_free(value, denied):
    # Caught however the bid is typed, with or without an approval.
    for form in BID_FORMS:
        cmd = form.format(v=value)
        result = check_bid_value_shape(cmd, task_dir=None)
        assert (result is not None) is denied, cmd
        if denied and result is not None:
            assert 'decimal point' in result


def test_ignores_non_bid_commands():
    assert check_bid_value_shape('new_tab("https://x/y")') is None
    assert check_bid_value_shape('browser-use open https://x/y') is None
    assert check_bid_value_shape('ls -la') is None


def test_ignores_generic_numeric_fields():
    """The 0.13 helpers set ANY field and the guard runs on every Bash
    command: without a bid context, dates, OTP codes and quantities
    pass."""
    assert check_bid_value_shape('type_text("2026-03-31")') is None
    assert check_bid_value_shape('fill_input("#otp", "123456")') is None
    assert check_bid_value_shape('js("el.value = \'999\'")') is None
    assert check_bid_value_shape('type_text("100234")  # quantity') is None


def test_allows_integer_otp_and_quantities_in_legacy_form(task_dir):
    """The legacy ``browser-use input <idx>`` form without "bid" is
    OTP/postal/quantity-ambiguous — even a decimal-sized value is not
    proved to be a bid by its shape, and an approval check without a
    bid context would deny legitimate typing."""
    freeze(['3.00'])
    assert (
        check_bid_value_shape('browser-use input 47 "446770"', task_dir) is None
    )
    assert (
        check_bid_value_shape('browser-use input 12 "875765"', task_dir) is None
    )
    assert (
        check_bid_value_shape('browser-use input 47 "113.0"', task_dir) is None
    )
    # ...but a concatenation is a concatenation in any form:
    assert check_bid_value_shape('browser-use input 47 "3.002.27"', task_dir)


class TestNoCeilingWithoutAFrozenApproval:
    """Magnitude is not the server's guess any more. The approved bids
    say what this execution may type; with no approval frozen, the
    shape check is all the command text can honestly prove — and
    ``ad_changes`` reconciles what actually ran."""

    def test_high_jpy_style_bid_passes_without_approval(self):
        for form in BID_FORMS:
            cmd = form.format(v='120')
            assert check_bid_value_shape(cmd, task_dir=None) is None, cmd

    def test_absurd_value_passes_without_approval(self):
        # 999 in a bid context: denied only when an approval exists to
        # say the user never approved it.
        assert (
            check_bid_value_shape(
                'fill_input("input.bid-input", "999")', task_dir=None
            )
            is None
        )

    def test_corrupt_approval_fails_open_on_magnitude(self, task_dir):
        p = ad_changes.APPROVED_DIR
        p.mkdir(parents=True)
        (p / f'{TASK_ID}.tsv').write_text('not,a,tsv\n', encoding='utf-8')
        assert (
            check_bid_value_shape('type_text("999")  # bid cell', task_dir)
            is None
        )
        # The shape check still fires without a readable approval.
        assert (
            check_bid_value_shape('type_text("3.002.27") # bid', task_dir)
            is not None
        )


class TestBidMustMatchTheFrozenApproval:
    def test_approved_bid_passes_in_every_typing_form(self, task_dir):
        freeze(['120', '3.00'])
        for form in BID_FORMS:
            for v in ('120', '3.0', '3.00'):
                cmd = form.format(v=v)
                assert check_bid_value_shape(cmd, task_dir) is None, cmd

    def test_typed_113_when_approval_says_3_is_denied(self, task_dir):
        """The shipped clear-failure: 3.0 typed into a field still
        showing 11 concatenated to 113.0. The approval is what says the
        run never approved 113."""
        freeze(['3.00'])
        deny = check_bid_value_shape(
            'browser-use input 12 "113.0"  # set bid', task_dir
        )
        assert deny is not None
        assert '113.0' in deny
        assert '3' in deny  # names the approved value

    def test_small_deviation_outside_tolerance_denied(self, task_dir):
        freeze(['1.30'])
        assert (
            check_bid_value_shape('type_text("1.20")  # bid', task_dir)
            is not None
        )
        # At tolerance: ad_changes reconciles executions at ±0.01 too.
        assert (
            check_bid_value_shape('type_text("1.31")  # bid', task_dir) is None
        )

    def test_approval_without_bids_denies_any_bid(self, task_dir):
        """A pause-only approval holds no bid: typing one is a change
        the user never approved."""
        freeze([''], name='')  # blank target_bid rows
        deny = check_bid_value_shape(
            'fill_input("input.bid", "0.75")', task_dir
        )
        assert deny is not None
        assert 'holds no bid' in deny

    def test_earlier_freezes_count_too(self, task_dir):
        """A twice-approved task may execute rows from either freeze;
        the log is reconciled against both, and so is the typed value."""
        freeze(['2.50'])
        freeze(['1.20'], name='.1')
        assert (
            check_bid_value_shape('type_text("2.5")  # bid', task_dir) is None
        )
        assert (
            check_bid_value_shape('type_text("1.2")  # bid', task_dir) is None
        )
        assert (
            check_bid_value_shape('type_text("4.4")  # bid', task_dir)
            is not None
        )

    def test_many_bids_lists_a_cap(self, task_dir):
        freeze([f'{i // 10 * 0.1 + i % 10:.2f}' for i in range(1, 14)])
        deny = check_bid_value_shape('type_text("99.9")  # bid', task_dir)
        assert deny is not None and '…' in deny


def test_the_bash_deny_chain_runs_it(task_dir):
    deny = first_bash_deny(
        'fill_input("input.bid-input", "3.002.27")', task_dir
    )
    assert deny is not None
    assert 'decimal point' in deny[1]
    # The chain passes task_dir through: the approval check is live
    # there, not just when the guard is called directly.
    freeze(['3.00'])
    deny = first_bash_deny('type_text("113.0")  # bid', task_dir)
    assert deny is not None
    assert deny[0] == 'Bid-value sanity'
