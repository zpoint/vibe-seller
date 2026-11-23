"""Pure helpers + invariants for the Yuehulian Windows I/O layer.

The powershell / inspector-ws methods are I/O and are covered by the live
e2e run (validated 2026-11-18); here we pin the pure path conversion and
the spawn-hook's safety invariant (save-and-restore, never ``delete``).
"""

import ntpath

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
