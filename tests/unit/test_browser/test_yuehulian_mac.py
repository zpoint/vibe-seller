"""Unit tests for the Yuehulian macOS process layer + Mac spawn hook.

The Mac layer wraps the shared Mac app locator (its own tests cover
discovery) — here we pin the yuehulian-specific filtering: the MAIN
process pick, kernel discovery/termination, DevToolsActivePort reading,
and the Mac spawn hook's kernel match (plus the pin that the Windows
hook text stayed byte-identical).
"""

import os
import re
from types import SimpleNamespace
from unittest import mock

import pytest

from app.browser.adapters import yuehulian_win
import app.browser.adapters.yuehulian_mac as ym
from app.browser.adapters.yuehulian_win import (
    SPAWN_UNHOOK_JS,
    ProcessLayer,
)
from app.browser.mac_app_locator import LocatedMacApp, MacAppProcess

pytestmark = pytest.mark.unit

APP = '/apps/悦互联.app'
EXE = f'{APP}/Contents/MacOS/悦互联'
BUNDLE_ID = 'com.chinafic.yuehulian'
KERNEL = (
    '/Users/u/Library/Application Support/YueHuLian-Resources'
    '/chrome/148.0.7778.167/Chromium.app/Contents/MacOS/Chromium'
)
KERNEL_HELPER = (
    '/Users/u/Library/Application Support/YueHuLian-Resources'
    '/chrome/148.0.7778.167/Chromium.app/Contents/Frameworks'
    '/Chromium Framework.framework/Versions/148.0.7778.167/Helpers'
    '/Chromium Helper.app/Contents/MacOS/Chromium Helper'
)
UDD = (
    '/Users/u/Library/Application Support/YueHuLian/YueHuLianData'
    '/0000aaaa0000bbbb0000cccc0000dddd'
)
OTHER_UDD = (
    '/Users/u/Library/Application Support/YueHuLian/YueHuLianData'
    '/0000000000000000000000000000000f'
)

_LOCATED = LocatedMacApp(app_path=APP, exe_path=EXE, bundle_id=BUNDLE_ID)

_MAIN = MacAppProcess(pid=101, exe=EXE, args=(EXE, '--lang=zh-CN'))
_HELPER = MacAppProcess(
    pid=102,
    exe=f'{APP}/Contents/Frameworks/悦互联 Helper.app'
    '/Contents/MacOS/悦互联 Helper',
    args=('--type=renderer',),
)


def _layer(
    *,
    located=_LOCATED,
    procs=(_MAIN, _HELPER),
    launch_raises=False,
):
    layer = ym.MacProcessLayer()
    launch_calls = []

    def fake_launch(loc, args=()):
        launch_calls.append((loc, args))
        if launch_raises:
            raise RuntimeError('open failed')
        return None

    ctx = mock.patch.multiple(
        ym.mac_locator,
        locate=lambda spec, saved_path=None: located,
        app_processes=lambda loc: list(procs),
    )
    if launch_raises:
        ctx2 = mock.patch.object(
            ym.mac_locator, 'launch', side_effect=RuntimeError('boom')
        )
    else:
        ctx2 = mock.patch.object(
            ym.mac_locator, 'launch', side_effect=fake_launch
        )
    return layer, ctx, ctx2, launch_calls


class TestMainPid:
    def test_picks_the_main_not_a_helper(self):
        layer, c1, c2, _ = _layer()
        with c1, c2:
            assert layer.main_pid() == 101

    def test_a_same_exe_process_with_type_arg_is_not_the_main(self):
        """A helper can run the MAIN's exe path with `--type=renderer`
        (Electron relaunches); the filter is on the arg, so membership
        (`'--type=' in args`) is not enough — an arg is never exactly
        '--type='. The helper is listed FIRST: a filter that does not
        drop it would pick it as the MAIN."""
        procs = [
            MacAppProcess(pid=105, exe=EXE, args=(EXE, '--type=renderer')),
            MacAppProcess(pid=101, exe=EXE, args=(EXE, '--lang=zh-CN')),
        ]
        layer, c1, c2, _ = _layer(procs=procs)
        with c1, c2:
            assert layer.main_pid() == 101

    def test_no_processes_means_no_pid(self):
        layer, c1, c2, _ = _layer(procs=())
        with c1, c2:
            assert layer.main_pid() is None

    def test_not_located_means_no_pid(self):
        layer, c1, c2, _ = _layer(located=None, procs=())
        with c1, c2:
            assert layer.main_pid() is None
            assert layer.locate_error  # surfaced for the error path


class TestRunning:
    def test_running_with_processes(self):
        layer, c1, c2, _ = _layer()
        with c1, c2:
            assert layer.is_running() is True

    def test_not_located_is_not_running(self):
        layer, c1, c2, _ = _layer(located=None, procs=())
        with c1, c2:
            assert layer.is_running() is False


class TestLaunch:
    def test_launch_uses_the_shared_locator(self):
        layer, c1, c2, calls = _layer()
        with c1, c2:
            assert layer.launch() is True
        assert calls == [(_LOCATED, ())]

    def test_launch_failure_is_false_and_records_the_error(self):
        layer, c1, c2, _ = _layer(launch_raises=True)
        with c1, c2:
            assert layer.launch() is False
        assert layer.locate_error


