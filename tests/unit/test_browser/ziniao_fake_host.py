"""A fake Windows host with (or without) a Ziniao client on it.

Stands in for everything ``ensure_ziniao_running`` touches on the machine
— the process table, killing, launching, the client's HTTP API and the
clock — at the OS/library boundary (``psutil``, ``subprocess``,
``httpx``, ``asyncio.sleep``), not at our own functions. That keeps one
scenario meaningful against both this code and the code it replaced
(which read ``tasklist`` and killed with ``taskkill``), so a negative
control on the parent commit fails on behaviour, not on a missing name.

Processes are modelled on a real Windows install (one store open):
``ziniao.exe`` (main + Electron helpers), ``ziniao-gateway.exe`` and
``env-kit.exe`` in the install dir, ``ziniaobrowser.exe`` kernels and a
``node.exe`` under ``%APPDATA%\\ziniaobrowser\\env-kit``.

The API follows Ziniao's own contract that a request may need up to
120 s: a client can need ``first_answer_s`` of *uninterrupted* waiting on
its first request after launch (it signs in) — a request cut short by
its read timeout starts that over.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import threading
from unittest import mock

import httpx
import psutil

INSTALL = 'C:\\Program Files\\ziniao'
MAIN_EXE = INSTALL + '\\ziniao.exe'
APPDATA_KIT = 'C:\\Users\\u\\AppData\\Roaming\\ziniaobrowser\\env-kit'
KERNEL_EXE = APPDATA_KIT + '\\Core\\chrome_64_142\\ziniaobrowser.exe'
NODE_EXE = APPDATA_KIT + '\\agentic\\node.exe'
UPDATER_EXE = (
    'C:\\Users\\u\\AppData\\Local\\ziniaobrowser-updater\\pending\\'
    'ziniao_x64_6.26.6.26_production.exe'
)
PORT = 16851
USER = {'company': 'acme', 'username': 'ops', 'password': 'pw'}

NORMAL_MODE = [
    ('ziniao.exe', MAIN_EXE),
    ('ziniao.exe', MAIN_EXE),
    ('ziniao.exe', MAIN_EXE),
    (
        'env-kit.exe',
        INSTALL + '\\resources\\app.asar.unpacked\\envkit\\env-kit.exe',
    ),
]
LEFTOVERS = [
    ('ziniaobrowser.exe', KERNEL_EXE),
    ('ziniaobrowser.exe', KERNEL_EXE),
    ('node.exe', NODE_EXE),
]
BYSTANDERS = [
    (
        'python.exe',
        'C:\\work\\vibe-seller-wt-ziniao\\.venv\\Scripts\\python.exe',
    ),
    ('node.exe', 'C:\\Program Files\\nodejs\\node.exe'),
    ('chrome.exe', 'C:\\Program Files\\Google\\Chrome\\chrome.exe'),
]


class FakeZiniaoHost:
    def __init__(
        self,
        api: str = 'down',
        procs: list[tuple[str, str]] = (),
        *,
        ready_after: float = 3.0,
        first_answer_s: float = 0.0,
        dead_launches: int = 0,
        unkillable: tuple[str, ...] = (),
        port_owner: int | None = None,
    ):
        self.clock = 1000.0
        self.api = api  # down | webdriver | hung
        self.procs: dict[int, tuple[str, str]] = {}
        self._next_pid = 100
        for name, exe in list(procs) + BYSTANDERS:
            self.spawn(name, exe)
        self.ready_after = ready_after
        self.first_answer_s = first_answer_s
        self.dead_launches = dead_launches
        self.unkillable = set(unkillable)
        self.port_owner = port_owner
        self.launched_at: float | None = None
        self.answered_once = False
        self.launches: list[float] = []
        self.killed: list[tuple[str, str]] = []
        self.requests: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        # Hold a launch in its worker thread until set (real time).
        self.block: threading.Event | None = None

    # -- process table ------------------------------------------------
    def spawn(self, name: str, exe: str) -> int:
        self._next_pid += 1
        self.procs[self._next_pid] = (name, exe)
        return self._next_pid

    def names(self) -> list[str]:
        return sorted(n for n, _ in self.procs.values())

    def ziniao_left(self) -> list[str]:
        bystanders = {exe for _, exe in BYSTANDERS}
        return sorted(n for n, e in self.procs.values() if e not in bystanders)

    def _main_alive(self) -> bool:
        return any(n.lower() == 'ziniao.exe' for n, _ in self.procs.values())

    def kill_pid(self, pid: int) -> None:
        if pid not in self.procs:
            return
        name, exe = self.procs[pid]
        if name in self.unkillable:
            raise psutil.AccessDenied(pid)
        del self.procs[pid]
        self.killed.append((name, exe))
        if not self._main_alive():
            self.api = 'down'
            self.launched_at = None

    # -- launching ----------------------------------------------------
    def launch(self, cmd: list[str]) -> None:
        assert '--run_type=web_driver' in cmd, cmd
        assert f'--port={PORT}' in cmd, cmd
        self.launches.append(self.clock)
        self.spawn('ziniao.exe', MAIN_EXE)
        self.spawn('ziniao.exe', MAIN_EXE)
        self.spawn('ziniao-gateway.exe', INSTALL + '\\ziniao-gateway.exe')
        if self.dead_launches > 0:
            self.dead_launches -= 1
            self.api = 'down'  # this launch never binds its port
            return
        self.api = 'webdriver'
        self.launched_at = self.clock
        self.answered_once = False

    # -- the HTTP API -------------------------------------------------
    async def handle(self, request: httpx.Request) -> httpx.Response:
        read = request.extensions.get('timeout', {}).get('read')
        self.requests.append({'at': self.clock, 'read': read})
        bound = (
            self.api == 'webdriver'
            and self.launched_at is not None
            and self.clock >= self.launched_at + self.ready_after
        ) or (self.api == 'webdriver' and self.launched_at is None)
        if self.api == 'hung':
            self.clock += read
            raise httpx.ReadTimeout('timed out', request=request)
        if not bound:
            raise httpx.ConnectError('refused', request=request)
        need = 0.05 if self.answered_once else self.first_answer_s
        if read is not None and need > read:
            self.clock += read
            raise httpx.ReadTimeout('timed out', request=request)
        self.clock += need
        self.answered_once = True
        body = {'statusCode': 0, 'browserList': [{'browserName': 'A'}]}
        return httpx.Response(200, content=json.dumps(body).encode())


def _maybe(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


@contextlib.contextmanager
def installed(host: FakeZiniaoHost, *, wsl: bool = False):
    """Patch the machine boundary so ``host`` is what the code sees."""
    real_sleep = asyncio.sleep
    real_client = httpx.AsyncClient

    async def fake_sleep(delay, *a, **k):
        host.clock += float(delay or 0)
        await real_sleep(0)

    def fake_client(*a, **kw):
        kw['transport'] = httpx.MockTransport(host.handle)
        return real_client(*a, **kw)

    class FakeProc:
        def __init__(self, pid=None):
            self.pid = pid if pid is not None else 1
            if pid is not None and pid not in host.procs:
                raise psutil.NoSuchProcess(pid)

        def parents(self):
            return []

        def kill(self):
            host.kill_pid(self.pid)

        terminate = kill

        def cmdline(self):
            return []

    def process_iter(attrs=None):
        for pid, (name, exe) in list(host.procs.items()):
            p = mock.MagicMock()
            p.info = {'pid': pid, 'name': name, 'exe': exe}
            yield p

    def net_connections(kind='tcp'):
        if host.port_owner is None:
            return []
        c = mock.MagicMock()
        c.status = psutil.CONN_LISTEN
        c.laddr = mock.MagicMock(port=PORT)
        c.pid = host.port_owner
        return [c]

    def fake_run(cmd, *a, **kw):
        joined = ' '.join(map(str, cmd))
        out = ''
        if 'tasklist' in joined:  # the replaced code's process check
            if host._main_alive():
                out = 'ziniao.exe   1234 Console   1   100,000 K\n'
        elif 'taskkill' in joined and '/IM' in cmd:  # replaced force-kill
            for pid, (name, _) in list(host.procs.items()):
                if name.lower() == cmd[cmd.index('/IM') + 1].lower():
                    with contextlib.suppress(psutil.AccessDenied):
                        host.kill_pid(pid)
        elif 'taskkill' in joined:  # WSL kill by pid
            for i, arg in enumerate(cmd):
                if arg == '/PID':
                    with contextlib.suppress(psutil.AccessDenied):
                        host.kill_pid(int(cmd[i + 1]))
        elif 'Get-NetTCPConnection' in joined:
            listening = host.api in ('webdriver', 'hung') or host.port_owner
            out = '1' if listening else '0'
        elif 'Win32_Process' in joined:
            out = json.dumps([
                {'ProcessId': pid, 'Name': n, 'ExecutablePath': e}
                for pid, (n, e) in host.procs.items()
            ])
        else:
            raise AssertionError(f'unexpected subprocess: {joined}')
        res = mock.MagicMock(returncode=0, stderr=b'')
        res.stdout = out.encode() if not kw.get('text') else out
        return res

    def fake_popen(cmd, *a, **kw):
        if host.block is not None:
            assert host.block.wait(10), 'launch held too long'
        host.launch(list(cmd))
        return mock.MagicMock(pid=4242)

    def fake_send(event, props=None):
        host.events.append((event, dict(props or {})))

    patches = [
        mock.patch('asyncio.sleep', fake_sleep),
        mock.patch('httpx.AsyncClient', fake_client),
        mock.patch('psutil.process_iter', process_iter),
        mock.patch('psutil.Process', FakeProc),
        mock.patch('psutil.net_connections', net_connections),
        mock.patch('subprocess.run', fake_run),
        mock.patch('subprocess.Popen', fake_popen),
        mock.patch('app.telemetry.send', fake_send),
        mock.patch('app.browser.ziniao_utils.IS_WINDOWS', not wsl),
        mock.patch('app.browser.ziniao_utils.IS_MAC', False),
        mock.patch('app.browser.ziniao_utils.IS_LINUX', wsl),
        mock.patch('app.browser.ziniao_utils.is_wsl', lambda: wsl),
        mock.patch('app.browser.ziniao_utils.ziniao_host', lambda: '127.0.0.1'),
    ]
    if wsl:
        # The replaced code checked the interop paths before shelling out.
        patches.append(mock.patch('os.path.isfile', lambda p: True))
    startup = _maybe('app.browser.ziniao_startup')
    if startup is not None:
        patches += [
            mock.patch.object(startup, '_clock', lambda: host.clock),
            mock.patch.object(startup, 'is_wsl', lambda: wsl),
            mock.patch.object(startup, 'ziniao_host', lambda: '127.0.0.1'),
            mock.patch.object(startup, '_state', None),
        ]
    procs_mod = _maybe('app.browser.ziniao_processes')
    if procs_mod is not None:
        patches += [
            mock.patch.object(procs_mod, 'IS_WINDOWS', not wsl),
            mock.patch.object(procs_mod, '_KILL_WAIT_S', 0.3),
        ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield host


def client_path() -> str:
    return MAIN_EXE


async def ensure(**kw) -> None:
    """The public entry, as every caller imports it."""
    from app.browser import ziniao_utils  # noqa: PLC0415

    await ziniao_utils.ensure_ziniao_running(PORT, MAIN_EXE, dict(USER), **kw)
