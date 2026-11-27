"""Unit tests for the Wenmai macOS process layer.

The Mac layer wraps the shared Mac app locator (its own tests cover
discovery) — here we pin the wenmai-specific behaviour: the MAIN
process pick (the inspector's SIGUSR1 target), the client/inspector
platform seams, and the kernel kill's three-check identity with Mac
paths (the shared ``is_wenmai_kernel`` matcher fed a separator-
normalised Mac command line).
"""

from unittest import mock

import pytest

import app.browser.adapters.wenmai_client as wc
import app.browser.adapters.wenmai_mac as wm
from app.browser.adapters.wenmai_win import is_wenmai_kernel
from app.browser.mac_app_locator import LocatedMacApp, MacAppProcess
from app.browser.mac_inspector import MacInspectorSession
from app.browser.win_inspector import InspectorSession

pytestmark = pytest.mark.unit

APP = '/Applications/WenMaiAIBrowser.app'
EXE = f'{APP}/Contents/MacOS/WenMaiAIBrowser'
BUNDLE_ID = 'com.wenmai.aibrowser'
# The kernel runs from the client's userData tree (verified live),
# OUTSIDE the .app bundle — so it never matches the app's own exe.
# The REAL live shape (verified on the Mac, client v5.1.0615): the
# kernel runs from the client's userData tree as
# …/WenMaiAIBrowser/Browser/<chrome-ver>/WenMaiAIBrowser.app/…, with
# the store's profile at …/WenMaiAIBrowser/storeCache/<env id>.
KERNEL = (
    '/Users/u/Library/Application Support/WenMaiAIBrowser'
    '/Browser/146/WenMaiAIBrowser.app/Contents/MacOS/WenMaiAIBrowser'
)
ENV_ID = '100000001'
UDD = (
    f'/Users/u/Library/Application Support/WenMaiAIBrowser/storeCache/{ENV_ID}'
)
OTHER_UDD = (
    '/Users/u/Library/Application Support/WenMaiAIBrowser/storeCache/100000002'
)

_LOCATED = LocatedMacApp(app_path=APP, exe_path=EXE, bundle_id=BUNDLE_ID)
_MAIN = MacAppProcess(pid=101, exe=EXE, args=(EXE, '--lang=zh-CN'))
_HELPER = MacAppProcess(
    pid=102,
    exe=f'{APP}/Contents/Frameworks/WenMaiAIBrowser Helper.app'
    '/Contents/MacOS/WenMaiAIBrowser Helper',
    args=(EXE, '--type=renderer'),
)
# Another app's main exe squatting on the API port: matched by neither
# the bundle process scan (different exe) nor the kernel identity.
_FOREIGN = MacAppProcess(
    pid=103,
    exe='/Applications/OtherApp.app/Contents/MacOS/OtherApp',
    args=('/Applications/OtherApp.app/Contents/MacOS/OtherApp',),
)


def _layer(
    monkeypatch,
    *,
    located=_LOCATED,
    procs=(_MAIN, _HELPER, _FOREIGN),
):
    """A layer over a faked shared locator (monkeypatch auto-restores
    the shared module after the test)."""
    layer = wm.WenmaiMacProcessLayer()
    monkeypatch.setattr(
        wm.mac_locator,
        'locate',
        lambda spec, saved_path=None: located,
    )
    monkeypatch.setattr(
        wm.mac_locator, 'app_processes', lambda loc: list(procs)
    )
    return layer


class TestMainPid:
    def test_picks_the_app_exe_with_no_type_flag(self, monkeypatch):
        """The inspector's SIGUSR1 target is the process whose exe IS
        the located app's main executable and that carries no
        ``--type=`` helper flag."""
        assert _layer(monkeypatch).main_pid() == 101

    def test_none_when_only_helpers_run(self, monkeypatch):
        layer = _layer(monkeypatch, procs=(_HELPER,))
        assert layer.main_pid() is None

    def test_none_when_not_installed(self, monkeypatch):
        """No install found → None, with the locator's actionable
        error surfaced for the ClientNotRunning message."""
        layer = _layer(monkeypatch, located=None)
        assert layer.main_pid() is None
        assert layer.locate_error  # e.g. 'Could not find 稳卖 …'

    def test_is_running_false_when_not_installed(self, monkeypatch):
        assert _layer(monkeypatch, located=None).is_running() is False

    def test_is_running_true_with_any_bundle_process(self, monkeypatch):
        assert _layer(monkeypatch, procs=(_HELPER,)).is_running() is True


