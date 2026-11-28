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
    """The client is logged out and could not be logged in.

    ``credentials_stored`` says whether the vendor had stored
    credentials to try (the task layer decides terminality from it):
    with none stored only the operator can fix this, so the task
    ends FAILED; with credentials that did not take, a retry may
    still succeed, so the run continues (see ``is_terminal``).
    """

    code = 'LOGIN_REQUIRED'
    hint = 'Log into the client on this machine, then retry / refresh.'

    def __init__(self, message: str = '', *, credentials_stored: bool = False):
        super().__init__(message)
        self.credentials_stored = credentials_stored


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
    driven by the inject engine. The surfaced text carries this
    class's stable ``code`` (see ``__str__``) so the UI can link the
    docs section for it by token, not by translatable prose.
    """

    code = 'UNSUPPORTED_KERNEL'
    hint = (
        'This store does not use a Chrome (Chromium) kernel; switch '
        'its kernel to Chrome in the client. See the browsers guide.'
    )

    def __str__(self) -> str:
        # The task-error text is what the UI matches docs links
        # against; prose is vendor-supplied and translated, so the
        # stable taxonomy CODE rides along instead. Only this class
        # carries the token — existing errors read exactly as before.
        return f'[{self.code}] {super().__str__()}'


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


# ── Terminality policy (consumed by the task layer) ────────────────
#
# A TERMINAL start failure cannot be fixed from inside the task —
# only the operator can (switch the kernel, log the client in, pay
# for the API plan, re-bind the store). The server records the code
# and message on the task, stops the agent and ends the task FAILED,
# so the failure banner and its docs link render and the agent never
# treats the error as a puzzle to solve (incident 97db66d2: a bare
# HTTP 500 made an agent SSH into a production machine).
#
# TRANSIENT failures are worth exactly one retry from the wrapper
# (the client may just be coming up); past that the agent gets the
# typed line but the task is not failed server-side.
TERMINAL_CODES = frozenset({
    'UNSUPPORTED_KERNEL',
    'STORE_UNAVAILABLE',
    'STORE_NOT_FOUND',
    'PLAN_REQUIRED',
    'API_NOT_ENABLED',
    'ACCOUNT_MISMATCH',
})

TRANSIENT_CODES = frozenset({
    'CLIENT_NOT_RUNNING',
    'CDP_UNREACHABLE',
})


def is_terminal(exc: 'InjectError') -> bool:
    """Does this start failure end the task (operator-only fix)?

    LOGIN_REQUIRED is terminal only when the vendor had NO stored
    credentials to try; with credentials that failed to log in, a
    retry can still succeed. Unknown codes default to NOT terminal —
    the failure reaches the agent as a typed line, and only codes the
    taxonomy explicitly declares terminal stop the task.
    """
    if isinstance(exc, LoginRequired):
        return not exc.credentials_stored
    return exc.code in TERMINAL_CODES
