"""Every PowerShell script the Windows clients generate, on a real engine.

Substring checks cannot tell a well-formed script from a syntax error,
so each script is

1. parsed by the engine's own parser (``Parser::ParseInput``), and
2. executed — the process queries over a fake ``Win32_Process`` table,
   the shortcut-folder query as it is — through the invocation the
   product uses (UTF-8 prologue, one ``-Command`` argument), and parsed
   on the way back by the product's own code.

This proves syntax and logic, not Windows; platform behaviour is
covered by test_win_app_locator_windows.py.

Runs wherever PowerShell exists (all GitHub runner images ship it):
``powershell.exe`` on Windows — the engine the product launches — and
``pwsh`` elsewhere. Skips on a machine without any.
"""

import shutil
import subprocess
import sys
from unittest import mock

import pytest

from app.browser import win_app_locator as wal
import app.browser.adapters.yuehulian_win as yw

if sys.platform == 'win32':
    PWSH = shutil.which('powershell.exe') or shutil.which('pwsh')
else:
    PWSH = shutil.which('pwsh') or shutil.which('powershell')

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(PWSH is None, reason='no PowerShell on this machine'),
]

_TIMEOUT_S = 120


def _run(script: str) -> subprocess.CompletedProcess:
    """Invoke the engine exactly the way the product's runner does.

    Unlike the product runner (which answers '' on failure), a failing
    script fails the test with PowerShell's own stderr.
    """
    proc = subprocess.run(
        [PWSH, '-NoProfile', '-NonInteractive', '-Command', wal._UTF8 + script],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, (
        f'powershell rc={proc.returncode}\nscript: {script}\n'
        f'stderr: {proc.stderr}'
    )
    return proc


# ---- 1. every generated script parses -----------------------------------


def _generated() -> list[tuple[str, str]]:
    """Every script this code hands to PowerShell, captured verbatim."""
    out: list[tuple[str, str]] = []

    def record(name):
        def runner(script, *_a, **_k):
            out.append((name, script))
            return ''

        return runner

    with mock.patch.object(
        wal, 'run_powershell', record('locator: shortcut folders')
    ):
        wal._default_shortcut_targets()
    with mock.patch.object(
        wal, 'run_powershell', record('locator: App Paths (WSL)')
    ):
        wal._ps_app_paths()
    with mock.patch.object(
        wal, 'run_powershell', record('locator: Uninstall (WSL)')
    ):
        wal._ps_uninstall_entries()
    custom = yw.ProcessLayer(r'D:\悦互联')
    with mock.patch.object(yw, '_run_ps', record('yuehulian: is_running')):
        custom.is_running()
    with mock.patch.object(yw, '_run_ps', record('yuehulian: main_pid')):
        custom.main_pid()
    with mock.patch.object(
        yw, '_run_ps', record('yuehulian: kernel_user_data_dirs')
    ):
        custom.kernel_user_data_dirs()
    with mock.patch.object(yw, '_run_ps', record('yuehulian: launch')):
        custom.launch()
    out.append(('yuehulian: exe_path_script', custom.exe_path_script()))
    return out


_SCRIPTS = [
    'locator: shortcut folders',
    'locator: App Paths (WSL)',
    'locator: Uninstall (WSL)',
    'yuehulian: is_running',
    'yuehulian: main_pid',
    'yuehulian: kernel_user_data_dirs',
    'yuehulian: launch',
    'yuehulian: exe_path_script',
]


def _parse_errors(script: str) -> list[str]:
    """The engine's parse errors for ``script`` (empty when it parses)."""
    literal = "'" + (wal._UTF8 + script).replace("'", "''") + "'"
    proc = _run(
        '$e=$null;$t=$null;[void][System.Management.Automation.Language'
        '.Parser]::ParseInput('
        f'{literal},[ref]$t,[ref]$e);'
        '"ERRORS=$($e.Count)";'
        '$e|ForEach-Object{"L$($_.Extent.StartLineNumber):'
        '$($_.Extent.StartColumnNumber) $($_.Message)"}'
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines and lines[0].startswith('ERRORS='), proc.stdout
    return [] if lines[0] == 'ERRORS=0' else lines[1:]


def test_every_generated_script_parses():
    scripts = _generated()
    # A script added to the product without a capture here is untested.
    assert [name for name, _ in scripts] == _SCRIPTS
    broken = {
        name: errs
        for name, script in scripts
        if (errs := _parse_errors(script))
    }
    assert not broken, broken


# ---- 2. the process queries, executed over a fake process table ---------


_SOURCE = 'Get-CimInstance Win32_Process'  # what the product scripts ask

# The base table deliberately holds NO process whose path spells
# 'YueHuLian': that legacy name arm is tested by adding a row explicitly.
# Rows: the client in a Chinese install dir; its kernels (one --type=,
# the kernel root without); an elevated process whose path cannot be
# read; a --type= helper; an unrelated process; a dir differing from
# the queried one only in letter case; a dir holding only a kernel.
_BASE_PROCS = r"""
[pscustomobject]@{ProcessId=11;Name='用户端.exe';ExecutablePath='D:\悦互联\用户端.exe';CommandLine='"D:\悦互联\用户端.exe"'},
[pscustomobject]@{ProcessId=12;Name='chrome.exe';ExecutablePath='D:\悦互联\resources\chrome\chrome.exe';CommandLine='"D:\悦互联\resources\chrome\chrome.exe" --type=gpu-process --user-data-dir="D:\悦互联\YueHuLianData\p1"'},
[pscustomobject]@{ProcessId=13;Name='chrome.exe';ExecutablePath='D:\悦互联\resources\chrome\chrome.exe';CommandLine='"D:\悦互联\resources\chrome\chrome.exe" --user-data-dir="D:\悦互联\YueHuLianData\p2"'},
[pscustomobject]@{ProcessId=14;Name='elevated.exe';ExecutablePath=$null;CommandLine=$null},
[pscustomobject]@{ProcessId=15;Name='helper.exe';ExecutablePath='D:\悦互联\helper.exe';CommandLine='D:\悦互联\helper.exe --type=utility'},
[pscustomobject]@{ProcessId=16;Name='unrelated.exe';ExecutablePath='C:\Windows\System32\cmd.exe';CommandLine=$null},
[pscustomobject]@{ProcessId=18;Name='elsewhere.exe';ExecutablePath='E:\其它\elsewhere.exe';CommandLine='y'},
[pscustomobject]@{ProcessId=19;Name='mixed.exe';ExecutablePath='e:\program files\client dir\用户端.exe';CommandLine='z'},
[pscustomobject]@{ProcessId=20;Name='chrome.exe';ExecutablePath='E:\Kernels\chrome.exe';CommandLine='E:\Kernels\chrome.exe'},
[pscustomobject]@{ProcessId=21;Name='sub.exe';ExecutablePath='E:\Helpers\sub.exe';CommandLine='E:\Helpers\sub.exe --type=utility'}
"""
# A client in the pre-locator default folder: matched by NAME only.
_DEFAULT_DIR_PROC = (
    "[pscustomobject]@{ProcessId=17;Name='old.exe';"
    r"ExecutablePath='C:\Program Files (x86)\YueHuLian\old.exe';"
    "CommandLine='x'},"
)


def _call(method: str, install_dir: str | None, *, default_dir=False):
    """Run a ProcessLayer method with ``Win32_Process`` replaced by fakes.

    The script is generated by the product, executed by the engine and
    parsed by the product. Returns ``(the method's result, raw stdout)``
    so a test can pin exactly WHICH fake processes matched.
    """
    layer = yw.ProcessLayer(install_dir)
    # The table closes inline: a newline between ')' and the product's
    # ' | Where-Object' would end the statement ("empty pipe element").
    table = (
        '@(\n'
        + (_DEFAULT_DIR_PROC if default_dir else '')
        + _BASE_PROCS.strip('\n')
        + ')'
    )
    seen = {}

    def runner(script, *_a, **_k):
        # The rewrite must hit; if the product's query source changes,
        # the REAL process table would answer — fail, never fall back.
        assert _SOURCE in script, script
        proc = _run(script.replace(_SOURCE, table, 1))
        seen['out'] = proc.stdout
        return proc.stdout

    with (
        mock.patch.object(yw, '_run_ps', runner),
        mock.patch.object(yw.win_locator, 'locate', return_value=None),
    ):
        return getattr(layer, method)(), seen['out'].strip()


def test_is_running_counts_the_whole_custom_install():
    """``D:\\悦互联`` holds the client and a helper (by path prefix);
    its two chrome.exe kernels are not the client."""
    assert _call('is_running', r'D:\悦互联') == (True, '2')


def test_is_running_adds_the_legacy_name_to_the_located_dir():
    """Both arms together: prefix (pids 11, 15) + name (pid 17)."""
    assert _call('is_running', r'D:\悦互联', default_dir=True) == (True, '3')


def test_is_running_prefix_is_case_insensitive():
    # The dir carries no 'YueHuLian' token: ONLY the prefix can match.
    assert _call('is_running', r'E:\Program Files\CLIENT Dir') == (True, '1')


def test_is_running_ignores_kernels_and_null_paths():
    """Only a kernel lives here: not running. The elevated process with
    no readable path (pid 14) never breaks the query."""
    assert _call('is_running', r'E:\Kernels') == (False, '0')


def test_is_running_false_for_an_unrelated_dir():
    assert _call('is_running', r'E:\不相关') == (False, '0')


def test_is_running_false_when_not_located_and_nothing_runs():
    assert _call('is_running', None) == (False, '0')


def test_is_running_falls_back_to_the_name_when_not_located():
    """Before any launch nothing may be located yet (pre-launch poll):
    the default-folder client is still seen — by its name."""
    assert _call('is_running', None, default_dir=True) == (True, '1')


def test_a_drive_root_is_never_a_prefix():
    """Located at ``D:\\`` the prefix would match the whole drive, so
    only the name arm applies (pid 17, not 11/12/13/15 on D:)."""
    assert _call('is_running', 'D:\\', default_dir=True) == (True, '1')


def test_main_pid_is_the_electron_main_of_a_custom_install():
    assert _call('main_pid', r'D:\悦互联') == (11, '11')


def test_main_pid_skips_type_helpers_and_kernels():
    # A --type= helper is not the main …
    assert _call('main_pid', r'E:\Helpers') == (None, '')
    # … nor is the kernel's root chrome.exe, which has no --type=.
    assert _call('main_pid', r'E:\Kernels') == (None, '')


def test_main_pid_none_when_not_located_and_nothing_runs():
    assert _call('main_pid', None) == (None, '')


def test_main_pid_falls_back_to_the_name_when_not_located():
    assert _call('main_pid', None, default_dir=True) == (17, '17')


def test_kernel_user_data_dirs_from_a_custom_install():
    """Kernels are found by their fixed YueHuLianData dir name, wherever
    the client is installed."""
    assert _call('kernel_user_data_dirs', None) == (
        [r'D:\悦互联\YueHuLianData\p1', r'D:\悦互联\YueHuLianData\p2'],
        '\n'.join([
            r'D:\悦互联\YueHuLianData\p1',
            r'D:\悦互联\YueHuLianData\p2',
        ]),
    )


# ---- 3. the shortcut folders, asked from the shell on a real engine -------


def test_known_folders_script_runs_on_the_shell():
    """The one PowerShell call left in the shortcut hint asks the shell
    for the four shortcut folders. Run for real: the answer is at most
    those four, with no blank and no repeat (an unregistered folder
    answers '' and is dropped). And it is the ONLY thing it does — a
    shortcut is never touched through COM."""
    proc = _run(wal._KNOWN_FOLDERS_PS)
    folders = proc.stdout.splitlines()
    assert len(folders) <= 4
    assert all(folders)
    assert len(set(folders)) == len(folders)
    assert 'WScript' not in wal._KNOWN_FOLDERS_PS