class TestLaunch:
    def test_launch_returns_false_with_locate_error(self, monkeypatch):
        layer = _layer(monkeypatch, located=None)
        assert layer.launch() is False
        assert layer.locate_error

    def test_launch_uses_the_shared_locator(self, monkeypatch):
        layer = _layer(monkeypatch)
        with mock.patch.object(wm.mac_locator, 'launch') as launcher:
            assert layer.launch() is True
        launcher.assert_called_once_with(_LOCATED)


class TestPlatformSeams:
    """The client picks its process layer and inspector by platform
    (the #335 pattern): the Mac branch must construct the Mac twins,
    the Windows branch the Windows ones — whatever host runs the
    suite."""

    def test_mac_pick_constructs_the_mac_layer(self):
        with mock.patch.object(wc, 'get_platform', return_value='mac'):
            assert isinstance(wc.process_layer({}), wm.WenmaiMacProcessLayer)

    def test_windows_pick_constructs_the_windows_layer(self):
        with mock.patch.object(wc, 'get_platform', return_value='windows'):
            assert not isinstance(
                wc.process_layer({}), wm.WenmaiMacProcessLayer
            )

    def test_mac_pick_constructs_the_mac_inspector(self):
        with mock.patch.object(wc, 'get_platform', return_value='mac'):
            assert isinstance(
                wc.inspector_session(mock.Mock()), MacInspectorSession
            )

    def test_windows_pick_constructs_the_windows_inspector(self):
        with mock.patch.object(wc, 'get_platform', return_value='windows'):

            def preamble():
                return "$exe='x';"

            proc = mock.Mock(exe_path_script=preamble)
            insp = wc.inspector_session(proc)
        assert isinstance(insp, InspectorSession)
        # built from the proc's PowerShell preamble, per the base class
        # (called at open() time so a fresh locate happens per session)
        assert insp._exe_path_script is preamble


def _kernel_proc(pid, port, udd, exe=KERNEL):
    """A psutil row for one kernel-shaped process."""
    cmdline = (
        f'{exe} --user-data-dir={udd} '
        f'--remote-debugging-port={port} --use-mock-keychain'
    )
    info = {'pid': pid, 'exe': exe, 'cmdline': cmdline.split(' ')}
    proc = mock.Mock()
    proc.info = info
    return proc


class TestKillKernelByDebugPort:
    """The same three-check identity as Windows, fed Mac paths: only
    the process carrying ALL of the debug-port flag, the 稳卖 kernel
    exe path and this store's env id is terminated — never a foreign
    browser that merely holds the port."""

    def _run(self, procs, port=50555, env_id=ENV_ID):
        terminated = []
        with (
            mock.patch.object(
                wm.psutil, 'process_iter', lambda _attrs=None: procs
            ),
            mock.patch.object(
                wm.psutil,
                'Process',
                lambda pid: (
                    terminated.append(pid) or mock.Mock(terminate=mock.Mock())
                ),
            ),
        ):
            killed = wm.WenmaiMacProcessLayer().kill_kernel_by_debug_port(
                port, env_id
            )
        return killed, terminated

    def test_kills_exactly_this_stores_kernel(self):
        killed, terminated = self._run([
            _kernel_proc(201, 50555, UDD),
            # same port, a foreign exe: the port alone is NOT enough
            _kernel_proc(
                202,
                50555,
                UDD,
                exe='/Applications/Other.app/Contents/MacOS/Chromium',
            ),
            # this env's kernel but a different debug port
            _kernel_proc(203, 50556, UDD),
            # the right kernel shape but ANOTHER store's env id
            _kernel_proc(204, 50555, OTHER_UDD),
        ])
        assert killed is True
        assert terminated == [201]

    def test_returns_false_when_nothing_matches(self):
        """Nothing matched → False: the caller must NOT keep guessing
        at a kill (another app's browser may hold the port)."""
        killed, terminated = self._run([
            _kernel_proc(202, 50555, UDD, exe='/opt/other/browser')
        ])
        assert killed is False
        assert terminated == []


