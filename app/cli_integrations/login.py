"""Drive a CLI's login from the Settings page — no terminal anywhere.

Each platform's login is a short list of CLI invocations
(``CliIntegration.login_steps``) that print a URL / device code or write
a QR image, then block until the person approves in their browser or
app. One :class:`LoginSession` per integration runs them in order and
publishes a :class:`LoginView` the page polls; it is the ONLY owner of
the child process — cancel, timeout and server shutdown all go through
it, and they kill the whole process tree.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
from urllib.parse import parse_qs, urlsplit
import urllib.request

import psutil

from app.cli_integrations.base import CliIntegration, LoginStep, LoginView
from app.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_TAIL_LINES = 20
_MAX_MESSAGE = 400
# A CLI may print one long JSON line (lark-cli lists every granted scope).
_LINE_LIMIT = 1024 * 1024
_RELAY_TIMEOUT_S = 60.0
_LOCAL_HOSTS = ('localhost', '127.0.0.1')


def _summary(lines: list[str], rc: int) -> str:
    """A readable line from a failed step's output: the last one that is
    prose, not a piece of a JSON document — never the raw dump."""
    for line in reversed(lines):
        text = line.strip()
        if text and not text.startswith(('{', '}', '[', ']', '"')):
            return _clip(text)
    return f'exit code {rc}'


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 1] + '…'


def _kill_descendants(pid: int) -> None:
    """Kill the processes the CLI started (blocking, best effort).

    Signals only — never a wait: ``psutil.wait_procs`` would also
    ``waitpid`` the CLI itself, and its exit belongs to the event loop
    that spawned it (uvloop, which uvicorn runs, reaps in C and only then
    resolves ``proc.wait()`` — a stolen exit status leaves that await,
    the cancel request and the server's shutdown hanging forever).
    Orphaned grandchildren are reaped by init.
    """
    try:
        kids = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return
    for p in kids:
        with contextlib.suppress(psutil.Error):
            p.kill()


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the CLI and what it started; its exit is reaped by the loop."""
    if proc.returncode is not None:
        return
    await asyncio.to_thread(_kill_descendants, proc.pid)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), 10)


