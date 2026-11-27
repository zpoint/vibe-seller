"""The AdsPower Windows layer: port file, kernel evidence, scripts.

The PowerShell scripts are pinned as COMPLETE strings (a substring
check cannot notice a missing brace); the local_api file parsing and
the kernel-process attribution are exercised on real file/CLI shapes
recorded live (2026-11-27, client v8.9.23).
"""

from pathlib import Path

import pytest

from app.browser.adapters.adspower_common import KernelProcess
import app.browser.adapters.adspower_win as win_mod
from app.browser.adapters.adspower_win import (
    ADSPOWER_SPEC,
    AdsPowerProcessLayer,
    read_local_api_port,
)

pytestmark = pytest.mark.unit


# ── the local_api file (D2) ─────────────────────────────────────────


def _write_local_api(monkeypatch, tmp_path: Path, text: str) -> None:
    f = tmp_path / 'local_api'
    f.write_text(text, encoding='utf-8')
    monkeypatch.setattr(
        win_mod, 'local_api_file_path', lambda: r'C:\fake\local_api'
    )
    monkeypatch.setattr(
        win_mod.win_locator,
        'win_path_to_local',
        lambda _p: str(f),
    )


def test_read_port_parses_the_file_url(monkeypatch, tmp_path):
    # The exact line seen live (FACTS.md §a).
    _write_local_api(
        monkeypatch,
        tmp_path,
        'http://local.adspower.com:51234/',
    )
    assert read_local_api_port() == 51234


def test_read_port_missing_file_is_none(monkeypatch, tmp_path):
    _write_local_api(monkeypatch, tmp_path, 'http://local.adspower.com:51234/')
    monkeypatch.setattr(
        win_mod.win_locator,
        'win_path_to_local',
        lambda _p: str(tmp_path / 'nope'),
    )
    assert read_local_api_port() is None


def test_read_port_garbage_is_none(monkeypatch, tmp_path):
    _write_local_api(monkeypatch, tmp_path, 'not a url')
    assert read_local_api_port() is None


def test_read_port_negative_control_real_default(monkeypatch, tmp_path):
    # A different port in the file must produce a different answer
    # (pins that the port is really READ, not remembered).
    _write_local_api(monkeypatch, tmp_path, 'http://local.adspower.com:53413/')
    assert read_local_api_port() == 53413


# ── kernel process evidence (D7 / m6) ──────────────────────────────


def _ps_output(pid: int, name: str, data_dir: str) -> str:
    cmdline = (
        rf'"C:\x\{name}" --user-data-dir={data_dir} --no-sandbox '
        '--remote-debugging-port=0'
    )
    return f'{pid}\t{name}\t{cmdline}'


def test_list_kernel_processes_parses_cim_lines(monkeypatch):
    # Both live spellings: SunBrowser names the profile in the cache
    # dir; FlowerBrowser adds the _flower suffix (FACTS.md §m6).
    out = '\n'.join([
        _ps_output(11, 'SunBrowser.exe', r'C:\.ADSPOWER_GLOBAL\cache\k1_a1'),
        _ps_output(
            12,
            'flowerbrowser.exe',
            r'C:\.ADSPOWER_GLOBAL\cache\k2_a2_flower',
        ),
        '13\tchrome.exe\t--user-data-dir=C:\\other',  # not a kernel
        'garbage line',
    ])
    monkeypatch.setattr(win_mod, '_run_ps', lambda *a, **k: out)
    kernels = win_mod.list_kernel_processes()
    assert [(k.pid, k.is_firefox) for k in kernels] == [
        (11, False),
        (12, True),
    ]
    assert kernels[1].user_data_dir.endswith('k2_a2_flower')


def test_list_kernel_processes_negative_control_no_dir(monkeypatch):
    # A kernel without a user-data-dir cannot be attributed → skipped
    # (never killed blindly).
    monkeypatch.setattr(
        win_mod, '_run_ps', lambda *a, **k: '20\tSunBrowser.exe\t--x'
    )
    assert win_mod.list_kernel_processes() == []


def test_kill_process_tree_targets_one_pid(monkeypatch):
    # NEVER a whole image name (other profiles may belong to other
    # tasks or to the user) — always /T /F on ONE pid.
    seen = []
    monkeypatch.setattr(
        win_mod, '_run_ps', lambda s, **k: seen.append(s) or 'SUCCESS: x'
    )
    assert win_mod.kill_process_tree(4321) is True
    assert seen == ['taskkill /T /F /PID 4321']


