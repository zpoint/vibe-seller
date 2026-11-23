"""Windows I/O for the Yuehulian client (process + Node inspector).

Thin wrappers over ``powershell.exe`` and the inspector WebSocket; the
pure logic lives in :mod:`yuehulian_dom`, and install discovery in
:mod:`app.browser.win_app_locator` (shared with Ziniao). Everything
here only works on Windows (or WSL-on-Windows, driving the Windows side
over 127.0.0.1).

Validated live 2026-11-18: install location via registry/Start Menu,
Electron MAIN pid (``用户端.exe`` with no ``--type=`` — the kernel's root
``chrome.exe`` also has no ``--type=``, so it must be excluded), opening
the inspector with ``ELECTRON_RUN_AS_NODE`` + ``process._debugProcess``,
``Runtime.evaluate`` incl. ``webContents.executeJavaScript``, and closing
it with ``require('inspector').close()``.
"""

import asyncio
import json
import logging
import ntpath
import os

import aiohttp

from app.browser import win_app_locator as win_locator
from app.browser.win_app_locator import (
    run_powershell as _run_ps,
    win_path_to_local,
)

logger = logging.getLogger(__name__)

# The installed Electron binary sits directly in the install dir; the
# bundled kernel is under resources\chrome\...\chrome.exe. We separate
# the two by name (the kernel is chrome.exe) and path (resources).
_DEFAULT_DIR = r'C:\Program Files (x86)\YueHuLian'

# win_path_to_local / win_path_to_wsl (Windows path → where THIS process
# reads it: native Windows vs WSL /mnt) live in win_app_locator now.


def _where_ps(*conditions: str) -> str:
    """A complete ``Where-Object { … }`` clause from bare conditions.

    The braces live here only: a caller passes conditions and cannot
    leave the block unbalanced.
    """
    return 'Where-Object { ' + ' -and '.join(conditions) + ' }'


class ProcessLayer:
    """Locate, launch and inspect the Yuehulian client process tree."""

    def __init__(self, install_dir: str | None = None):
        self._dir = install_dir
        #: Why discovery failed (surfaced in ClientNotRunning errors).
        self.locate_error: str | None = None

    def locate_install_dir(self) -> str | None:
        """The install dir, via the shared Windows app locator.

        A caller-configured dir is trusted as-is; otherwise the locator
        tries the running client (observed before it was stopped),
        registry App Paths + Uninstall, Desktop/Start-Menu shortcuts,
        then bounded conventional dirs on every fixed drive — all with
        UTF-8 PowerShell output, so Chinese install paths survive.
        """
        if self._dir:
            return self._dir
        located = win_locator.locate(win_locator.YUEHULIAN_SPEC)
        if located:
            self._dir = ntpath.dirname(located.exe_path)
            return self._dir
        self.locate_error = win_locator.not_found_message(
            win_locator.YUEHULIAN_SPEC
        )
        return None

    def _path_match_ps(self) -> str:
        """PS condition matching any process of THIS install.

        A custom install dir (e.g. ``D:\\悦互联``) carries no
        'YueHuLian' in its path, so the located dir is matched as a
        case-insensitive path prefix (single quotes escaped for
        PowerShell). The legacy name match stays as the second arm so a
        not-yet-located state still works; ``locate_install_dir`` caches
        its result, so polling callers do not re-run discovery.
        """
        conds = ["$_.ExecutablePath -match 'YueHuLian'"]
        d = self.locate_install_dir()
        d = d.rstrip('\\') if d else ''
        # A bare drive root ('D:') would match every process on that
        # drive — only a real install dir becomes a prefix.
        if len(d) > 2:
            esc = d.replace("'", "''")
            conds.insert(
                0,
                '($_.ExecutablePath -and $_.ExecutablePath.StartsWith('
                f"'{esc}\\', [StringComparison]::OrdinalIgnoreCase))",
            )
        return '(' + ' -or '.join(conds) + ')'

    def is_running(self) -> bool:
        script = (
            '(Get-CimInstance Win32_Process | '
            + _where_ps(self._path_match_ps(), "$_.Name -ne 'chrome.exe'")
            + ' | Measure-Object).Count'
        )
        out = _run_ps(script)
        try:
            return int(out.strip() or '0') > 0
        except ValueError:
            return False

    def main_pid(self) -> int | None:
        """The Electron MAIN process pid (not a kernel chrome.exe)."""
        script = (
            'Get-CimInstance Win32_Process | '
            + _where_ps(
                self._path_match_ps(),
                "$_.Name -ne 'chrome.exe'",
                "$_.ExecutablePath -notmatch 'resources'",
                "$_.CommandLine -notmatch '--type='",
            )
            + ' | Select-Object -ExpandProperty ProcessId -First 1'
        )
        out = _run_ps(script).strip()
        try:
            return int(out) if out else None
        except ValueError:
            return None

    def launch(self) -> bool:
        d = self.locate_install_dir()
        if not d:
            return False
        script = (
            f"$exe=Get-ChildItem (Join-Path '{d}' '*.exe') | "
            'Where-Object {$_.Length -gt 1MB} | '
            'Sort-Object Length -Descending | Select-Object -First 1;'
            "if($exe){Start-Process -FilePath $exe.FullName;'ok'}"
            "else{'no-exe'}"
        )
        return _run_ps(script).strip().endswith('ok')

    def kernel_user_data_dirs(self) -> list[str]:
        """``--user-data-dir`` of every running Yuehulian kernel.

        Matches the ``YueHuLianData`` NAME in the kernel's command
        line — the client builds that data dir under its own fixed
        name wherever it is installed, so a custom install dir does
        not change this match (unlike the exe-path matches above).
        """
        script = (
            'Get-CimInstance Win32_Process | '
            + _where_ps(
                "$_.Name -eq 'chrome.exe'",
                "$_.CommandLine -match 'YueHuLianData'",
            )
            + ' | ForEach-Object { if($_.CommandLine -match '
            '\'--user-data-dir="?([^" ]+)\'){$matches[1]} } | '
            'Sort-Object -Unique'
        )
        out = _run_ps(script)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def read_devtools_port(
        self, user_data_dir: str, newer_than: float | None = None
    ) -> int | None:
        """First line of ``<user_data_dir>/DevToolsActivePort`` (the port).

        A kernel launched earlier (e.g. a prior injected session) leaves a
        stale file; pass ``newer_than`` (a unix ts) to reject a file not
        rewritten since, so open_store only accepts the port its own
        launch wrote.
        """
        path = os.path.join(
            win_path_to_local(user_data_dir), 'DevToolsActivePort'
        )
        try:
            if newer_than is not None:
                if os.path.getmtime(path) < newer_than:
                    return None
            with open(path, encoding='utf-8') as f:
                first = f.readline().strip()
            return int(first) if first.isdigit() else None
        except (OSError, ValueError):
            return None

    def exe_path_script(self) -> str:
        """PS fragment resolving the install's main exe to ``$exe``."""
        d = self.locate_install_dir() or _DEFAULT_DIR
        return (
            f"$exe=(Get-ChildItem (Join-Path '{d}' '*.exe') | "
            'Where-Object {$_.Length -gt 1MB} | '
            'Sort-Object Length -Descending | '
            'Select-Object -First 1).FullName;'
        )


