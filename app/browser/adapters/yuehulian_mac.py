"""macOS I/O for the Yuehulian client (process + Node inspector).

The macOS sibling of :mod:`yuehulian_win`: the same duck-typed
``ProcessLayer`` interface the client drives on Windows, implemented
with the shared Mac app locator (:mod:`app.browser.mac_app_locator`)
and psutil instead of PowerShell, plus the Mac inspector
(:mod:`app.browser.mac_inspector`, SIGUSR1) with yuehulian's spawn-hook
helpers. The DOM logic (:mod:`yuehulian_dom`) is shared unchanged —
the Mac client is the same Vue/Element-UI app.

Validated live 2026-11-27 against the real Mac client (v1.11.13):
``open -a`` launches the client; the MAIN process is the one whose exe
is ``Contents/MacOS/<CFBundleExecutable>`` with no ``--type=`` arg;
``kill -USR1`` opens the inspector on 9229; the kernel is spawned
through ``child_process.spawn`` as
``…/YueHuLian-Resources/chrome/<ver>/Chromium.app/Contents/MacOS/
Chromium`` with ``--user-data-dir=…/YueHuLianData/<env-id>`` (spaces in
the path, no debug port, ``--use-mock-keychain``); ``DevToolsActivePort``
lands in the user-data-dir; the client ignores TERM/AppleScript quit
(only its own 安全退出 or SIGKILL stops it).
"""

import json
import logging
import os

import psutil

from app.browser import mac_app_locator as mac_locator
from app.browser.adapters.yuehulian_win import (
    SPAWN_HOOK_JS,
    SPAWN_UNHOOK_JS,
)
from app.browser.mac_app_locator import (
    LocatedMacApp,
    MacAppProcess,
    MacAppSpec,
)
from app.browser.mac_inspector import (
    MacInspectorSession as _MacInspectorBase,
)

__all__ = [
    'MAC_SPAWN_HOOK_JS',
    'MacInspectorSession',
    'MacProcessLayer',
    'YUEHULIAN_MAC_APP',
]

logger = logging.getLogger(__name__)

YUEHULIAN_MAC_APP = MacAppSpec(
    key='yuehulian',
    display_name='悦互联 (YueHuLian)',
    bundle_ids=('com.chinafic.yuehulian',),
    app_names=('悦互联.app',),
)

# The bundled kernel the client spawns on macOS (FACTS §2): under the
# app's own resources dir, named Chromium. Matched against the spawn
# file — the MAIN exe lives inside 悦互联.app, kernels under
# YueHuLian-Resources, so this never touches the client itself.
_WIN_KERNEL_JS_RE = r'chrome\.exe'
_MAC_KERNEL_JS_RE = r'Chromium\.app\/Contents\/MacOS\/Chromium'

# Spawn hook: DERIVED from the Windows SPAWN_HOOK_JS by swapping only
# the kernel regex, so any future fix to the hook logic reaches both
# platforms (the Windows text stays byte-identical in its own module;
# a test pins it AND that the swap happened exactly once). Same
# behaviour: a kernel launch with no debug port gets
# --remote-debugging-port=0 and its user-data-dir recorded.
MAC_SPAWN_HOOK_JS = SPAWN_HOOK_JS.replace(_WIN_KERNEL_JS_RE, _MAC_KERNEL_JS_RE)


def _kernel_processes() -> list[MacAppProcess]:
    """Every running YueHuLian kernel process (main and helpers).

    Matched on the exe path sitting under the client's extracted
    ``YueHuLian-Resources`` dir — the vendor ships its kernel there and
    nowhere else, so no other app's Chromium matches. Blocking.
    """
    out = []
    for p in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            exe = p.info['exe'] or ''
            if 'YueHuLian-Resources' not in exe:
                continue
            cmdline = [str(a) for a in (p.info['cmdline'] or [])]
            out.append(MacAppProcess(p.info['pid'], exe, tuple(cmdline)))
        except (psutil.Error, OSError):
            continue
    return out


def _udd_of(args: tuple[str, ...]) -> str:
    """The ``--user-data-dir`` value in a kernel's command line ('')."""
    for a in args:
        if a.startswith('--user-data-dir='):
            return a[len('--user-data-dir=') :]
    return ''


