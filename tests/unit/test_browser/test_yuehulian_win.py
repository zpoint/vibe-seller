"""Pure helpers + invariants for the Yuehulian Windows I/O layer.

The powershell / inspector-ws methods are I/O and are covered by the live
e2e run (validated 2026-11-18); here we pin the pure path conversion and
the spawn-hook's safety invariant (save-and-restore, never ``delete``).
"""

import pytest

from app.browser.adapters.yuehulian_win import (
    SPAWN_HOOK_JS,
    SPAWN_UNHOOK_JS,
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
