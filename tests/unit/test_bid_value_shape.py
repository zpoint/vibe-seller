"""The pre-live bid-value shape guard (the 10x overspend bug).

The Shadow-DOM bid input does not reliably clear, so a 1.30 target can
become 11.3 or 3.002.27. ``check_bid_value_shape`` denies the value-SHAPE
pathology (concatenation, absurd magnitude) in the Bash command before it
goes live; whether the value matches the approved change is the
``ad_changes`` exit gate's job.
"""

import pytest

from app.ai.bash_safety import check_bid_value_shape, first_bash_deny

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ('value', 'denied'),
    [
        ('1.30', False),
        ('4.00', False),
        ('24.38', False),  # a legit high product-targeting bid
        # 11.3 is a plausible single-decimal value — the shape guard
        # cannot catch it; the ad_changes exit gate checks the applied
        # value against the approved change.
        ('11.3', False),
        ('3.002.27', True),  # concatenation (two decimal points)
        ('1.301.30', True),  # concatenation
        ('113.0', True),  # decimal, > ceiling — the shipped real-money bug
        ('999.0', True),  # absurd magnitude, decimal-shaped
        # Only DECIMAL-shaped values here — they behave the same in every
        # form below. Integer magnitudes are context-dependent and are
        # tested separately.
    ],
)
def test_check_bid_value_shape(value, denied):
    # Caught however the bid is typed: the legacy 0.12 subcommand, the
    # 0.13 typing helpers, or a JS value assignment.
    for cmd in (
        f'browser-use input 47 "{value}"',
        f'fill_input("input.bid-input", "{value}")',
        f'type_text("{value}")  # into the bid cell',
        f"js(\"q('.bid-input').value = '{value}'\")",
    ):
        result = check_bid_value_shape(cmd)
        assert (result is not None) is denied, cmd


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


def test_denies_absurd_integer_in_bid_context():
    """A currency can be typed without a decimal, so an absurd INTEGER in
    an explicit bid context is still denied."""
    assert (
        check_bid_value_shape('fill_input("input.bid-input", "999")')
        is not None
    )
    assert check_bid_value_shape('type_text("999")  # bid cell') is not None
    assert (
        check_bid_value_shape('fill_input("input.bid-input", "999.0")')
        is not None
    )


def test_allows_integer_otp_and_quantities_in_legacy_form():
    """The legacy ``browser-use input <idx>`` form is OTP/postal/quantity
    ambiguous: integers pass the ceiling (firing on a 6-digit OTP once
    blocked live login), a decimal above it is still caught."""
    assert check_bid_value_shape('browser-use input 47 "446770"') is None
    assert check_bid_value_shape('browser-use input 12 "875765"') is None
    assert check_bid_value_shape('browser-use input 3 "12345"') is None
    assert check_bid_value_shape('browser-use input 47 "113.0"') is not None


def test_the_bash_deny_chain_runs_it():
    deny = first_bash_deny('fill_input("input.bid-input", "3.002.27")')
    assert deny is not None
    assert 'decimal point' in deny[1]