# Spawn hook: wrap child_process spawn so a kernel launch with no debug
# port gets --remote-debugging-port=0, recording the kernel's
# user-data-dir so the caller can read the right DevToolsActivePort. Saves
# the original first and restores it on uninstall (never `delete` — that
# removes the client's native spawn).
SPAWN_HOOK_JS = r"""
(function(){
  var cp = require('child_process');
  if (cp.__vsHooked) return 'already';
  var proto = cp.ChildProcess.prototype;
  Object.defineProperty(proto, '__vsOrigSpawn',
    {value: proto.spawn, configurable: true});
  global.__vsSpawns = [];
  proto.spawn = function(options){
    try {
      var args = (options && options.args) || [];
      var file = (options && options.file) || args[0] || '';
      if (/chrome\.exe/i.test(file) &&
          !args.some(function(a){return /--remote-debugging-port/.test(a);})){
        args.push('--remote-debugging-port=0');
        var udd='';
        args.forEach(function(a){
          var m=/--user-data-dir=(.*)/.exec(a); if(m) udd=m[1];
        });
        global.__vsSpawns.push({udd: udd, ts: Date.now()});
      }
    } catch(e){}
    return proto.__vsOrigSpawn.apply(this, arguments);
  };
  cp.__vsHooked = true;
  return 'hooked';
})()
"""

SPAWN_UNHOOK_JS = r"""
(function(){
  var cp = require('child_process');
  var proto = cp.ChildProcess.prototype;
  if (proto.__vsOrigSpawn) { proto.spawn = proto.__vsOrigSpawn; }
  cp.__vsHooked = false;
  return 'unhooked';
})()
"""


class InspectorError(RuntimeError):
    """The Node inspector could not be opened or driven."""


class InspectorSession:
    """Open/drive/close the Electron main-process Node inspector."""

    def __init__(
        self,
        proc: ProcessLayer,
        host: str = '127.0.0.1',
        port: int = 9229,
    ):
        self._proc = proc
        self._host = host
        self._port = port
        self._ws_url: str | None = None

    async def open(self, main_pid: int) -> None:
        script = (
            self._proc.exe_path_script() + "$env:ELECTRON_RUN_AS_NODE='1';"
            '$env:NODE_OPTIONS=$null;'
            'Start-Process -FilePath $exe -ArgumentList '
            f"'-e',('process._debugProcess({main_pid})') -NoNewWindow;'ok'"
        )
        await asyncio.to_thread(_run_ps, script)
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
        if not self._ws_url:
            raise InspectorError('inspector not open')
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(self._ws_url, max_msg_size=0) as ws:

                async def call(mid, method, params):
                    await ws.send_json({
                        'id': mid,
                        'method': method,
                        'params': params,
                    })
                    while True:
                        msg = await asyncio.wait_for(
                            ws.receive_json(), timeout=timeout
                        )
                        if msg.get('id') == mid:
                            return msg

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

    async def install_spawn_hook(self) -> None:
        await self.evaluate(SPAWN_HOOK_JS)

    async def uninstall_spawn_hook(self) -> None:
        try:
            await self.evaluate(SPAWN_UNHOOK_JS)
        except Exception as e:
            logger.warning('spawn unhook failed: %s', e)

    async def recorded_spawns(self) -> list[dict]:
        val = await self.evaluate('JSON.stringify(global.__vsSpawns||[])')
        try:
            return json.loads(val) if val else []
        except (ValueError, TypeError):
            return []

    async def close(self) -> None:
        try:
            await self.evaluate("require('inspector').close()")
        except Exception:
            pass
        self._ws_url = None
