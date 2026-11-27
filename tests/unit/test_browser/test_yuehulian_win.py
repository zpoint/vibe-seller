"""Pure helpers + invariants for the Yuehulian Windows I/O layer.

The powershell / inspector-ws methods are I/O and are covered by the live
e2e run (validated 2026-11-18); here we pin the pure path conversion and
the spawn-hook's safety invariant (save-and-restore, never ``delete``).
"""

import json
import ntpath
import os
import shutil
import subprocess

import pytest

import app.browser.adapters.yuehulian_win as win_mod
from app.browser.adapters.yuehulian_win import (
    SPAWN_HOOK_JS,
    SPAWN_UNHOOK_JS,
    win_path_to_local,
)
from app.browser.win_app_locator import win_path_to_wsl

pytestmark = pytest.mark.unit


def test_win_path_to_wsl_basic():
    assert win_path_to_wsl('C:\\Users\\Admin\\x') == '/mnt/c/Users/Admin/x'


def test_win_path_to_wsl_double_backslash_and_quotes():
    # Win32 cmdline user-data-dir can arrive double-escaped / quoted.
    assert win_path_to_wsl('"D:\\\\a\\\\b"').startswith('/mnt/d/')


def test_win_path_to_wsl_passthrough_for_non_drive():
    assert win_path_to_wsl('/already/unix') == '/already/unix'


def test_win_path_to_local_native_windows_keeps_windows_path(monkeypatch):
    # The real deployment is native Windows: the file is read at C:\...,
    # NOT /mnt/c/... . Converting to /mnt there was the production bug that
    # made read_devtools_port never find DevToolsActivePort. Exercise the
    # nt branch on Linux CI via ntpath.
    monkeypatch.setattr(win_mod.os, 'name', 'nt')
    monkeypatch.setattr(win_mod.os.path, 'normpath', ntpath.normpath)
    out = win_path_to_local('C:\\Users\\Admin\\x')
    assert out == 'C:\\Users\\Admin\\x'
    assert not out.startswith('/mnt')


def test_win_path_to_local_native_collapses_doubled_backslashes(monkeypatch):
    # The client doubles separators in a kernel's spawn args; normpath
    # collapses them so the DevToolsActivePort path resolves.
    monkeypatch.setattr(win_mod.os, 'name', 'nt')
    monkeypatch.setattr(win_mod.os.path, 'normpath', ntpath.normpath)
    out = win_path_to_local('C:\\\\Users\\\\Admin\\\\x')
    assert out == 'C:\\Users\\Admin\\x'


def test_win_path_to_local_twist_wsl_uses_mnt(monkeypatch):
    # Twist: under WSL (non-nt) the SAME input must convert to /mnt — the
    # other half of the OS-aware contract.
    monkeypatch.setattr(win_mod.os, 'name', 'posix')
    assert win_path_to_local('C:\\Users\\Admin\\x') == '/mnt/c/Users/Admin/x'


def test_spawn_hook_injects_debug_port():
    assert '--remote-debugging-port=0' in SPAWN_HOOK_JS
    assert '--user-data-dir' in SPAWN_HOOK_JS


def test_spawn_hook_injects_occlusion_flag_once():
    # The hook must carry the occlusion flag exactly once (an
    # accidentally doubled literal would mean the guard is missing) and
    # must guard it so a vendor arg already carrying it is not
    # duplicated.
    assert (
        SPAWN_HOOK_JS.count('--disable-backgrounding-occluded-windows') == 2
    )  # once in the guard, once in the push
    assert (
        SPAWN_UNHOOK_JS.count('--disable-backgrounding-occluded-windows') == 0
    )


def test_spawn_hook_saves_original_and_never_deletes():
    # The hook must save the native spawn and restore it, never `delete`
    # it (that bricks the client's child-process spawning).
    assert '__vsOrigSpawn' in SPAWN_HOOK_JS
    assert '__vsOrigSpawn' in SPAWN_UNHOOK_JS
    assert 'delete ' not in SPAWN_HOOK_JS
    assert 'delete ' not in SPAWN_UNHOOK_JS


# The process queries, pinned as COMPLETE scripts: a substring check
# cannot notice a missing brace. Behaviour under a real engine is
# covered by test_powershell_scripts.py.
_NAME_ARM = "$_.ExecutablePath -match 'YueHuLian'"


