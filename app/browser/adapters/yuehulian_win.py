"""Windows I/O for the Yuehulian client (process + Node inspector).

Thin wrappers over ``powershell.exe`` and the inspector WebSocket; the
pure logic lives in :mod:`yuehulian_dom`. Everything here only works on
Windows (or WSL-on-Windows, driving the Windows side over 127.0.0.1).

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
import subprocess

import aiohttp

logger = logging.getLogger(__name__)

# The installed Electron binary sits directly in the install dir; the
# bundled kernel is under resources\chrome\...\chrome.exe. We separate
# the two by name (the kernel is chrome.exe) and path (resources).
_PS = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
_UTF8 = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'
_DEFAULT_DIR = r'C:\Program Files (x86)\YueHuLian'


def _run_ps(script: str, timeout: float = 30.0) -> str:
    """Run a PowerShell script, return stdout (UTF-8), '' on failure."""
    try:
        proc = subprocess.run(
            [*_PS, _UTF8 + script],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning('powershell call failed: %s', e)
        return ''
    return (proc.stdout or '').strip()


def win_path_to_wsl(win_path: str) -> str:
    """``C:\\Users\\x`` → ``/mnt/c/Users/x`` (best-effort, drive letters)."""
    p = win_path.strip().strip('"').replace('\\', '/')
    if len(p) >= 2 and p[1] == ':':
        return f'/mnt/{p[0].lower()}{p[2:]}'
    return p


class ProcessLayer:
    """Locate, launch and inspect the Yuehulian client process tree."""

    def __init__(self, install_dir: str | None = None):
        self._dir = install_dir

    def locate_install_dir(self) -> str | None:
        if self._dir:
            return self._dir
        script = (
            '$dir=$null;'
            "$keys='HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\"
            "CurrentVersion\\Uninstall\\*','HKLM:\\SOFTWARE\\Microsoft\\"
            "Windows\\CurrentVersion\\Uninstall\\*';"
            'foreach($e in (Get-ItemProperty $keys -ErrorAction '
            'SilentlyContinue | Where-Object { $_.DisplayIcon -match '
            "'YueHuLian' -or $_.InstallLocation -match 'YueHuLian' })){"
            "if($e.InstallLocation){$dir=$e.InstallLocation.TrimEnd('\\')}"
            'elseif($e.DisplayIcon){$dir=Split-Path ($e.DisplayIcon '
            "-replace ',\\d+$','')};if($dir){break}}"
            "if(-not $dir -and (Test-Path '" + _DEFAULT_DIR + "'))"
            "{$dir='" + _DEFAULT_DIR + "'};"
            'if($dir){$dir}'
        )
        out = _run_ps(script).splitlines()
        self._dir = out[0].strip() if out and out[0].strip() else None
        return self._dir

    def is_running(self) -> bool:
        script = (
            '(Get-CimInstance Win32_Process | Where-Object '
            "{ $_.ExecutablePath -match 'YueHuLian' -and "
            "$_.Name -ne 'chrome.exe' } | Measure-Object).Count"
        )
        out = _run_ps(script)
        try:
            return int(out.strip() or '0') > 0
        except ValueError:
            return False

    def main_pid(self) -> int | None:
        """The Electron MAIN process pid (not a kernel chrome.exe)."""
        script = (
            'Get-CimInstance Win32_Process | Where-Object '
            "{ $_.ExecutablePath -match 'YueHuLian' -and "
            "$_.Name -ne 'chrome.exe' -and "
            "$_.ExecutablePath -notmatch 'resources' -and "
            "$_.CommandLine -notmatch '--type=' } | "
            'Select-Object -ExpandProperty ProcessId -First 1'
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
        """``--user-data-dir`` of every running Yuehulian kernel."""
        script = (
            'Get-CimInstance Win32_Process | Where-Object '
            "{ $_.Name -eq 'chrome.exe' -and "
            "$_.CommandLine -match 'YueHuLianData' } | "
            'ForEach-Object { if($_.CommandLine -match '
            '\'--user-data-dir="?([^" ]+)\'){$matches[1]} } | '
            'Sort-Object -Unique'
        )
        out = _run_ps(script)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def read_devtools_port(self, user_data_dir: str) -> int | None:
        """First line of ``<user_data_dir>/DevToolsActivePort`` (the port)."""
        path = (
            win_path_to_wsl(user_data_dir).rstrip('/') + '/DevToolsActivePort'
        )
        try:
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
