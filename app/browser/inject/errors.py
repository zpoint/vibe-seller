"""Typed, actionable errors for inject-driven browser backends.

Each carries a stable ``code`` and ``hint`` so the operator sees exactly
what to do (log in vs fix the store name vs restart the client) instead
of one generic failure. The classifier in the task layer surfaces these
verbatim.
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


class StoreNotFound(InjectError):
    code = 'STORE_NOT_FOUND'
    hint = 'Check the store name, or that it is visible to this login.'


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