def _captured(monkeypatch, method, install_dir=None):
    """Run a ProcessLayer method with PowerShell stubbed; return the
    exact script it generated."""
    scripts = []

    def fake_run(script, *_a, **_k):
        scripts.append(script)
        return ''

    monkeypatch.setattr(win_mod, '_run_ps', fake_run)
    getattr(win_mod.ProcessLayer(install_dir), method)()
    assert len(scripts) == 1
    return scripts[0]


def _both_arms(ps_dir_literal: str) -> str:
    """The path condition: the located dir (given as the PowerShell
    single-quoted literal the product must emit) OR the legacy name —
    ``((<null-guarded prefix>) -or <name>)``."""
    return (
        '(($_.ExecutablePath -and $_.ExecutablePath.StartsWith('
        + ps_dir_literal
        + ', [StringComparison]::OrdinalIgnoreCase))'
        + ' -or '
        + _NAME_ARM
        + ')'
    )


def _is_running_script(condition: str) -> str:
    return (
        '(Get-CimInstance Win32_Process | Where-Object { '
        + condition
        + " -and $_.Name -ne 'chrome.exe' } | Measure-Object).Count"
    )


def _main_pid_script(condition: str) -> str:
    return (
        'Get-CimInstance Win32_Process | Where-Object { '
        + condition
        + " -and $_.Name -ne 'chrome.exe'"
        + " -and $_.ExecutablePath -notmatch 'resources'"
        + " -and $_.CommandLine -notmatch '--type=' }"
        + ' | Select-Object -ExpandProperty ProcessId -First 1'
    )


class TestProcessMatchUsesLocatedDir:
    """is_running()/main_pid() must see a custom-install client.

    Installed to e.g. ``D:\\悦互联`` the client's path contains no
    'YueHuLian', so the process queries match the LOCATED install dir
    as a case-insensitive path prefix (quotes escaped for PowerShell),
    with the legacy name as the second arm.
    """

    def test_is_running_script_matches_chinese_install_dir(self, monkeypatch):
        script = _captured(monkeypatch, 'is_running', install_dir='D:\\悦互联')
        # Spelled out in full …
        assert script == (
            '(Get-CimInstance Win32_Process | Where-Object { '
            '(($_.ExecutablePath -and $_.ExecutablePath.StartsWith('
            "'D:\\悦互联\\', [StringComparison]::OrdinalIgnoreCase)) "
            "-or $_.ExecutablePath -match 'YueHuLian') "
            "-and $_.Name -ne 'chrome.exe' } | Measure-Object).Count"
        )
        # … and equal to the composed expectation.
        assert script == _is_running_script(_both_arms("'D:\\悦互联\\'"))

    def test_main_pid_script_matches_located_dir(self, monkeypatch):
        assert _captured(
            monkeypatch, 'main_pid', install_dir='E:\\custom dir'
        ) == _main_pid_script(_both_arms("'E:\\custom dir\\'"))

    def test_single_quotes_in_dir_are_escaped(self, monkeypatch):
        assert _captured(
            monkeypatch, 'is_running', install_dir="D:\\悦'联"
        ) == _is_running_script(_both_arms("'D:\\悦''联\\'"))

    def test_trailing_backslash_is_not_part_of_the_prefix(self, monkeypatch):
        assert _captured(
            monkeypatch, 'is_running', install_dir='D:\\悦互联\\'
        ) == _is_running_script(_both_arms("'D:\\悦互联\\'"))

    def test_drive_root_never_becomes_a_prefix(self, monkeypatch):
        # Located at D:\ the prefix would match the whole drive.
        for root in ('D:\\', 'D:'):
            assert _captured(
                monkeypatch, 'is_running', install_dir=root
            ) == _is_running_script('(' + _NAME_ARM + ')')

    def test_not_located_falls_back_to_name_only(self, monkeypatch):
        monkeypatch.setattr(
            win_mod.win_locator, 'locate', lambda spec, **kw: None
        )
        name_only = '(' + _NAME_ARM + ')'
        assert _captured(monkeypatch, 'is_running') == _is_running_script(
            name_only
        )
        assert _captured(monkeypatch, 'main_pid') == _main_pid_script(name_only)

    def test_configured_dir_counts_without_discovery(self, monkeypatch):
        # A caller-configured dir is trusted: the prefix arm uses it
        # directly and locate() is never asked (prove with a bomb).
        def bomb(*_a, **_k):
            raise AssertionError('locate() called for a configured dir')

        monkeypatch.setattr(win_mod.win_locator, 'locate', bomb)
        assert _captured(
            monkeypatch, 'is_running', install_dir='D:\\自定义目录'
        ) == _is_running_script(_both_arms("'D:\\自定义目录\\'"))


