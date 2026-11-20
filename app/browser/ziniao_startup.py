"""Get the Ziniao client answering in WebDriver mode — the one way.

Every caller that needs Ziniao (every task on a Ziniao store before its
agent runs, browser start, store setup) goes through
:func:`ensure_ziniao_running` and gets the same behaviour: if the client's
HTTP API answers, use it; if nothing answers — not running, opened by hand
in normal mode, listening on another port, or leftovers from a crash —
stop every Ziniao process and launch it in WebDriver mode. Nobody is asked
and there is no "force" switch.

Startup follows Ziniao's own WebDriver doc (``ziniao-open/skill-hub`` →
``skills/ziniao-webdriver-doc``): kill every Ziniao process before each
launch and before the retry, and give HTTP requests ≥120 s. Each launch
attempt has a 120 s deadline; a request the client has *accepted* may use
all of what is left (:func:`_wait_ready`). A launch that misses the
deadline is killed and relaunched once; only then does it fail.

What is never killed: a client that *accepted* the connection but is slow
while this server has live Ziniao browsers (its kernels serve CDP without
the control API — killing it would take every running task down), and on
WSL anything that a timeout alone points at.

One startup round runs at a time per server (kill-all is machine-wide, so
two rounds would kill each other's launch); concurrent callers join it.
See docs/ziniao-concurrency.md § Client startup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.browser.launch_guards import BrowserBusyError
from app.browser.ziniao_processes import (
    KillReport,
    describe,
    kill_all_ziniao,
    wsl_port_has_listener,
)
from app.browser.ziniao_utils import is_wsl, launch_ziniao, ziniao_host
from app.config import LOCALHOST
from app.env_options import Options
from app.models.ziniao_account import ZiniaoAccount
from app.platform import find_pid_listening_on_port
from app.telemetry_events import (
    BrowserFailureReason,
    TelemetryEvent,
    ZiniaoPollFailure,
    ZiniaoPriorState,
)
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

# The clock every deadline here reads (tests swap in a fake one).
_clock = time.monotonic

# Per launch attempt. Ziniao's doc asks for >=120 s per request.
STARTUP_DEADLINE_S = 120.0
LAUNCH_ATTEMPTS = 2
_POLL_GAP_S = 2.0
_CONNECT_S = 5.0
_CONNECT_S_WSL = 3.0
_PROBE_READ_S = 10.0
# A client that accepted the probe but did not answer may just be busy
# (another store's startBrowser). Give it this much more before deciding.
_PATIENCE_READ_S = 20.0

WSL_LAUNCH_HINT = (
    'WSL cannot start Ziniao by itself. On Windows, download and run '
    'ziniao_webdriver.bat (/api/ziniao/launcher), wait for "ready", '
    'then retry.'
)


class ZiniaoStartError(RuntimeError):
    """Ziniao could not be brought up. Carries why, as data.

    ``reason`` is the ``BrowserFailureReason``; ``task_category`` is the
    ``task.error_category`` the UI translates — callers read these
    instead of parsing the message.
    """

    def __init__(self, message: str, reason: str, task_category: str):
        super().__init__(message)
        self.reason = reason
        self.task_category = task_category


class ZiniaoAccountConflictError(RuntimeError):
    """Another Ziniao account owns the (one per machine) client."""

    task_category = 'ziniao_account_conflict'


class ZiniaoStartingError(BrowserBusyError):
    """The caller's wait ran out while a startup round is still going.

    A ``BrowserBusyError``, so routes answer 503 — nothing is known to be
    wrong yet, the round keeps running and a retry joins it.
    """

    def __init__(self, message: str, elapsed_s: float):
        super().__init__(message)
        self.elapsed_s = elapsed_s


@dataclass
class _Probe:
    result: dict | None
    failure: str | None


# Most informative first: "something accepted and did not answer" says
# more than "nothing listening".
_FAILURE_RANK = {
    ZiniaoPollFailure.READ_TIMEOUT: 4,
    ZiniaoPollFailure.BAD_RESPONSE: 3,
    ZiniaoPollFailure.CONNECT_TIMEOUT: 2,
    ZiniaoPollFailure.REFUSED: 1,
}


async def _probe_host(
    host: str, port: int, data: dict, connect_s: float, read_s: float
) -> _Probe:
    timeout = httpx.Timeout(read_s, connect=connect_s)
    try:
        # trust_env=False: Ziniao is always local; never via a proxy.
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            resp = await c.post(
                f'http://{host}:{port}', content=json.dumps(data)
            )
        body = resp.json()
    except httpx.ConnectTimeout:
        return _Probe(None, ZiniaoPollFailure.CONNECT_TIMEOUT)
    except httpx.ConnectError:
        return _Probe(None, ZiniaoPollFailure.REFUSED)
    except httpx.TimeoutException:
        return _Probe(None, ZiniaoPollFailure.READ_TIMEOUT)
    except (httpx.HTTPError, ValueError):
        return _Probe(None, ZiniaoPollFailure.BAD_RESPONSE)
    if not isinstance(body, dict):
        return _Probe(None, ZiniaoPollFailure.BAD_RESPONSE)
    return _Probe(body, None)


async def _probe(
    port: int, user_info: dict, connect_s: float, read_s: float
) -> _Probe:
    """One ``getBrowserList``. Any JSON answer (even -10003) means
    WebDriver mode; login errors are the caller's business.

    WSL tries 127.0.0.1 (mirrored networking) then the Windows gateway
    (NAT), like ``try_connect_ziniao``.
    """
    data = {
        'action': 'getBrowserList',
        'requestId': str(uuid.uuid4()),
        **user_info,
    }
    hosts = [await asyncio.to_thread(ziniao_host)]
    if is_wsl() and hosts[0] != LOCALHOST:
        hosts.insert(0, LOCALHOST)
    worst: str | None = None
    for host in hosts:
        p = await _probe_host(host, port, data, connect_s, read_s)
        if p.result is not None:
            return p
        if worst is None or _FAILURE_RANK[p.failure] > _FAILURE_RANK[worst]:
            worst = p.failure
    return _Probe(None, worst)


async def _wait_ready(
    port: int, user_info: dict, deadline_s: float
) -> tuple[bool, int, str | None]:
    """Poll until the API answers or the deadline passes.

    A refused connection (port not bound yet) comes back at once and is
    re-polled every 2 s, so a client that comes up in 8 s is seen in
    ~8-10 s. A connection the client *accepts* gets every second left
    before the deadline as its read timeout: the first ``getBrowserList``
    on a cold client signs in and can take tens of seconds, and cutting
    it short (the old 10 s cap) just makes the next request start over.

    Returns ``(ready, polls, last_failure)``.
    """
    end = _clock() + deadline_s
    polls = 0
    last: str | None = None
    while (remaining := end - _clock()) > 0:
        polls += 1
        p = await _probe(port, user_info, min(_CONNECT_S, remaining), remaining)
        if p.result is not None:
            return True, polls, None
        last = p.failure
        remaining = end - _clock()
        if remaining > 0:
            await asyncio.sleep(min(_POLL_GAP_S, remaining))
    return False, polls, last


def _send(event: str, props: dict) -> None:
    try:
        telemetry.send(
            event, {'backend': 'ziniao', 'os': telemetry.runtime_os(), **props}
        )
    except Exception:
        pass


def _prior_state(first_failure: str | None, report: KillReport) -> str:
    if first_failure == ZiniaoPollFailure.READ_TIMEOUT:
        return ZiniaoPriorState.UNRESPONSIVE
    if report.main_was_running:
        return ZiniaoPriorState.NORMAL_MODE
    if report.killed:
        return ZiniaoPriorState.LEFTOVERS
    return ZiniaoPriorState.NOT_RUNNING


def _account_key(user_info: dict) -> tuple[str, str]:
    return (user_info.get('company', ''), user_info.get('username', ''))


class _State:
    """Module state, bound to the event loop that created it (tests run
    one loop per test; production has one)."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.current: _Round | None = None
        # (account, port) of the last round that brought the client up.
        self.owner: tuple[tuple[str, str], int] | None = None
        # Last STARTUP_TIMEOUT: (monotonic time, error) — the cooldown.
        self.failed: tuple[float, ZiniaoStartError] | None = None


