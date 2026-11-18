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
    win_path_to_wsl,
)

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