class LoginSession:
    """One login attempt of one integration."""

    def __init__(
        self,
        integ: CliIntegration,
        binary: Path,
        steps: list[LoginStep],
        session_dir: Path,
        env: dict[str, str],
        on_done=None,
        verify=None,
    ) -> None:
        self.integ = integ
        self.binary = binary
        self.steps = steps
        self.dir = session_dir
        self.env = env
        self.view = LoginView(steps=len(steps))
        self.started = time.monotonic()
        self._on_done = on_done
        # ``await verify()`` → is the CLI signed in now? The outcome of a
        # login is that, not the last exit code: lark-cli exits non-zero
        # when it signed in but the org admin withheld some scopes.
        self._verify = verify
        self._proc: asyncio.subprocess.Process | None = None
        self._qr: Path | None = None
        self._tail: list[str] = []
        self.task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def qr_path(self) -> Path | None:
        """The QR image of the running step, once the CLI said so."""
        if self.view.qr and self._qr is not None and self._qr.is_file():
            return self._qr
        return None

    def start(self) -> None:
        self.task = asyncio.create_task(
            self._run(), name=f'login-{self.integ.key}'
        )

    async def cancel(self) -> None:
        if self.active:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        if self.view.state not in ('succeeded', 'failed'):
            self.view.state = 'cancelled'

    async def paste_back(self, address: str) -> None:
        """Deliver the address the person's browser ended on to the
        CLI's local listener (``LoginStep.paste_back``).

        The approval is a redirect to ``LoginView.callback`` — a port
        on THIS computer. A browser elsewhere (the page opened over the
        network) lands on its own machine instead and shows "cannot be
        reached"; its address bar still holds the answer, and this
        hands it on exactly as the browser would have. Only that one
        local port, and only an address carrying the approval (or the
        refusal) is ever sent.
        """
        target = self._relay_target(address)
        await asyncio.to_thread(_relay, target)

    def _relay_target(self, address: str) -> str:
        if not (self.view.paste_back and self.view.callback):
            raise ValueError('this sign-in is not waiting for an address')
        want = urlsplit(self.view.callback)
        got = urlsplit(address.strip())
        if got.scheme != 'http' or got.hostname not in _LOCAL_HOSTS:
            raise ValueError(
                f'not a local address: {got.scheme}://{got.netloc}'
            )
        if got.port != want.port:
            raise ValueError(
                f"port {got.port} is not this sign-in's {want.port}"
            )
        query = parse_qs(got.query)
        if 'code' not in query and 'error' not in query:
            raise ValueError('the address carries no approval')
        path = got.path or '/'
        return f'http://127.0.0.1:{want.port}{path}?{got.query}'

    async def _run(self) -> None:
        try:
            rc = 0
            for i, step in enumerate(self.steps, 1):
                self.view.step = i
                rc = await self._run_step(step)
                if rc != 0 and i < len(self.steps):
                    self._fail(rc)  # a setup step must succeed
                    return
            signed_in = (
                await self._verify() if self._verify is not None else rc == 0
            )
            if not signed_in:
                self._fail(rc)
                return
            # Signed in, but the session is not over: ``on_done`` still
            # enables the platform (fetching its skills takes seconds).
            # The page polls until ``finishing`` drops; reading the row
            # in between would show "signed in, AI can't use it" and
            # stop there.
            self.view.finishing = self._on_done is not None
            self.view.state = 'succeeded'
            self.view.url = self.view.user_code = self.view.hint = None
            self.view.callback = None
            self.view.qr = self.view.paste_back = False
            if rc != 0 and not self.view.notices:
                # Signed in, but the CLI said something: keep it visible.
                self.view.message = self.view.message or _summary(
                    self._tail, rc
                )
        except asyncio.CancelledError:
            self.view.state = 'cancelled'
            raise
        except Exception as e:  # never leave the page spinning
            logger.exception('%s login failed', self.integ.key)
            self.view.state = 'failed'
            self.view.message = str(e)
        finally:
            try:
                if self._on_done is not None:
                    with contextlib.suppress(Exception):
                        await self._on_done(self)
            finally:
                self.view.finishing = False

    def _fail(self, rc: int) -> None:
        self.view.state = 'failed'
        if self.view.message is None and not self.view.notices:
            self.view.message = _summary(self._tail, rc)

    async def _run_step(self, step: LoginStep) -> int:
        session = str(self.dir)
        self._tail = []
        args = [a.replace('{session}', session) for a in step.argv]
        self._qr = self.dir / step.qr_file if step.qr_file else None
        self.view.url = self.view.user_code = self.view.callback = None
        self.view.qr = False
        self.view.state = 'starting'
        self.view.hint = step.hint
        self.view.paste_back = step.paste_back
        kwargs: dict = {}
        if IS_WINDOWS:
            # The server may be a console-less tray app: no window pops.
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        proc = await asyncio.create_subprocess_exec(
            str(self.binary),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=session,
            env=self.env,
            limit=_LINE_LIMIT,
            **kwargs,
        )
        self._proc = proc
        try:
            return await asyncio.wait_for(
                self._pump(proc, step), step.timeout_s
            )
        except TimeoutError:
            self.view.error = 'timeout'
            self.view.message = 'timed out waiting for the approval'
            return 124
        finally:
            await _terminate(proc)
            self._proc = None

    async def _pump(self, proc: asyncio.subprocess.Process, step) -> int:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode('utf-8', 'replace').rstrip()
            if not line:
                continue
            self._tail = [*self._tail[-(_TAIL_LINES - 1) :], line]
            # The parser decides when the QR is ready: a CLI may create
            # the file empty and fill it later (wecom-cli does).
            step.parse(line, self.view)
            if self.view.message is not None:
                self.view.message = _clip(self.view.message)
            if self.view.url or self.view.user_code or self.view.qr:
                self.view.state = 'waiting'
        return await proc.wait()


class LoginManager:
    """At most one running login per integration."""

    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}

    def get(self, key: str) -> LoginSession | None:
        return self._sessions.get(key)

    async def start(
        self,
        integ: CliIntegration,
        binary: Path,
        steps: list[LoginStep],
        session_dir: Path,
        env: dict[str, str],
        on_done=None,
        verify=None,
    ) -> LoginSession:
        """Replace any earlier session of *integ* with a fresh one."""
        old = self._sessions.get(integ.key)
        if old is not None:
            await old.cancel()
        await asyncio.to_thread(_fresh_dir, session_dir)
        session = LoginSession(
            integ, binary, steps, session_dir, env, on_done, verify
        )
        self._sessions[integ.key] = session
        session.start()
        return session

    async def cancel(self, key: str) -> bool:
        session = self._sessions.get(key)
        if session is None:
            return False
        await session.cancel()
        return True

    async def shutdown(self) -> None:
        """Server shutdown: no login process outlives the server."""
        for session in list(self._sessions.values()):
            await session.cancel()


def _relay(url: str) -> None:
    """GET *url* (a 127.0.0.1 address) directly — never through a
    proxy, which could not reach this computer's loopback (blocking)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=_RELAY_TIMEOUT_S) as resp:
            resp.read()
    except (urllib.error.HTTPError, http.client.RemoteDisconnected):
        # The CLI got the address and answered with an error page, or
        # with nothing (gws's proxy-aware sign-in drops the connection
        # on a refusal): either way its exit decides the outcome.
        return
    except (OSError, urllib.error.URLError) as e:
        raise ValueError(f'the sign-in is no longer listening: {e}') from e


def _fresh_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        os.chmod(path, 0o700)  # holds a QR that grants a login


login_manager = LoginManager()
