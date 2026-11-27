"""Open and drive a Windows Electron app's main-process Node inspector.

Shared by the inject-driven adapters: a vendor client's
Electron fuses leave ``RunAsNode`` and ``EnableNodeCliInspectArguments``
on, so launching the installed exe with ``ELECTRON_RUN_AS_NODE=1 -e
"process._debugProcess(<main pid>)"`` opens the main-process inspector
on 127.0.0.1:9229. From there ``Runtime.evaluate`` runs in the MAIN
process, and ``BrowserWindow.getAllWindows()[0].webContents
.executeJavaScript(js)`` runs in the first window's renderer.

The adapter supplies only the PowerShell fragment that resolves the
installed exe to ``$exe`` (an :class:`InspectorSession` needs no
client-specific knowledge beyond that). The inspector is opened per
operation and closed right after, so no code-exec surface lingers.

``process._debugProcess`` always opens on 9229, so sessions collide
machine-wide (two vendor clients, two server instances, an outside
probe). ``open`` therefore takes a machine-wide lock, refuses a port
that already serves a session it did not open, and verifies the pid —
see :class:`InspectorBusy`.

Validated live on real clients (2026-11): use a CURRENT main pid — a
stale pid fails with an error.
"""

import asyncio
from collections.abc import Callable
import json
import logging

import aiohttp

from app.browser.machine_lock import MachineLock
from app.browser.win_app_locator import run_powershell as _run_inspector_ps

logger = logging.getLogger(__name__)


class InspectorError(RuntimeError):
    """The Node inspector could not be opened or driven."""


class InspectorBusy(InspectorError):
    """9229 is serving ANOTHER process's inspector session.

    ``process._debugProcess`` always opens on 127.0.0.1:9229, so two
    Electron clients on one machine (or an outside probe) collide: the
    loser would silently attach to the WINNER's inspector — running one
    vendor's login JS in another vendor's window and closing someone
    else's session on cleanup (both observed live). ``open`` refuses
    instead: it never attaches to (and never closes) a session it did
    not open itself. A caller may retry after a short backoff; the
    other session ends when its owner closes it.
    """


class _StaleInspectorTarget(Exception):
    """The inspector's remembered target id went away (window reload).

    Signals :meth:`InspectorSession.evaluate` to re-discover the target
    and retry once; never escapes to callers.
    """