_state: _State | None = None


def _get_state() -> _State:
    global _state
    loop = asyncio.get_running_loop()
    if _state is None or _state.loop is not loop:
        _state = _State()
    return _state


class _Round:
    """One kill-and-launch round; at most one in flight per server."""

    def __init__(
        self, port: int, client_path: str, user_info: dict, first: str | None
    ):
        self.port = port
        self.client_path = client_path
        self.user_info = user_info
        self.first_failure = first
        self.started = _clock()
        self.finished: float | None = None
        self.task: asyncio.Task | None = None
        self.killed = 0
        self.prior = ZiniaoPriorState.NOT_RUNNING

    def _fail(
        self, message: str, reason: str, category: str, **props
    ) -> ZiniaoStartError:
        _send(
            TelemetryEvent.BROWSER_SESSION_FAILED,
            {
                'error_category': reason,
                'prior_state': self.prior,
                'killed_processes': self.killed,
                'elapsed_s': round(_clock() - self.started),
                'last_poll_error': 'none',
                **props,
            },
        )
        return ZiniaoStartError(message, reason, category)

    async def _kill_all(self, wsl: bool) -> KillReport:
        report = await asyncio.to_thread(kill_all_ziniao, wsl, self.client_path)
        self.killed += len(report.killed)
        if report.killed:
            logger.info(
                'Stopping every Ziniao process before launch: %s',
                describe(report.killed),
            )
        if report.survivors:
            raise self._fail(
                'Ziniao is running but could not be closed '
                f'({describe(report.survivors)}). It may have been started '
                'with admin rights. Close Ziniao completely by hand, then '
                'retry.',
                BrowserFailureReason.KILL_FAILED,
                'ziniao_kill_failed',
                launch_attempts=0,
            )
        if not wsl:
            pid = await asyncio.to_thread(find_pid_listening_on_port, self.port)
            if pid:
                raise self._fail(
                    f'Port {self.port} is used by another program (pid '
                    f'{pid}), so Ziniao cannot open its control port. '
                    'Close that program or change the port of this '
                    'Ziniao account, then retry.',
                    BrowserFailureReason.PORT_IN_USE,
                    'ziniao_port_in_use',
                    launch_attempts=0,
                )
        return report

    async def _run_wsl(self) -> None:
        """WSL cannot launch Ziniao. Close it only when Windows shows no
        listener on the port (normal mode / leftovers); a listener WSL
        cannot reach is a networking problem — killing it would lose a
        healthy client that only the .bat can bring back."""
        try:
            listening = await asyncio.to_thread(
                wsl_port_has_listener, self.port
            )
        except Exception as e:
            logger.warning('WSL interop check failed, not killing: %s', e)
            raise self._fail(
                f'Ziniao is not answering on port {self.port}. '
                + WSL_LAUNCH_HINT,
                BrowserFailureReason.WSL_NOT_RUNNING,
                'ziniao_not_running',
                launch_attempts=0,
            ) from e
        if listening:
            raise self._fail(
                f'Something on Windows is listening on port {self.port}, '
                'but WSL cannot reach it. Turn on WSL mirrored networking '
                '(see the Windows setup guide), then retry.',
                BrowserFailureReason.WSL_UNREACHABLE,
                'ziniao_wsl_unreachable',
                launch_attempts=0,
            )
        report = await self._kill_all(wsl=True)
        self.prior = _prior_state(self.first_failure, report)
        closed = (
            'Ziniao was open but not in WebDriver mode; it has been closed. '
            if report.killed
            else 'Ziniao is not running. '
        )
        raise self._fail(
            closed + WSL_LAUNCH_HINT,
            BrowserFailureReason.WSL_NOT_RUNNING,
            'ziniao_not_running',
            launch_attempts=0,
        )

    async def run(self) -> None:
        if is_wsl():
            await self._run_wsl()
        report = await self._kill_all(wsl=False)
        self.prior = _prior_state(self.first_failure, report)
        last: str | None = self.first_failure
        for attempt in range(1, LAUNCH_ATTEMPTS + 1):
            if attempt > 1:
                await self._kill_all(wsl=False)
            logger.info(
                'Launching Ziniao in WebDriver mode (port %d, attempt %d/%d, '
                'found: %s)',
                self.port,
                attempt,
                LAUNCH_ATTEMPTS,
                self.prior,
            )
            launched = _clock()
            try:
                await asyncio.to_thread(
                    launch_ziniao, self.client_path, self.port
                )
            except Exception as e:
                raise self._fail(
                    f'Could not open Ziniao: {e}',
                    BrowserFailureReason.LAUNCH_FAILED,
                    'ziniao_launch_failed',
                    launch_attempts=attempt,
                ) from e
            ready, polls, last = await _wait_ready(
                self.port, self.user_info, STARTUP_DEADLINE_S
            )
            if ready:
                took = _clock() - launched
                logger.info(
                    'Ziniao is up in WebDriver mode after %.1fs (attempt '
                    '%d/%d; stopped %d Ziniao process(es) before launch)',
                    took,
                    attempt,
                    LAUNCH_ATTEMPTS,
                    self.killed,
                )
                _send(
                    TelemetryEvent.BROWSER_SESSION_STARTED,
                    {
                        'auto_launched': True,
                        'attempts_to_success': polls,
                        'prior_state': self.prior,
                        'killed_processes': self.killed,
                        'launch_attempts': attempt,
                        'startup_s': round(took),
                    },
                )
                return
            logger.warning(
                'Ziniao gave no answer within %.0fs (attempt %d/%d, last '
                'check: %s)',
                STARTUP_DEADLINE_S,
                attempt,
                LAUNCH_ATTEMPTS,
                last,
            )
        elapsed = round(_clock() - self.started)
        raise self._fail(
            f'Ziniao did not start: it was opened {LAUNCH_ATTEMPTS} times '
            f'and gave no answer within {STARTUP_DEADLINE_S:.0f}s each time '
            f'({elapsed}s in all; last check: {last}). Check that Ziniao '
            'opens and signs in when you start it by hand, then retry.',
            BrowserFailureReason.STARTUP_TIMEOUT,
            'ziniao_start_timeout',
            launch_attempts=LAUNCH_ATTEMPTS,
            last_poll_error=last,
        )


