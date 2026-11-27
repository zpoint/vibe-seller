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

Validated live on real clients (2026-11): use a CURRENT main pid — a
stale pid fails with an error.
"""

import asyncio
from collections.abc import Callable
import json
import logging

import aiohttp

from app.browser.win_app_locator import run_powershell as _run_inspector_ps

logger = logging.getLogger(__name__)


class InspectorError(RuntimeError):
    """The Node inspector could not be opened or driven."""


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
    """

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

    async def open(self, main_pid: int) -> None:
        script = (
            self._exe_path_script() + "$env:ELECTRON_RUN_AS_NODE='1';"
            '$env:NODE_OPTIONS=$null;'
            'Start-Process -FilePath $exe -ArgumentList '
            f"'-e',('process._debugProcess({main_pid})') -NoNewWindow;'ok'"
        )
        await asyncio.to_thread(_run_inspector_ps, script)
        for _ in range(12):
            url = await self._discover_ws()
            if url:
                self._ws_url = url
                return
            await asyncio.sleep(0.5)
        raise InspectorError('inspector did not open on 127.0.0.1:9229')

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
        async with aiohttp.ClientSession() as s:
            try:
                async with s.ws_connect(ws_url, max_msg_size=0) as ws:
                    return await self._call_on(ws, expr, await_promise, timeout)
            except aiohttp.WSServerHandshakeError as e:
                # The remembered target id is gone (window reload).
                raise _StaleInspectorTarget(str(e.status)) from e

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
        try:
            await self.evaluate("require('inspector').close()")
        except Exception:
            pass
        self._ws_url = None