class InspectorSession:
    """Open/drive/close an Electron main-process Node inspector.

    ``exe_path_script`` returns the PowerShell preamble that assigns the
    app's installed exe path to ``$exe`` (called at ``open`` time so a
    fresh locate/discovery happens per session).

    ``open`` takes a MACHINE-WIDE lock (one inspector session on this
    machine at a time — two vendors, or two server instances, must not
    both drive 9229), refuses when the port already serves a session it
    did not open, and verifies the connected process IS the pid it was
    asked to debug (an outside probe takes no lock of ours).

    Subclassing for another platform: override ONLY ``_trigger`` — the
    base ``open`` handles locking, the busy check and the pid
    verification for every platform.
    """

    #: The machine lock name (one inspector per machine, per the class
    #: docstring). Distinct from every other MachineLock user.
    LOCK_NAME = 'inspector'
    #: How long ``open`` waits for the machine lock (another session
    #: closing down) before giving up.
    LOCK_WAIT_S = 30.0
    #: Busy retries: a collision with a session that is on its way out.
    BUSY_RETRIES = 2
    BUSY_BACKOFF_S = 1.0

    def __init__(
        self,
        exe_path_script: Callable[[], str],
        host: str = '127.0.0.1',
        port: int = 9229,
    ):
        self._exe_path_script = exe_path_script
        self._host = host
        self._port = port
        self._ws_url: str | None = None
        self._lock: MachineLock | None = None

    async def open(self, main_pid: int) -> None:
        """Open the inspector on ``main_pid`` (locked, checked, verified).

        In order: take the machine-wide lock → refuse if 9229 already
        serves an inspector we did not open → trigger (platform hook) →
        connect → verify ``process.pid`` is the pid we asked for. On a
        mismatch the socket is dropped WITHOUT ``inspector.close()``
        (it is someone else's session) and ``InspectorBusy`` is raised
        after a short bounded retry.
        """
        if self._ws_url is not None:
            return  # already open
        last_busy: InspectorBusy | None = None
        for attempt in range(self.BUSY_RETRIES + 1):
            lock = MachineLock(self.LOCK_NAME)
            await lock.acquire(timeout=self.LOCK_WAIT_S)
            self._lock = lock
            try:
                if await self._discover_ws():
                    raise InspectorBusy(
                        f'{self._host}:{self._port} is already serving '
                        'an inspector session this process did not open; '
                        'refusing to attach to (or close) it'
                    )
                await self._trigger(main_pid)
                for _ in range(12):
                    url = await self._discover_ws()
                    if url:
                        self._ws_url = url
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise InspectorError(
                        f'inspector did not open on {self._host}:{self._port}'
                    )
                pid = await self._probe_pid()
                if pid is not None and int(pid) != int(main_pid):
                    # Attached to someone ELSE'S inspector (an outside
                    # probe holds 9229 and takes no lock of ours).
                    # Disconnect WITHOUT closing their session.
                    self._ws_url = None
                    raise InspectorBusy(
                        f'{self._host}:{self._port} is serving a '
                        f'DIFFERENT process (pid {pid}, expected '
                        f'{main_pid}); not touching that session'
                    )
                return
            except InspectorBusy as e:
                last_busy = e
                self._release_lock()
                if attempt < self.BUSY_RETRIES:
                    await asyncio.sleep(self.BUSY_BACKOFF_S)
                continue
            except Exception:
                self._release_lock()
                raise
        raise last_busy  # type: ignore[misc]

    async def _trigger(self, main_pid: int) -> None:
        """Platform hook: make THE target process open its inspector.

        The default (Windows and WSL driving the Windows side) runs the
        installed exe under ``ELECTRON_RUN_AS_NODE`` with
        ``process._debugProcess(main_pid)``. A subclass on another
        platform overrides ONLY this method (e.g. SIGUSR1 on macOS) and
        gets locking, the busy check and the pid verification from the
        base ``open`` for free.

        Contract: return once the trigger has been ISSUED (or raise on
        a definite failure); the base ``open`` then polls
        ``host:port/json`` for the websocket target. Must not itself
        connect to the inspector.
        """
        script = (
            self._exe_path_script() + "$env:ELECTRON_RUN_AS_NODE='1';"
            '$env:NODE_OPTIONS=$null;'
            'Start-Process -FilePath $exe -ArgumentList '
            f"'-e',('process._debugProcess({main_pid})') -NoNewWindow;'ok'"
        )
        await asyncio.to_thread(_run_inspector_ps, script)

    def _release_lock(self) -> None:
        if self._lock is not None:
            try:
                self._lock.release()
            finally:
                self._lock = None

    async def _probe_pid(self) -> int | None:
        """The pid of the process behind the connected inspector.

        None when it cannot be read (the connection is then kept: the
        machine lock still guards our own instances).
        """
        if not self._ws_url:
            return None
        try:
            value = await self._evaluate_once(
                self._ws_url, 'process.pid', False, 10.0
            )
            return int(value)
        except Exception:  # noqa: BLE001 — a failed probe is not fatal
            return None

    async def _discover_ws(self) -> str | None:
        url = f'http://{self._host}:{self._port}/json'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    data = await r.json()
            for target in data:
                ws = target.get('webSocketDebuggerUrl')
                if ws:
                    return ws
        except Exception:
            return None
        return None

    async def evaluate(
        self,
        expr: str,
        *,
        await_promise: bool = False,
        timeout: float = 25.0,
    ):
        """Evaluate ``expr`` in the inspected main process.

        Each call opens a fresh WebSocket to the inspector's CURRENT
        target. A window reload (e.g. a login that re-inits the app)
        makes the inspector hand out a NEW target id, so the remembered
        URL starts answering the handshake with 400 and live sockets
        close mid-call (both seen live driving a vendor login). On
        either, re-discover the target once and retry the call.
        """
        if not self._ws_url:
            raise InspectorError('inspector not open')
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                return await self._evaluate_once(
                    self._ws_url, expr, await_promise, timeout
                )
            except _StaleInspectorTarget as e:
                last_err = e
                if attempt == 2:
                    break
                fresh = await self._discover_ws()
                if not fresh or fresh == self._ws_url:
                    break  # nothing better to retry against
                self._ws_url = fresh
        raise InspectorError(f'inspector target went away mid-call: {last_err}')

    async def _evaluate_once(
        self, ws_url: str, expr: str, await_promise: bool, timeout: float
    ):
        # Hard-bound the whole call: on some hosts an unbound loopback
        # port HANGS instead of refusing (measured on WSL mirrored
        # networking), and an aiohttp default timeout is minutes — a
        # stuck eval must not hold the machine lock hostage.
        async def _run():
            async with aiohttp.ClientSession() as s:
                try:
                    async with s.ws_connect(ws_url, max_msg_size=0) as ws:
                        return await self._call_on(
                            ws, expr, await_promise, timeout
                        )
                except aiohttp.WSServerHandshakeError as e:
                    # The remembered target id is gone (window reload).
                    raise _StaleInspectorTarget(str(e.status)) from e

        return await asyncio.wait_for(_run(), timeout=timeout + 10.0)

    async def _call_on(
        self, ws, expr: str, await_promise: bool, timeout: float
    ):
        async def call(mid, method, params):
            await ws.send_json({
                'id': mid,
                'method': method,
                'params': params,
            })
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get('id') == mid:
                        return data
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    continue  # not ours; wait for the TEXT response
                else:  # CLOSED / CLOSING / ERROR mid-call
                    raise _StaleInspectorTarget(str(msg.type))

        await call(1, 'Runtime.enable', {})
        r = await call(
            2,
            'Runtime.evaluate',
            {
                'expression': expr,
                'returnByValue': True,
                'awaitPromise': await_promise,
                'includeCommandLineAPI': True,
            },
        )
        res = r.get('result', {})
        if 'exceptionDetails' in res:
            raise InspectorError(json.dumps(res['exceptionDetails'])[:400])
        return res.get('result', {}).get('value')

    async def eval_in_renderer(self, js: str, *, timeout: float = 25.0):
        """Run ``js`` in the first BrowserWindow's renderer DOM."""
        expr = (
            "require('electron').BrowserWindow.getAllWindows()[0]"
            '.webContents.executeJavaScript(' + json.dumps(js) + ')'
        )
        return await self.evaluate(expr, await_promise=True, timeout=timeout)

    async def close(self) -> None:
        """Close the inspector session and release the machine lock.

        Only ever closes a session THIS object opened (``open``
        verified the pid); a busy-refused session never reaches here
        with a socket attached. The short timeout matters: close runs
        under the machine lock, and on a dead endpoint (the socket
        already gone) the call must not hold the lock for the full
        evaluate budget.
        """
        try:
            await self.evaluate("require('inspector').close()", timeout=5.0)
        except Exception:
            pass
        self._ws_url = None
        self._release_lock()
