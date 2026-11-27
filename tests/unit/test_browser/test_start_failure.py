"""The terminal-browser-start-failure contract's policy layer.

What decides whether a failed store-browser start ends the task
(``app/browser/inject/errors.py``) and what the task layer records /
answers (``app/browser/start_failure.py``). The wrapper's own line and
retry are executed for real in ``test_wrapper_start_failure.py``; the
route → task transition against a live app is
``tests/workflow/test_wf_browser_start_failure.py``.
"""

import pytest

from app.browser.inject.errors import (
    TERMINAL_CODES,
    TRANSIENT_CODES,
    AccountMismatch,
    ApiNotEnabled,
    CdpUnreachable,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    PlanRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
    is_terminal,
)
from app.browser.start_failure import (
    category_for,
    error_body,
    typed_error_text,
)

pytestmark = pytest.mark.unit

# Every code the contract declares TERMINAL: the task ends FAILED and
# the agent stops (only the operator can fix these).
TERMINAL = [
    UnsupportedKernel('kernel reports Firefox'),
    StoreUnavailable('no device bound'),
    StoreNotFound('missing'),
    PlanRequired('api needs a plan'),
    ApiNotEnabled('api off'),
    AccountMismatch('different account'),
]

# TRANSIENT: typed line to the agent, one wrapper retry, task NOT
# failed server-side.
NOT_TERMINAL = [ClientNotRunning(), CdpUnreachable('no answer')]


class TestIsTerminal:
    @pytest.mark.parametrize('exc', TERMINAL, ids=lambda e: e.code)
    def test_terminal_codes(self, exc):
        assert is_terminal(exc)

    @pytest.mark.parametrize('exc', NOT_TERMINAL, ids=lambda e: e.code)
    def test_transient_codes(self, exc):
        assert not is_terminal(exc)

    def test_login_required_without_stored_credentials_is_terminal(self):
        assert is_terminal(LoginRequired())

    def test_login_required_with_stored_credentials_is_not_terminal(self):
        # Credentials existed and the login did not take — a retry can
        # still succeed, so the run is not ended server-side.
        assert not is_terminal(
            LoginRequired('stayed logged out', credentials_stored=True)
        )

    def test_unknown_code_defaults_to_not_terminal(self):
        # A code the policy does not explicitly declare never stops a
        # task: the failure reaches the agent as a typed line only.
        assert not is_terminal(InjectError('mystery failure'))

    def test_terminal_code_sets_match_the_contract(self):
        assert TERMINAL_CODES == frozenset(exc.code for exc in TERMINAL)
        assert TRANSIENT_CODES == frozenset(exc.code for exc in NOT_TERMINAL)
        assert not (TERMINAL_CODES & TRANSIENT_CODES)


class TestTaskLayerShapes:
    def test_error_body_carries_code_message_hint(self):
        exc = UnsupportedKernel('this kernel reports Firefox/100')
        body = error_body(exc)
        assert body['code'] == 'UNSUPPORTED_KERNEL'
        assert 'Firefox/100' in body['message']
        assert body['hint']  # the operator-facing hint always present

    def test_typed_error_text_prefixes_the_code(self):
        assert (
            typed_error_text(StoreNotFound('gone')) == '[STORE_NOT_FOUND] gone'
        )

    def test_typed_error_text_never_double_prefixes(self):
        # UnsupportedKernel prefixes its own code in __str__ (what the
        # UI's docs mapping keys on) — the task layer must not wrap it
        # again.
        text = typed_error_text(UnsupportedKernel('kernel is Firefox'))
        assert text.count('[UNSUPPORTED_KERNEL]') == 1
        assert text.startswith('[UNSUPPORTED_KERNEL]')

    def test_category_is_the_snake_cased_code(self):
        assert category_for(UnsupportedKernel()) == 'unsupported_kernel'
        assert category_for(AccountMismatch()) == 'account_mismatch'