class TestKernelMatcherTakesMacPaths:
    """The shared matcher, fed the Mac command line normalised to
    backslash separators and the MAC kernel-path shape, accepts a real
    Mac kernel — the one place both platforms' identity is pinned
    together."""

    def test_mac_cmdline_matches_after_normalisation(self):
        cmdline = (
            f'{KERNEL} --user-data-dir={UDD} --remote-debugging-port=50555'
        ).replace('/', '\\')
        assert is_wenmai_kernel(
            cmdline,
            KERNEL,
            50555,
            ENV_ID,
            kernel_path_re=wm.MAC_KERNEL_PATH_RE,
        )

    def test_raw_mac_cmdline_is_not_accidentally_matched(self):
        """The normalisation is the layer's job: the RAW Mac command
        line (forward slashes) must NOT match the Windows-shaped
        tail — pinning why ``kill_kernel_by_debug_port`` normalises."""
        raw = f'{KERNEL} --user-data-dir={UDD} --remote-debugging-port=50555'
        assert not is_wenmai_kernel(
            raw,
            KERNEL,
            50555,
            ENV_ID,
            kernel_path_re=wm.MAC_KERNEL_PATH_RE,
        )

    def test_windows_shape_does_not_match_the_mac_regex(self):
        """The live-found bug, pinned: the REAL Mac kernel tree
        (``…\\WenMaiAIBrowser\\Browser\\146\\WenMaiAIBrowser.app\\…``,
        no intermediate segment) does NOT match the Windows
        ``wenmaiaibrowser\\…\\browser\\`` shape — without the Mac regex
        the stale-kernel kill would silently never fire on macOS."""
        cmdline = (
            f'{KERNEL} --user-data-dir={UDD} --remote-debugging-port=50555'
        ).replace('/', '\\')
        # the Windows default rejects the real Mac path …
        assert not is_wenmai_kernel(cmdline, KERNEL, 50555, ENV_ID)
        # … and the Mac regex rejects the Windows-style tree (an
        # intermediate segment where the Mac tree has none).
        win_tree = (
            r'C:\Users\u\AppData\Roaming\WenMaiAIBrowser\instances\i1'
            r'\Browser\120.0\WenMaiAIBrowser.exe'
        )
        win_cmdline = (
            f'{win_tree} --user-data-dir=C:\\Users\\u\\AppData\\Roaming'
            f'\\WenMaiAIBrowser\\storeCache\\{ENV_ID}'
            ' --remote-debugging-port=50555'
        )
        assert not is_wenmai_kernel(
            win_cmdline,
            win_tree,
            50555,
            ENV_ID,
            kernel_path_re=wm.MAC_KERNEL_PATH_RE,
        )

    def test_client_main_exe_never_matches_the_mac_regex(self):
        """The client's OWN main exe and the sibling data dirs cannot
        satisfy the Mac kernel shape — only a kernel under
        ``…\\WenMaiAIBrowser\\Browser\\<ver>\\WenMaiAIBrowser.app\\``
        can (the kill must never target the client itself)."""
        for exe in (
            EXE,  # the .app bundle main exe in /Applications
            '/Applications/WenMaiAIBrowser.app/Contents/MacOS/OtherHelper',
            # the userData root and the sibling data dirs
            '/Users/u/Library/Application Support/WenMaiAIBrowser/x',
            '/Users/u/Library/Application Support/wenmaiaibrowserdatas/x',
            '/Users/u/Library/Application Support/wenmaiaibrowser-conf/x',
        ):
            cmdline = (
                f'{exe} --user-data-dir={UDD} --remote-debugging-port=50555'
            )
            assert not is_wenmai_kernel(
                cmdline.replace('/', '\\'),
                exe,
                50555,
                ENV_ID,
                kernel_path_re=wm.MAC_KERNEL_PATH_RE,
            ), exe
