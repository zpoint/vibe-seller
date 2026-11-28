"""The wenmai Windows process layer: locator spec + PowerShell scripts.

The scripts are pinned as COMPLETE strings (a substring check cannot
notice a missing brace); behaviour under a real engine is covered by
test_powershell_scripts.py's harness and the live e2e.
"""

import json

import pytest

import app.browser.adapters.wenmai_win as win_mod
from app.browser.adapters.wenmai_win import (
    WENMAI_WIN_APP,
    WenmaiProcessLayer,
    is_wenmai_kernel,
)
import app.browser.win_app_locator as locator

pytestmark = pytest.mark.unit


def _captured_ps(monkeypatch, run) -> list[str]:
    scripts = []

    def fake_run(script, *_a, **_k):
        scripts.append(script)
        return run(script) if callable(run) else run

    monkeypatch.setattr(win_mod, '_run_ps', fake_run)
    return scripts


def _located(exe: str):
    return type('Located', (), {'exe_path': exe})()


def test_main_pid_script_reads_the_api_port_listener(monkeypatch):
    # The MAIN process is the owner of the 3980 listener (verified
    # live): the query must target exactly that, with errors muted so
    # a down client is None, not a PowerShell failure.
    scripts = _captured_ps(monkeypatch, '')
    WenmaiProcessLayer().main_pid()
    assert scripts == [
        '(Get-NetTCPConnection -LocalPort 3980 -State Listen '
        '-ErrorAction SilentlyContinue).OwningProcess'
    ]


def test_main_pid_parses_the_first_pid(monkeypatch):
    _captured_ps(monkeypatch, '4242\n')
    assert WenmaiProcessLayer().main_pid() == 4242


def test_main_pid_negative_control_none_when_no_listener(monkeypatch):
    _captured_ps(monkeypatch, '')
    assert WenmaiProcessLayer().main_pid() is None


def test_launch_uses_the_located_exe(monkeypatch):
    scripts = _captured_ps(monkeypatch, 'ok')
    monkeypatch.setattr(
        win_mod.win_locator,
        'locate',
        lambda spec, **kw: _located(r'C:\demo\WenMaiAIBrowser.exe'),
    )
    assert WenmaiProcessLayer().launch() is True
    assert scripts == [
        "Start-Process -FilePath 'C:\\demo\\WenMaiAIBrowser.exe';'ok'"
    ]


def test_launch_negative_control_no_exe_fails(monkeypatch):
    _captured_ps(monkeypatch, '')
    monkeypatch.setattr(win_mod.win_locator, 'locate', lambda spec, **kw: None)
    assert WenmaiProcessLayer().launch() is False


def test_exe_path_script_escapes_single_quotes(monkeypatch):
    _captured_ps(monkeypatch, '')
    monkeypatch.setattr(
        win_mod.win_locator,
        'locate',
        lambda spec, **kw: _located(r"C:\demo's\app.exe"),
    )
    assert (
        WenmaiProcessLayer().exe_path_script() == r"$exe='C:\demo''s\app.exe';"
    )


def test_configured_path_is_honoured(monkeypatch):
    # wenmai_client_path (a full exe or a dir) goes to the shared
    # locator's configured hint — operator intent, trusted as-is.
    calls = {}

    def fake_locate(spec, *, configured_path=None, **kw):
        calls['spec'] = spec
        calls['configured'] = configured_path
        return _located(r'D:\demo\app.exe')

    monkeypatch.setattr(win_mod.win_locator, 'locate', fake_locate)
    exe = WenmaiProcessLayer(r'D:\custom').exe_path()
    assert exe == r'D:\demo\app.exe'
    assert calls['spec'] is WENMAI_WIN_APP
    assert calls['configured'] == r'D:\custom'


def test_wenmai_spec_is_declared_in_the_vendor_module():
    # R1: vendor knowledge lives in the vendor module, not the shared
    # locator. The spec matches the real install (exe name + tokens).
    assert WENMAI_WIN_APP.key == 'wenmai'
    assert 'wenmaiaibrowser.exe' in WENMAI_WIN_APP.exe_names
    assert '稳卖' in WENMAI_WIN_APP.match_tokens
    # And the shared locator module no longer carries it.
    assert not hasattr(locator, 'WENMAI_WIN_APP')


# ── W2: the stuck-kernel kill matches ONLY this store's kernel ──────

