"""macOS I/O for the Wenmai (稳卖) client (process + Node inspector).

The macOS sibling of :mod:`wenmai_win`: the same duck-typed surface the
client drives on Windows (``launch`` / ``main_pid`` /
``kill_kernel_by_debug_port`` / ``locate_error``), implemented with the
shared Mac app locator (:mod:`app.browser.mac_app_locator`) and psutil
instead of PowerShell, plus the Mac inspector
(:mod:`app.browser.mac_inspector`, SIGUSR1) instead of the Windows
debug launch. The DOM logic (:mod:`wenmai_dom`) and the local-API
shapes (:mod:`wenmai_client`) are shared unchanged — the Mac client is
the same Electron app running the same Express API on port 3980.

Live facts (Mac client v5.1.0615, verified 2026-11-27): the app bundle
is ``/Applications/WenMaiAIBrowser.app`` (bundle id
``com.wenmai.aibrowser``, exe ``Contents/MacOS/WenMaiAIBrowser``); the
local API answers on 3980 with the same shapes as Windows; userData
lives under ``~/Library/Application Support/WenMaiAIBrowser/`` (with
the ``Browser`` tree, ``wenmaiaibrowserdatas`` and
``wenmaiaibrowser-conf`` siblings); the Electron fuses leave the
default Node behaviour where ``SIGUSR1`` opens the inspector on 9229.

The kernel-kill identity reuses :func:`wenmai_win.is_wenmai_kernel`:
a Mac kernel's command line is normalised to Windows separators first
and the matcher is fed the MAC kernel-path shape
(:data:`MAC_KERNEL_PATH_RE` — the Windows tree has an intermediate
``instances\\…`` segment the Mac tree lacks), so the same three checks
(the debug-port flag, the 稳卖 kernel exe path, the env id at the tail
of ``--user-data-dir``) run on both platforms and stay unit-tested in
one place. All functions are blocking; call via ``asyncio.to_thread``.
"""

import logging

import psutil

from app.browser import mac_app_locator as mac_locator
from app.browser.adapters.wenmai_win import (
    DEFAULT_API_PORT,
    is_wenmai_kernel,
)
from app.browser.mac_app_locator import (
    LocatedMacApp,
    MacAppSpec,
)

__all__ = [
    'MAC_KERNEL_PATH_RE',
    'WENMAI_MAC_APP',
    'WenmaiMacProcessLayer',
]

logger = logging.getLogger(__name__)

# Install discovery for THIS vendor, declared here (not in the shared
# locator) per R1 — the macOS twin of wenmai_win.WENMAI_WIN_APP.
WENMAI_MAC_APP = MacAppSpec(
    key='wenmai',
    display_name='稳卖 (WenMai)',
    bundle_ids=('com.wenmai.aibrowser',),
    app_names=('WenMaiAIBrowser.app',),
)

# The MAC kernel exe shape (verified live, client v5.1.0615): the
# kernel is its own .app bundle under the userData tree,
# ``<userData>/WenMaiAIBrowser/Browser/<chrome-ver>/WenMaiAIBrowser.app/
# Contents/MacOS/WenMaiAIBrowser`` — unlike Windows there is NO
# intermediate ``instances\…`` segment, so the Windows
# ``wenmaiaibrowser\…\browser\`` regex does NOT match it (found live:
# the real Mac kernel path failed the Windows shape). The client's own
# main exe (``…/WenMaiAIBrowser.app/Contents/MacOS/…`` directly under
# /Applications) and the sibling data dirs (``wenmaiaibrowserdatas``,
# ``wenmaiaibrowser-conf``) all lack the ``wenmaiaibrowser\browser\``
# segment, so none of them can match.
MAC_KERNEL_PATH_RE = r'wenmaiaibrowser\\browser\\[^\\]+\\wenmaiaibrowser\.app\\'


def _no_type_flag(args: tuple[str, ...]) -> bool:
    """Whether the process is a main process, not an Electron helper.

    Helpers carry ``--type=renderer|utility|gpu-process``; a main
    process never does. ``startswith``, not membership: an arg is
    ``'--type=renderer'``, never exactly ``'--type='``.
    """
    return not any(a.startswith('--type=') for a in args)


class WenmaiMacProcessLayer:
    """Locate / launch the 稳卖 client and find its main process."""

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
            WENMAI_MAC_APP, saved_path=self._client_path
        )
        if located:
            self._located = located
            return located.app_path
        self.locate_error = mac_locator.not_found_message(WENMAI_MAC_APP)
        return None

    def is_running(self) -> bool:
        """Any process of the app bundle is running."""
        if not self.locate_install_dir():
            return False
        return bool(mac_locator.app_processes(self._located))

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

    def main_pid(self, api_port: int = DEFAULT_API_PORT) -> int | None:
        """The MAIN process pid (the app's own exe, no ``--type=`` arg).

        The Windows twin derives it from the API-port listener's owner,
        but macOS refuses the process-fd enumeration psutil needs for
        that (``proc_pidinfo(PROC_PIDLISTFDS)`` → EPERM, seen live), so
        the pid comes from the shared locator's process scan instead:
        the one process whose exe IS the located app's main executable
        and that carries no ``--type=`` helper flag (kernels run from
        userData, outside the bundle, so they never match; another app
        squatting on the API port never matches either — and SIGUSR1's
        default action terminates, so only a proven 稳卖 main exe is
        ever signalled). ``api_port`` is kept for the Windows-twin call
        signature; the port itself is proven answering by
        ``ensure_running`` before anyone reaches here.
        """
        located = self._located
        if not self.locate_install_dir():
            return None
        for proc in mac_locator.app_processes(self._located):
            if not _no_type_flag(proc.args):
                continue
            if located and proc.exe != located.exe_path:
                continue
            return proc.pid
        return None

    def kill_kernel_by_debug_port(self, port: int, env_id: str) -> bool:
        """Kill THIS store's stuck 稳卖 kernel, and nothing else.

        The same three-check identity as Windows
        (:func:`wenmai_win.is_wenmai_kernel`, pure and unit-tested
        there): the debug-port flag, the 稳卖 kernel exe path and this
        store's env id at the tail of ``--user-data-dir`` — with the
        Mac command line's ``/`` separators normalised to ``\\`` and
        the MAC kernel-path shape
        (:data:`MAC_KERNEL_PATH_RE`) fed to the one matcher. Returns
        True when the kernel was identified (the kill itself is
        best-effort, like every kill); False means nothing matched —
        the caller must NOT keep guessing.
        """
        victims = []
        for p in psutil.process_iter(['pid', 'exe', 'cmdline']):
            try:
                cmdline = ' '.join(str(a) for a in (p.info['cmdline'] or []))
                if not cmdline:
                    continue
                normalized = cmdline.replace('/', '\\')
                if is_wenmai_kernel(
                    normalized,
                    str(p.info['exe'] or ''),
                    port,
                    env_id,
                    kernel_path_re=MAC_KERNEL_PATH_RE,
                ):
                    victims.append(p.info['pid'])
            except (psutil.Error, OSError):
                continue
        if not victims:
            return False
        for pid in victims:
            try:
                psutil.Process(pid).terminate()
            except (psutil.Error, OSError) as e:
                logger.debug('could not terminate kernel %s: %s', pid, e)
        logger.info(
            'terminated stuck 稳卖 kernel pid(s) %s (port %s, env %s)',
            victims,
            port,
            env_id,
        )
        return True