class TestDevtoolsPort:
    def test_reads_the_port_from_the_user_data_dir(self, tmp_path):
        (tmp_path / 'DevToolsActivePort').write_text('64927\npath')
        layer = ym.MacProcessLayer()
        assert layer.read_devtools_port(str(tmp_path)) == 64927

    def test_a_stale_file_is_rejected_by_newer_than(self, tmp_path):
        f = tmp_path / 'DevToolsActivePort'
        f.write_text('64927\n')
        old = os.path.getmtime(f) - 10
        layer = ym.MacProcessLayer()
        assert (
            layer.read_devtools_port(str(tmp_path), newer_than=old + 20) is None
        )

    def test_a_fresh_file_passes_newer_than(self, tmp_path):
        f = tmp_path / 'DevToolsActivePort'
        f.write_text('64927\n')
        ts = os.path.getmtime(f)
        layer = ym.MacProcessLayer()
        assert layer.read_devtools_port(str(tmp_path), newer_than=ts - 1) == (
            64927
        )

    def test_a_missing_file_is_none(self, tmp_path):
        layer = ym.MacProcessLayer()
        assert layer.read_devtools_port(str(tmp_path / 'nope')) is None


class TestKernels:
    def _procs(self):
        """Fake psutil process_iter rows: 悦互联 kernels (main, helper,
        another env) plus a foreign Chromium carrying the SAME udd
        string and an unrelated process. The exe-path filter in
        ``_kernel_processes`` must keep only the 悦互联 kernels."""

        def row(pid, exe, args):
            info = {'pid': pid, 'exe': exe, 'cmdline': list(args)}
            return SimpleNamespace(pid=pid, info=info)

        return [
            row(201, KERNEL, (KERNEL, f'--user-data-dir={UDD}')),
            row(
                202,
                KERNEL_HELPER,
                ('--type=renderer', f'--user-data-dir={UDD}'),
            ),
            row(203, KERNEL, (KERNEL, f'--user-data-dir={OTHER_UDD}')),
            row(
                204,
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                ('chrome', f'--user-data-dir={UDD}'),
            ),
            row(205, '/usr/bin/other', ()),
        ]

    def _patch_process_iter(self):
        return mock.patch.object(
            ym.psutil, 'process_iter', lambda _attrs=None: self._procs()
        )

    def test_kernel_user_data_dirs_lists_unique_envs(self):
        with self._patch_process_iter():
            assert ym.MacProcessLayer().kernel_user_data_dirs() == [
                OTHER_UDD,
                UDD,
            ]

    def test_terminate_kernels_kills_only_that_envs_kernels(self):
        """Only kernels whose exe is under YueHuLian-Resources AND whose
        ``--user-data-dir`` equals this env's dir are terminated: the
        other env's kernel and the foreign Chromium with the same udd
        string are left alone."""
        terminated = []
        with (
            self._patch_process_iter(),
            mock.patch.object(
                ym.psutil,
                'Process',
                lambda pid: terminated.append(pid)
                or mock.Mock(terminate=lambda: None),
            ),
        ):
            killed = ym.MacProcessLayer().terminate_kernels(UDD)
        assert sorted(killed) == [201, 202]
        assert sorted(terminated) == [201, 202]


class TestWindowsLayerParity:
    def test_windows_terminate_kernels_is_a_noop(self):
        """Same interface on both process layers; Windows never kills
        (its kernels die with the client), so the client's orphan
        recovery is a no-op there."""
        assert ProcessLayer().terminate_kernels('/any/udd') == []


class TestSpawnHook:
    def test_mac_hook_injects_the_debug_port(self):
        assert '--remote-debugging-port=0' in ym.MAC_SPAWN_HOOK_JS
        assert '--user-data-dir' in ym.MAC_SPAWN_HOOK_JS

    def test_mac_hook_is_the_windows_hook_with_the_kernel_swapped(self):
        """The Mac hook is DERIVED from the Windows text (one string
        replace), so a future fix to the hook logic reaches both
        platforms. The swap must happen exactly once — a Windows hook
        edit that renames or duplicates the kernel regex breaks this
        loudly instead of silently matching nothing."""
        assert yuehulian_win.SPAWN_HOOK_JS.count(ym._WIN_KERNEL_JS_RE) == 1
        assert ym.MAC_SPAWN_HOOK_JS == yuehulian_win.SPAWN_HOOK_JS.replace(
            ym._WIN_KERNEL_JS_RE, ym._MAC_KERNEL_JS_RE
        )
        assert ym._WIN_KERNEL_JS_RE not in ym.MAC_SPAWN_HOOK_JS

    def test_mac_hook_matches_the_mac_kernel_only(self):
        """The hook's kernel regex fires for the Mac kernel path and for
        nothing else — not the client exe, not a Windows chrome.exe."""
        m = re.search(r'if \(/(.*)/i\.test\(file\)', ym.MAC_SPAWN_HOOK_JS)
        assert m, 'the hook must keep a case-insensitive file regex'
        pat = re.compile(m.group(1).replace('\\/', '/'), re.IGNORECASE)
        assert pat.search(KERNEL)
        assert not pat.search(EXE)
        assert not pat.search(r'C:\Program Files\YueHuLian\chrome.exe')

    def test_windows_spawn_hook_text_is_pinned(self):
        """The Windows hook must stay byte-identical (this PR adds a Mac
        twin; it must not rewrite the Windows one). Update this literal
        ONLY with an intentional Windows change, in its own PR."""
        assert (
            yuehulian_win.SPAWN_HOOK_JS
            == r"""
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
        )

    async def test_unhook_text_is_shared_with_windows(self):
        """The Mac session reuses the Windows unhook verbatim (same
        global state), so the two hooks restore the same way."""
        insp = ym.MacInspectorSession(ym.MacProcessLayer())
        evaluate = mock.AsyncMock(return_value='unhooked')
        with mock.patch.object(insp, 'evaluate', new=evaluate):
            await insp.uninstall_spawn_hook()
        assert evaluate.await_args.args == (SPAWN_UNHOOK_JS,)