def _round_done(state: _State, rnd: _Round, task: asyncio.Task) -> None:
    rnd.finished = _clock()
    if task.cancelled():
        return
    err = task.exception()  # retrieved: every joiner may be gone
    if err is None:
        state.owner = (_account_key(rnd.user_info), rnd.port)
        state.failed = None
    elif (
        isinstance(err, ZiniaoStartError)
        and err.reason == BrowserFailureReason.STARTUP_TIMEOUT
    ):
        state.failed = (rnd.finished, err)


async def _join(rnd: _Round, wait_s: float | None) -> None:
    shielded = asyncio.shield(rnd.task)
    if wait_s is None:
        await shielded
        return
    try:
        await asyncio.wait_for(shielded, max(wait_s, 0.01))
    except TimeoutError:
        held = _clock() - rnd.started
        raise ZiniaoStartingError(
            f'Ziniao is starting ({held:.0f}s so far; a start can take up '
            f'to {LAUNCH_ATTEMPTS * STARTUP_DEADLINE_S:.0f}s). Nothing is '
            'wrong yet — retry in a minute.',
            held,
        ) from None


async def _refuse_if_other_account(
    state: _State, user_info: dict, connect_s: float
) -> None:
    """Never kill the client another account brought up while it works."""
    if state.owner is None:
        return
    (owner_key, owner_port) = state.owner
    if owner_key == _account_key(user_info):
        return
    p = await _probe(owner_port, {}, connect_s, _PROBE_READ_S)
    if p.result is None and p.failure != ZiniaoPollFailure.READ_TIMEOUT:
        state.owner = None  # that client is gone
        return
    raise ZiniaoAccountConflictError(
        f'Ziniao is running for another Ziniao account (company '
        f'"{owner_key[0]}", port {owner_port}). Only one account can run '
        "at a time on this computer — stop that account's stores first."
    )