class MacProcessLayer:
    """Locate, launch and inspect the Yuehulian client process tree."""

    def __init__(self, client_path: str | None = None):
        self._client_path = client_path
        self._located: LocatedMacApp | None = None
        #: Why discovery failed (surfaced in ClientNotRunning errors).
        self.locate_error: str | None = None

    def locate_install_dir(self) -> str | None:
        """The ``.app`` bundle path, via the shared Mac app locator.

        A caller-saved path (the ``client_path`` setting) is tried
        first; otherwise the running client, Spotlight by bundle id,
        then the conventional Applications folders.
        """
        if self._located:
            return self._located.app_path
        located = mac_locator.locate(
            YUEHULIAN_MAC_APP, saved_path=self._client_path
        )
        if located:
            self._located = located
            return located.app_path
        self.locate_error = mac_locator.not_found_message(YUEHULIAN_MAC_APP)
        return None

    def _app_processes(self) -> list[MacAppProcess]:
        if not self.locate_install_dir():
            return []
        return mac_locator.app_processes(self._located)

    def is_running(self) -> bool:
        return bool(self._app_processes())

    def main_pid(self) -> int | None:
        """The Electron MAIN process pid (not a helper, not a kernel).

        The client's own processes all live inside the ``.app`` bundle;
        the MAIN one is the exe matching ``CFBundleExecutable`` with no
        ``--type=`` argument (helpers carry ``--type=utility|renderer|
        gpu-process``; kernels are outside the bundle entirely).
        """
        located = self._located
        for proc in self._app_processes():
            # startswith, not membership: an arg is '--type=renderer',
            # never exactly '--type='.
            if any(a.startswith('--type=') for a in proc.args):
                continue
            if located and proc.exe != located.exe_path:
                continue
            return proc.pid
        return None

    def launch(self) -> bool:
        app = self.locate_install_dir()
        if not app:
            return False
        try:
            mac_locator.launch(self._located)
        except RuntimeError as e:
            self.locate_error = str(e)
            logger.warning('could not launch the client: %s', e)
            return False
        return True

    def kernel_user_data_dirs(self) -> list[str]:
        """``--user-data-dir`` of every running YueHuLian kernel."""
        return sorted({
            udd for udd in (_udd_of(p.args) for p in _kernel_processes()) if udd
        })

    def terminate_kernels(self, user_data_dir: str) -> list[int]:
        """Kill leftover kernels bound to one env's user-data-dir.

        A client killed with SIGKILL leaves its kernel processes behind
        (re-parented to launchd), still holding the profile's singleton
        lock — the client's own batch close cannot reach them, and a
        fresh open of that env would find no debug port. Only 悦互联
        kernels whose ``--user-data-dir`` is EXACTLY this env's dir are
        terminated, by pid. Blocking; returns the pids killed.
        """
        victims = [
            p.pid
            for p in _kernel_processes()
            if _udd_of(p.args) == user_data_dir
        ]
        for pid in victims:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
            except (psutil.Error, OSError) as e:
                logger.debug('could not terminate kernel %s: %s', pid, e)
        if victims:
            logger.info(
                'terminated leftover kernel pid(s) %s for %r',
                victims,
                user_data_dir,
            )
        return victims

    def read_devtools_port(
        self, user_data_dir: str, newer_than: float | None = None
    ) -> int | None:
        """First line of ``<user-data-dir>/DevToolsActivePort``.

        A kernel launched earlier (e.g. a prior injected session) leaves
        a stale file; ``newer_than`` (a unix ts) rejects a file not
        rewritten since, so open_store only accepts the port its own
        launch wrote. Mac paths are read as-is (no drive translation).
        """
        path = os.path.join(
            os.path.normpath(user_data_dir.strip().strip('"')),
            'DevToolsActivePort',
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


class MacInspectorSession(_MacInspectorBase):
    """Yuehulian's Mac inspector: SIGUSR1 trigger + the spawn hook.

    Kept constructor-compatible with the Windows
    ``InspectorSession(proc)`` the client builds, so the platform pick
    is one line. The spawn-hook helpers are yuehulian-specific (the Mac
    kernel match); the unhook text is shared with the Windows layer.
    """

    def __init__(self, proc: MacProcessLayer, **kw):
        super().__init__(**kw)
        self._proc = proc

    async def install_spawn_hook(self) -> None:
        await self.evaluate(MAC_SPAWN_HOOK_JS)

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