# --- Behavioural check of SPAWN_HOOK_JS under a real JS engine ---------
# The text tests above pin that the flag is present; these run the hook
# itself in node against a stubbed child_process, so the "exactly once,
# guarded, and only for kernels" behaviour is proven, not assumed.
# Skipped where node is absent (CI's unit job may not install it); the
# text tests above still pin the invariant there.

_NODE_RUNNER = """
const cpPath = require.resolve('child_process');
const captured = [];
const fakeCp = {ChildProcess: {prototype: {spawn: function(options){
  captured.push({file: options && options.file,
                 args: (options && options.args) || []});
}}}};
require.cache[cpPath] = {id: cpPath, filename: cpPath, loaded: true,
                         exports: fakeCp};
const hookResult = eval(process.env.VS_HOOK_JS);
const cp = require('child_process');
cp.ChildProcess.prototype.spawn(JSON.parse(process.env.VS_OPTIONS));
console.log(JSON.stringify({hookResult: hookResult, captured: captured,
                            spawns: global.__vsSpawns}));
"""


def _run_spawn_hook(options):
    """Run SPAWN_HOOK_JS in node with child_process stubbed; spawn
    ``options`` through the hooked prototype; return the parsed result."""
    env = dict(
        os.environ,
        VS_HOOK_JS=SPAWN_HOOK_JS,
        VS_OPTIONS=json.dumps(options),
    )
    proc = subprocess.run(
        ['node', '-e', _NODE_RUNNER],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=30,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not shutil.which('node'), reason='needs node')
def test_spawn_hook_node_adds_debug_port_and_occlusion_flag_once():
    out = _run_spawn_hook({
        'file': r'C:\YueHuLian\chrome.exe',
        'args': [r'chrome.exe', r'--user-data-dir=C:\udd1'],
    })
    assert out['hookResult'] == 'hooked'
    assert len(out['captured']) == 1
    args = out['captured'][0]['args']
    assert args.count('--remote-debugging-port=0') == 1
    assert args.count('--disable-backgrounding-occluded-windows') == 1
    # the recorded spawn points at the kernel's user-data-dir
    assert out['spawns'] == [{'udd': r'C:\udd1', 'ts': out['spawns'][0]['ts']}]


@pytest.mark.skipif(not shutil.which('node'), reason='needs node')
def test_spawn_hook_node_never_duplicates_vendor_occlusion_flag():
    out = _run_spawn_hook({
        'file': 'chrome.exe',
        'args': [
            'chrome.exe',
            '--disable-backgrounding-occluded-windows',
            r'--user-data-dir=C:\udd2',
        ],
    })
    args = out['captured'][0]['args']
    assert args.count('--disable-backgrounding-occluded-windows') == 1
    assert args.count('--remote-debugging-port=0') == 1


@pytest.mark.skipif(not shutil.which('node'), reason='needs node')
def test_spawn_hook_node_vendor_debug_port_still_gets_occlusion_flag():
    # A kernel the vendor already launches with its own debug port gets
    # no port injected and no spawn recorded (the vendor's port makes
    # the recording meaningless) — but it STILL gets the occlusion flag
    # exactly once: a minimized kernel hangs the same way regardless of
    # who opened the debug port.
    out = _run_spawn_hook({
        'file': 'chrome.exe',
        'args': ['chrome.exe', '--remote-debugging-port=1234'],
    })
    args = out['captured'][0]['args']
    assert '--remote-debugging-port=0' not in args
    assert args.count('--disable-backgrounding-occluded-windows') == 1
    assert out['spawns'] == []


@pytest.mark.skipif(not shutil.which('node'), reason='needs node')
def test_spawn_hook_node_vendor_port_and_flag_not_duplicated():
    # Vendor port + vendor flag: nothing of ours is added at all.
    out = _run_spawn_hook({
        'file': 'chrome.exe',
        'args': [
            'chrome.exe',
            '--remote-debugging-port=1234',
            '--disable-backgrounding-occluded-windows',
        ],
    })
    args = out['captured'][0]['args']
    assert args.count('--disable-backgrounding-occluded-windows') == 1
    assert '--remote-debugging-port=0' not in args
    assert out['spawns'] == []


@pytest.mark.skipif(not shutil.which('node'), reason='needs node')
def test_spawn_hook_node_ignores_non_kernel_spawns():
    out = _run_spawn_hook({
        'file': r'C:\Windows\notepad.exe',
        'args': ['notepad.exe'],
    })
    assert out['captured'][0]['args'] == ['notepad.exe']
    assert out['spawns'] == []