async def ensure_ziniao_running(
    socket_port: int,
    client_path: str,
    user_info: dict,
    *,
    wait_s: float | None = None,
    live: bool = False,
    session_start: bool = False,
    retry_failed: bool = False,
) -> None:
    """Return once Ziniao answers in WebDriver mode on ``socket_port``.

    Kills and (re)launches it when needed; raises ``ZiniaoStartError``
    when that fails.

    - ``wait_s`` bounds how long *this caller* waits on a startup round
      (HTTP routes must answer): ``ZiniaoStartingError`` then.
    - ``live``: this server has Ziniao browsers open or a launch going.
      A client that accepts but is slow is then left alone.
    - ``session_start`` marks the call that opens a store browser — only
      it records the already-running case.
    - ``retry_failed`` (a user clicking Refresh) skips the cooldown after
      a startup that timed out; automatic callers get that result at once.
    """
    state = _get_state()
    wsl = is_wsl()
    connect_s = _CONNECT_S_WSL if wsl else _CONNECT_S
    probed_at = _clock()
    p = await _probe(socket_port, user_info, connect_s, _PROBE_READ_S)
    if p.result is None and p.failure == ZiniaoPollFailure.READ_TIMEOUT:
        # Accepted, no answer: a WebDriver client on our port, busy or
        # hung. Give it a while longer before anything else.
        p = await _probe(socket_port, user_info, connect_s, _PATIENCE_READ_S)
        if p.result is None and p.failure == ZiniaoPollFailure.READ_TIMEOUT:
            if live or wsl:
                logger.warning(
                    'Ziniao on port %d accepted but did not answer in %.0fs; '
                    'not restarting it (%s).',
                    socket_port,
                    _PROBE_READ_S + _PATIENCE_READ_S,
                    'store browsers are open on it'
                    if live
                    else 'WSL never closes a client over a timeout',
                )
                return
    if p.result is not None:
        code = str(p.result.get('statusCode', ''))
        if code != '0':
            logger.warning(
                'Ziniao answered on port %d with statusCode=%s: %s',
                socket_port,
                code,
                p.result.get('err', ''),
            )
        if session_start:
            _send(
                TelemetryEvent.BROWSER_SESSION_STARTED,
                {'attempts_to_success': 1, 'auto_launched': False},
            )
        return

    while True:
        rnd = state.current
        if rnd is not None and not rnd.task.done():
            if rnd.port == socket_port:
                await _join(rnd, wait_s)
                return
            # A round for another port: let it finish, then look again.
            await asyncio.wait({rnd.task})
            continue
        if rnd is not None and (rnd.finished or _clock()) > probed_at:
            # A round ended after our probe and may have just brought the
            # client up — look before killing it again.
            probed_at = _clock()
            p = await _probe(socket_port, user_info, connect_s, _PROBE_READ_S)
            if p.result is not None:
                return
            continue
        cooldown = Options.ZINIAO_START_COOLDOWN_S.get_float()
        if state.failed and not retry_failed:
            when, err = state.failed
            ago = _clock() - when
            if ago < cooldown:
                raise ZiniaoStartError(
                    f'{err} (Not retried: the last start failed {ago:.0f}s '
                    f'ago; the next automatic try is after '
                    f'{cooldown - ago:.0f}s.)',
                    err.reason,
                    err.task_category,
                )
        await _refuse_if_other_account(state, user_info, connect_s)
        if state.current is not rnd:
            continue  # someone started a round while we checked
        rnd = _Round(socket_port, client_path, user_info, p.failure)
        rnd.task = asyncio.create_task(rnd.run())
        rnd.task.add_done_callback(
            lambda t, s=state, r=rnd: _round_done(s, r, t)
        )
        state.current = rnd
        await _join(rnd, wait_s)
        return


async def ensure_ziniao_for_account(
    db: AsyncSession, account_id: str, who: str, **kwargs
) -> None:
    """``ensure_ziniao_running`` for a stored Ziniao account.

    Ends the caller's read transaction before waiting: a start can take
    minutes, and a session that stays in its transaction keeps a pooled
    connection for all of it (objects stay usable — the session factory
    sets ``expire_on_commit=False``).
    """
    account = await db.get(ZiniaoAccount, account_id)
    if not account:
        raise RuntimeError(f'Ziniao account not found for {who}.')
    user_info = {
        'company': account.company,
        'username': account.username,
        'password': decrypt_password(account.encrypted_password),
    }
    port = account.socket_port
    client_path = account.client_path or 'ziniao'
    await db.commit()
    await ensure_ziniao_running(port, client_path, user_info, **kwargs)
