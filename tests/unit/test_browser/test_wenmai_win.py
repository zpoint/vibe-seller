"""The wenmai Windows process layer: locator spec + PowerShell scripts.

The scripts are pinned as COMPLETE strings (a substring check cannot
notice a missing brace); behaviour under a real engine is covered by
test_powershell_scripts.py's harness and the live e2e.
"""

import pytest

import app.browser.adapters.wenmai_win as win_mod
from app.browser.adapters.wenmai_win import WENMAI_SPEC, WenmaiProcessLayer
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
    assert calls['spec'] is WENMAI_SPEC
    assert calls['configured'] == r'D:\custom'


def test_wenmai_spec_is_declared_in_the_vendor_module():
    # R1: vendor knowledge lives in the vendor module, not the shared
    # locator. The spec matches the real install (exe name + tokens).
    assert WENMAI_SPEC.key == 'wenmai'
    assert 'wenmaiaibrowser.exe' in WENMAI_SPEC.exe_names
    assert '稳卖' in WENMAI_SPEC.match_tokens
    # And the shared locator module no longer carries it.
    assert not hasattr(locator, 'WENMAI_SPEC')
