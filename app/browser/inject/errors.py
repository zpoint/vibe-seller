"""Typed, actionable errors for inject-driven browser backends.

Each carries a stable ``code`` and a vendor-neutral ``hint`` so the
operator sees exactly what to do (log in vs fix the store vs restart
the client) instead of one generic failure. The classifier in the task
layer surfaces these verbatim.

This taxonomy is shared by every vendor adapter. Product names never
appear here: a vendor that needs product-specific guidance passes its
own message to the constructor (``StoreUnavailable`` carries the
vendor's own error text), and vendor display names / docs anchors come
from the vendor's spec, not from these hints.
"""


class InjectError(RuntimeError):
    """Base for inject-backend failures with an operator-facing code."""

    code = 'INJECT_ERROR'
    hint = ''

    def __init__(self, message: str = ''):
        super().__init__(message or self.hint or self.code)


class ClientNotRunning(InjectError):
    code = 'CLIENT_NOT_RUNNING'
    hint = 'Start the browser client, or check its install path.'


class LoginRequired(InjectError):
    code = 'LOGIN_REQUIRED'
    hint = 'Log into the client on this machine, then retry / refresh.'


class AccountMismatch(InjectError):
    """The client is logged into a different account than the saved one.

    Never resolved automatically: logging someone out of their session
    is the operator's decision. The constructor message names the
    account (the vendor adapter fills in what the client reports).
    """

    code = 'ACCOUNT_MISMATCH'
    hint = (
        'The client is logged into a different account than the saved '
        'credentials. Log out in the client, or re-save the matching '
        'credentials, then retry.'
    )


class ApiNotEnabled(InjectError):
    """The client ships a local API but it is switched off.

    The #1 support case for vendors that gate their local API behind a
    client setting (most vendors ship it disabled by default).
    """

    code = 'API_NOT_ENABLED'
    hint = (
        'The local API of the client is switched off. Enable it in '
        'its settings (look for API / 自动化 / local API), then retry.'
    )


class PlanRequired(InjectError):
    """The client's local API requires a paid plan."""

    code = 'PLAN_REQUIRED'
    hint = (
        'The local API of the client requires a paid plan. Upgrade '
        'the account (or pick a plan that includes API access), then '
        'retry.'
    )


class StoreNotFound(InjectError):
    code = 'STORE_NOT_FOUND'
    hint = 'Check the store name, or that it is visible to this login.'


class StoreUnavailable(InjectError):
    """The store exists for this login but cannot be opened.

    Carries the vendor's own message in the constructor (e.g. no device
    bound to the store, the store is expired/disabled in the vendor
    client). The vendor adapter appends its product-specific fix hint;
    the class stays vendor-neutral.
    """

    code = 'STORE_UNAVAILABLE'
    hint = 'The store exists but cannot be opened; the message says why.'


class UnsupportedKernel(InjectError):
    """The env runs a non-Chromium kernel the backend cannot drive.

    Firefox-kernel envs expose no CDP endpoint, so they cannot be
    driven by the inject engine.
    """

    code = 'UNSUPPORTED_KERNEL'
    hint = (
        'This store does not use a Chrome (Chromium) kernel; switch '
        'its kernel to Chrome in the client. See the browsers guide.'
    )


class CdpUnreachable(InjectError):
    code = 'CDP_UNREACHABLE'
    hint = 'Retry; if it persists the client version may need adapting.'


class AccountConflict(InjectError):
    code = 'ACCOUNT_CONFLICT'
    hint = 'One login per machine: stop the other account sessions first.'


class IdleExpired(InjectError):
    code = 'IDLE_EXPIRED'
    hint = 'The session dropped while idle; it will be reopened.'


class UnsupportedPlatform(InjectError):
    code = 'UNSUPPORTED_PLATFORM'
    hint = 'This backend is Windows-only (incl. WSL on Windows).'