# ── shell + launching ──────────────────────────────────────────────


def test_shell_running_counts_processes(monkeypatch):
    monkeypatch.setattr(win_mod, '_run_ps', lambda *a, **k: '7')
    assert AdsPowerProcessLayer().shell_running() is True


def test_shell_running_false_on_zero(monkeypatch):
    monkeypatch.setattr(win_mod, '_run_ps', lambda *a, **k: '0')
    assert AdsPowerProcessLayer().shell_running() is False


def test_launch_script_uses_the_located_exe(monkeypatch):
    scripts = []
    monkeypatch.setattr(
        win_mod, '_run_ps', lambda s, **k: scripts.append(s) or 'ok'
    )
    layer = AdsPowerProcessLayer()
    monkeypatch.setattr(
        layer,
        'exe_path',
        lambda: r'C:\Program Files\AdsPower Global\AdsPower Global.exe',
    )
    assert layer.launch() is True
    assert scripts == [
        "Start-Process -FilePath 'C:\\Program Files\\AdsPower Global"
        "\\AdsPower Global.exe';'ok'"
    ]


def test_headless_script_carries_documented_flags(monkeypatch):
    # The vendor's documented flags only — no --api-port (the port is
    # discovered through the local_api file; 50325 may be reserved).
    # R2: the key itself is NOT in the script; it arrives through the
    # environment (the client tests pin the logging contract).
    seen = []
    monkeypatch.setattr(
        win_mod,
        '_run_ps_env',
        lambda s, env: seen.append((s, env)) or 'ok',
    )
    layer = AdsPowerProcessLayer()
    monkeypatch.setattr(
        layer,
        'exe_path',
        lambda: (
            r'C:\Program Files\AdsPower Global'
            r'\AdsPower Global.exe'
        ),
    )
    assert layer.launch_headless('k3') is True
    ((script, env),) = seen
    assert "'--headless=true'" in script
    assert '$env:VS_ADSPOWER_KEY' in script
    assert 'k3' not in script
    assert env['VS_ADSPOWER_KEY'] == 'k3'
    assert '--api-port' not in script
    assert '-WindowStyle Hidden' in script


def test_headless_negative_control_no_key(monkeypatch):
    monkeypatch.setattr(win_mod, '_run_ps', lambda *a, **k: 'ok')
    layer = AdsPowerProcessLayer()
    monkeypatch.setattr(layer, 'exe_path', lambda: r'C:\x\e.exe')
    assert layer.launch_headless('') is False


# ── the descriptor (R1: vendor knowledge lives here) ───────────────


def test_spec_names_the_global_exe():
    assert ADSPOWER_SPEC.exe_names == ('AdsPower Global.exe',)
    assert ADSPOWER_SPEC.dir_names == ('AdsPower Global',)
    assert ADSPOWER_SPEC.key == 'adspower'


def test_kernel_process_firefox_flag():
    assert KernelProcess(1, 'FlowerBrowser.exe', 'd').is_firefox is True
    assert KernelProcess(2, 'SunBrowser.exe', 'd').is_firefox is False


# ── R6: APPDATA resolution is cached ───────────────────────────────


def test_windows_appdata_cached_after_first_success(monkeypatch):
    monkeypatch.setattr(win_mod, '_APPDATA_CACHE', {})
    calls = []
    monkeypatch.setattr(win_mod.os, 'name', 'posix')
    monkeypatch.setattr(
        win_mod,
        '_run_ps',
        lambda s, **k: calls.append(s) or r'C:\Users\x\AppData\Roaming',
    )
    assert win_mod.windows_appdata() == r'C:\Users\x\AppData\Roaming'
    assert win_mod.windows_appdata() == r'C:\Users\x\AppData\Roaming'
    assert len(calls) == 1  # the second call hit the cache


def test_windows_appdata_failure_is_not_cached(monkeypatch):
    monkeypatch.setattr(win_mod, '_APPDATA_CACHE', {})
    monkeypatch.setattr(win_mod.os, 'name', 'posix')
    results = ['', r'C:\Users\x\AppData\Roaming']
    monkeypatch.setattr(win_mod, '_run_ps', lambda s, **k: results.pop(0))
    assert win_mod.windows_appdata() == ''  # failure…
    assert (
        win_mod.windows_appdata() == r'C:\Users\x\AppData\Roaming'
    )  # …is retried, then cached
    assert win_mod._APPDATA_CACHE['v'] == (r'C:\Users\x\AppData\Roaming')