_KERNEL_EXE = (
    'C:\\Users\\<user>\\AppData\\Roaming\\WenMaiAIBrowser'
    '\\instances\\userdata1\\Browser\\135\\5.1.0716'
    '\\WenMaiAIBrowser.exe'
)
_FOREIGN_EXE = (
    'C:\\Users\\<user>\\AppData\\Roaming\\adspower_global'
    '\\cwd_global\\chrome_156\\SunBrowser.exe'
)


def _kernel_cmdline(port, env):
    udd = (
        '--user-data-dir="C:\\Users\\<user>\\AppData\\Roaming'
        '\\WenMaiAIBrowser\\storeCache\\' + env + '"'
    )
    return (
        '--force-color-profile=srgb '
        '--remote-debugging-port=' + str(port) + ' ' + udd
    )


def test_stuck_kernel_is_matched():
    assert is_wenmai_kernel(
        _kernel_cmdline(2030, '100000000001'),
        _KERNEL_EXE,
        2030,
        '100000000001',
    )


def test_foreign_app_with_the_same_port_is_not_matched():
    """W2's incident class: another vendor's kernel (or a debug Chrome)
    holding OUR port carries the same debug-port flag — the kill must
    not touch it."""
    assert not is_wenmai_kernel(
        _kernel_cmdline(2030, '100000000001'),
        _FOREIGN_EXE,
        2030,
        '100000000001',
    )


def test_wenmai_kernel_of_another_store_is_not_matched():
    """A 稳卖 kernel of a DIFFERENT env is a production store browser
    of the same vendor — still not ours to kill."""
    assert not is_wenmai_kernel(
        _kernel_cmdline(2030, '100000000002'),
        _KERNEL_EXE,
        2030,
        '100000000001',
    )
    # And a near-miss env id (a superstring, not the same id) is not
    # matched either.
    assert not is_wenmai_kernel(
        _kernel_cmdline(2030, '1100000000010'),
        _KERNEL_EXE,
        2030,
        '100000000001',
    )


def test_wrong_port_or_missing_pieces_are_not_matched():
    assert not is_wenmai_kernel(
        _kernel_cmdline(9999, '100000000001'),
        _KERNEL_EXE,
        2030,
        '100000000001',
    )
    # No exe path at all (a protected system process): never matched.
    assert not is_wenmai_kernel(
        _kernel_cmdline(2030, '100000000001'), '', 2030, '100000000001'
    )
    assert not is_wenmai_kernel('', _KERNEL_EXE, 2030, '100000000001')


def test_kill_identifies_nothing_without_a_confirmed_match(monkeypatch):
    """When the port flag's owner is NOT our kernel, kill NOTHING and
    report False — the caller surfaces the safe close-it-in-the-client
    hint instead of guessing."""
    kills = []

    def fake_run(script, *a, **kw):
        if 'ConvertTo-Json' in script:
            foreign = {
                'ProcessId': 123,
                'ExecutablePath': _FOREIGN_EXE,
                'CommandLine': _kernel_cmdline(2030, '100000000001'),
            }
            return json.dumps([foreign])
        kills.append(script)
        return 'ok'

    monkeypatch.setattr(win_mod, '_run_ps', fake_run)
    assert (
        win_mod.WenmaiProcessLayer().kill_kernel_by_debug_port(
            2030, '100000000001'
        )
        is False
    )
    assert kills == []  # no Stop-Process was ever issued


def test_kill_stops_only_the_confirmed_kernel(monkeypatch):
    kills = []

    def fake_run(script, *a, **kw):
        if 'ConvertTo-Json' in script:
            ours = {
                'ProcessId': 123,
                'ExecutablePath': _KERNEL_EXE,
                'CommandLine': _kernel_cmdline(2030, '100000000001'),
            }
            foreign = {
                'ProcessId': 777,
                'ExecutablePath': _FOREIGN_EXE,
                'CommandLine': _kernel_cmdline(2030, '100000000001'),
            }
            return json.dumps([ours, foreign])
        kills.append(script)
        return 'ok'

    monkeypatch.setattr(win_mod, '_run_ps', fake_run)
    assert (
        win_mod.WenmaiProcessLayer().kill_kernel_by_debug_port(
            2030, '100000000001'
        )
        is True
    )
    assert len(kills) == 1 and 'Stop-Process -Id 123' in kills[0]
