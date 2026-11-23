"""Real-Windows locator tests (windows CI matrix; skip elsewhere).

Builds a FAKE install at a non-default location with a Chinese dir
name (``D:\\紫鸟测试\\ziniao\\ziniao.exe``, GitHub windows runners have
D:) and proves EACH hint separately finds it — plus the decoy cases
the old per-app lookups got wrong. Nothing here touches a real Ziniao
or 悦互联 install: registry keys and shortcuts are created per test
and removed in teardown (a pre-existing real ``App Paths\\ziniao.exe``
key skips its test rather than being deleted). The not-found and
hint-order assertions assume NO real Ziniao / 悦互联 install exists on
the machine — true for CI runners.

The runner is an English Windows, whose code page cannot write 紫鸟 —
``WScript.Shell`` refuses such paths — so shortcuts are written through
IShellLinkW (``windows_shortcut.cs``).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

from app.browser import lnk_file, win_app_locator as wal
from app.browser.adapters.yuehulian_win import ProcessLayer
from tests.unit.test_browser import scratch_dir as sd

try:
    import winreg  # noqa: PLC0415
except ImportError:
    winreg = None  # type: ignore[assignment]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform != 'win32', reason='Windows only'),
]

_UNINSTALL_BASE = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
_APP_PATHS_ZINIAO = (
    r'Software\Microsoft\Windows\CurrentVersion\App Paths\ziniao.exe'
)
_SYSTEM32 = os.path.join(
    os.environ.get('SystemRoot', r'C:\Windows'), 'System32'
)
_TINY_EXE = os.path.join(_SYSTEM32, 'where.exe')


def _ps_scalar(script: str) -> str:
    """Run a PowerShell snippet, return its stdout; a failure fails the
    test with the engine's stderr."""
    proc = subprocess.run(
        [
            'powershell.exe',
            '-NoProfile',
            '-Command',
            '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;' + script,
        ],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, (
        f'powershell rc={proc.returncode}\nscript: {script}\n'
        f'stdout: {proc.stdout}\nstderr: {proc.stderr}'
    )
    return proc.stdout.strip()


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


# Compiled with Add-Type at first use; see the file for why WScript.Shell
# cannot write the shortcuts these tests need.
_SHORTCUT_CS = os.path.join(os.path.dirname(__file__), 'windows_shortcut.cs')


def _load_shortcut_writer() -> str:
    """The PowerShell prologue that makes ``VibetestShortcut`` available."""
    return 'Add-Type -Path ' + _ps_quote(_SHORTCUT_CS) + ';'


def _make_lnk(lnk_path: str, target: str) -> None:
    """Write a real shortcut with IShellLinkW, as an installer does."""
    _ps_scalar(
        _load_shortcut_writer()
        + '[VibetestShortcut]::Create('
        + _ps_quote(lnk_path)
        + ','
        + _ps_quote(target)
        + ')'
    )
    assert os.path.isfile(lnk_path), f'{lnk_path} was not written'


def _shell_target(lnk_path: str) -> str:
    """What the SHELL says this shortcut points at (IShellLinkW.GetPath)."""
    return _ps_scalar(
        _load_shortcut_writer()
        + '[VibetestShortcut]::ReadTarget('
        + _ps_quote(lnk_path)
        + ')'
    )


def _hexdump(path: str) -> str:
    with open(path, 'rb') as f:
        data = f.read()
    rows = [
        f'{i:04x}  ' + ' '.join(f'{b:02x}' for b in data[i : i + 16])
        for i in range(0, min(len(data), 512), 16)
    ]
    return f'{len(data)} bytes\n' + '\n'.join(rows)


def _write_hkcu(path: str, values: dict[str, str]) -> None:
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    try:
        for name, val in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
    finally:
        key.Close()


def _delete_hkcu(path: str) -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumValue(key, i)[0])
                except OSError:
                    break
                i += 1
            for name in names:
                winreg.DeleteValue(key, name)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _known_folder(name: str) -> str:
    """Where Windows keeps a known folder — the SAME API the product's
    shortcut scan uses, so a shortcut lands exactly where it looks."""
    return _ps_scalar('[Environment]::GetFolderPath(' + _ps_quote(name) + ')')


def _tiny_exe_bytes() -> bytes:
    """A small real Windows executable (its content is never run here)."""
    with open(_TINY_EXE, 'rb') as f:
        return f.read()


def _fill(size: int) -> bytes:
    """``size`` bytes with an MZ header — size is all the locator weighs."""
    return b'MZ' + bytes(max(size - 2, 0))


_ROOT: str | None = None  # the test root, chosen once
_ROOT_IS_OURS = False  # no drive was usable, so it was made here


def _writable_root() -> str:
    """The root every fake install lives under, chosen once.

    A writable fixed drive's root (D: on GitHub runners), else a private
    prefixed directory under the temp dir — private so that "nothing
    directly under the root disappears" holds there too.
    """
    global _ROOT, _ROOT_IS_OURS
    if _ROOT is not None:
        return _ROOT
    for root in ('D:\\', 'E:\\'):
        probe = os.path.join(root, sd.PREFIX + 'probe')
        if os.path.isdir(root):
            try:
                os.makedirs(probe, exist_ok=True)
                os.rmdir(probe)
                _ROOT = root
                return _ROOT
            except OSError:
                continue
    _ROOT = os.path.join(
        tempfile.gettempdir(), sd.PREFIX + 'root-' + uuid.uuid4().hex[:8]
    )
    os.makedirs(_ROOT, exist_ok=False)
    _ROOT_IS_OURS = True
    return _ROOT


@pytest.fixture(scope='module', autouse=True)
def _test_root():
    """Pick the root before any test needs it and, when it was made
    here because no drive was usable, take it back at the end."""
    root = _writable_root()
    yield root
    if _ROOT_IS_OURS:
        sd.remove_scratch_dir(root)  # through the guard, like any other


@pytest.fixture(autouse=True)
def _clean_state():
    wal.clear_locator_cache()
    yield
    wal.clear_locator_cache()


@pytest.fixture(autouse=True)
def _test_root_untouched():
    """Net under ``scratch_dir``: directly under the test root a test may
    remove nothing that was there before it and leave nothing but a
    scratch directory. The test that does fails by name."""
    root = _writable_root()
    before = set(os.listdir(root))
    yield
    # A root that is gone altogether had everything removed from it.
    after = set(os.listdir(root)) if os.path.isdir(root) else set()
    assert not before - after, (
        f'this test REMOVED directly under {root}: {sorted(before - after)}'
    )
    left = {name for name in after - before if not name.startswith(sd.PREFIX)}
    assert not left, f'this test left directly under {root}: {sorted(left)}'


@pytest.fixture(scope='module')
def scratch() -> sd.ScratchDir:
    """Every fake install lives under ONE scratch directory: the only one
    these tests remove recursively."""
    area = sd.ScratchDir(_writable_root())
    yield area
    area.remove()


@pytest.fixture
def shortcut_spot():
    """Place a real ``.lnk`` in a known folder the product scans.

    The folder comes from ``[Environment]::GetFolderPath`` — the API the
    product uses. A missing one is created and taken back afterwards if
    still empty (``CreatedDirs``). Every shortcut written here is deleted
    afterwards, and one that already exists is never overwritten (the
    Desktop may hold the user's own).
    """
    made_dirs = sd.CreatedDirs()
    written: list[str] = []

    def place(folder: str, relative: str, target: str) -> str:
        root = _known_folder(folder)
        assert root, f'{folder}: no known folder on this machine'
        lnk = os.path.join(root, relative)
        assert not os.path.exists(lnk), (
            f'{lnk} already exists: never overwrite a shortcut that is not ours'
        )
        made_dirs.ensure(os.path.dirname(lnk))
        _make_lnk(lnk, target)
        written.append(lnk)
        return lnk

    yield place
    for lnk in written:
        if os.path.exists(lnk):
            os.remove(lnk)
    made_dirs.remove()


@pytest.fixture(scope='module')
def fake_ziniao_install(scratch) -> str:
    """A fake install at <scratch>\\紫鸟测试\\ziniao\\ziniao.exe (Chinese,
    non-default, NOT under any conventional root)."""
    return scratch.file(
        '紫鸟测试', 'ziniao', 'ziniao.exe', content=_tiny_exe_bytes()
    )


def test_finds_via_desktop_shortcut_only(fake_ziniao_install, shortcut_spot):
    """(a) The user's main hint: a Desktop .lnk pointing at a custom
    install — invisible to registry/drive hints."""
    lnk = shortcut_spot('Desktop', '紫鸟测试.lnk', fake_ziniao_install)
    hit = wal.locate(wal.ZINIAO_SPEC)
    assert hit is not None
    assert hit.hint == 'shortcut'
    assert hit.exe_path.lower() == fake_ziniao_install.lower()
    assert hit.detail == '紫鸟测试.lnk'
    assert os.path.isfile(lnk)  # found through a real shortcut file


def test_reader_agrees_with_the_shell_on_a_chinese_shortcut(
    fake_ziniao_install, shortcut_spot
):
    """The reader must give the SHELL's own answer for a real file
    Windows wrote: a Chinese-named link to a Chinese install path,
    across drives. Should Windows write a layout the reader does not
    know, the file's bytes are in the failure message."""
    lnk = shortcut_spot(
        'Desktop', 'vibetest-紫鸟浏览器.lnk', fake_ziniao_install
    )
    with open(lnk, 'rb') as f:
        data = f.read()
    shell = _shell_target(lnk)
    assert shell.lower() == fake_ziniao_install.lower(), _hexdump(lnk)

    ours = lnk_file.read_target(data, os.path.dirname(lnk))

    assert ours is not None, _hexdump(lnk)
    assert ours.lower() == shell.lower(), (
        f'reader: {ours!r}\nshell:  {shell!r}\n' + _hexdump(lnk)
    )


def test_reader_agrees_with_the_shell_on_an_ascii_shortcut(
    shortcut_spot, scratch
):
    """Same for an all-ASCII target: Windows then writes only the ANSI
    copy of the path (the layout a Windows 8.1+ link carries)."""
    exe = scratch.file(
        'vibetest-ascii', 'client.exe', content=_tiny_exe_bytes()
    )
    lnk = shortcut_spot('Desktop', 'vibetest-ascii.lnk', exe)
    with open(lnk, 'rb') as f:
        data = f.read()
    shell = _shell_target(lnk)
    ours = lnk_file.read_target(data, os.path.dirname(lnk))
    assert ours is not None, _hexdump(lnk)
    assert ours.lower() == shell.lower() == exe.lower(), (
        f'reader: {ours!r}\nshell:  {shell!r}\n' + _hexdump(lnk)
    )


def test_finds_via_start_menu_shortcut_only(fake_ziniao_install, shortcut_spot):
    """Same hint in the Start Menu, one folder down: the user Programs
    root is scanned recursively."""
    shortcut_spot('Programs', 'vibetest-ziniao\\紫鸟.lnk', fake_ziniao_install)
    hit = wal.locate(wal.ZINIAO_SPEC)
    assert hit is not None
    assert hit.hint == 'shortcut'
    assert hit.exe_path.lower() == fake_ziniao_install.lower()


def test_finds_via_hkcu_uninstall_install_location(fake_ziniao_install):
    """(b) Only an HKCU Uninstall entry with InstallLocation."""
    sub = _UNINSTALL_BASE + '\\vibetest-' + uuid.uuid4().hex[:8]
    install_dir = os.path.dirname(fake_ziniao_install)
    _write_hkcu(
        sub,
        {
            'DisplayName': '紫鸟测试',
            'InstallLocation': install_dir + '\\',
        },
    )
    try:
        hit = wal.locate(wal.ZINIAO_SPEC)
        assert hit is not None
        assert hit.hint == 'uninstall'
        assert hit.exe_path.lower() == fake_ziniao_install.lower()
    finally:
        _delete_hkcu(sub)


def test_finds_via_hkcu_app_paths(fake_ziniao_install):
    """(c) Only an HKCU App Paths\\ziniao.exe default value. Skips when
    the machine has a real one — teardown must never delete it."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_PATHS_ZINIAO):
            pytest.skip('real ziniao.exe App Paths key present')
    except OSError:
        pass
    _write_hkcu(_APP_PATHS_ZINIAO, {'': fake_ziniao_install})
    try:
        hit = wal.locate(wal.ZINIAO_SPEC)
        assert hit is not None
        assert hit.hint == 'app_paths'
        assert hit.exe_path.lower() == fake_ziniao_install.lower()
    finally:
        _delete_hkcu(_APP_PATHS_ZINIAO)


def test_finds_via_conventional_dir_on_other_drive():
    """(d) A plain install under D:\\Program Files\\ziniao — or the
    per-user Programs dir when no writable second drive exists.

    Both are places a REAL install may occupy. A path that already
    exists is left untouched and this test skips: it never overwrites
    or removes a file it did not create.
    """
    made = sd.CreatedDirs()
    exe = None
    for cand in (
        r'D:\Program Files\ziniao\ziniao.exe',
        os.path.join(
            os.environ.get('LOCALAPPDATA', 'x'),
            'Programs',
            'ziniao',
            'ziniao.exe',
        ),
    ):
        if os.path.exists(cand):
            pytest.skip(f'{cand} already exists: possibly a real install')
        try:
            made.ensure(os.path.dirname(cand))
            with open(cand, 'wb') as f:
                f.write(_tiny_exe_bytes())
        except OSError:
            continue
        exe = cand
        break
    if exe is None:
        pytest.skip('no writable conventional root on this machine')
    try:
        hit = wal.locate(wal.ZINIAO_SPEC)
        assert hit is not None
        assert hit.hint == 'conventional'
        assert hit.exe_path.lower() == exe.lower()
    finally:
        os.remove(exe)
        made.remove()  # only what this test created, only while empty


def test_decoy_uninstaller_with_ziniao_in_path_rejected(scratch):
    """(e) A DisplayIcon that IS an uninstaller whose path merely
    contains 'ziniao' must not be returned (the old bug)."""
    decoy_dir = scratch.mkdir('ziniao-decoy-紫鸟')
    decoy = os.path.join(decoy_dir, 'uninst.exe')
    with open(decoy, 'wb') as f:
        f.write(_tiny_exe_bytes())
    sub = _UNINSTALL_BASE + '\\vibetest-' + uuid.uuid4().hex[:8]
    _write_hkcu(
        sub,
        {
            'DisplayName': '紫鸟测试',
            'DisplayIcon': decoy + ',0',
            'InstallLocation': decoy_dir + '\\',
        },
    )
    try:
        assert wal.locate(wal.ZINIAO_SPEC) is None
        assert 'Could not find ziniao.exe' in wal.not_found_message(
            wal.ZINIAO_SPEC
        )
    finally:
        _delete_hkcu(sub)


@pytest.fixture(scope='module')
def fake_yuehulian_install(scratch) -> str:
    """A fake 悦互联 install whose main exe has a Chinese name, with a
    kernel decoy under resources\\ and an uninstaller decoy.

    The main exe is a real executable: the shell refuses to make a
    shortcut to a zero-filled one. The decoys are never pointed at.
    """
    base = '悦互联测试'
    scratch.file(base, 'uninst.exe', content=_fill(128 * 1024))
    scratch.file(
        base, 'resources', 'chrome', 'chrome.exe', content=_fill(1024 * 1024)
    )
    return scratch.file(base, '用户端.exe', content=_tiny_exe_bytes())


def test_yuehulian_via_shortcut(fake_yuehulian_install, shortcut_spot):
    """A 悦互联-named shortcut resolves to the Chinese-named main exe —
    never the chrome kernel or the uninstaller."""
    shortcut_spot('Desktop', '悦互联测试.lnk', fake_yuehulian_install)
    hit = wal.locate(wal.YUEHULIAN_SPEC)
    assert hit is not None
    assert hit.hint == 'shortcut'
    assert hit.exe_path.lower() == fake_yuehulian_install.lower()


def test_yuehulian_via_uninstall_install_location(fake_yuehulian_install):
    sub = _UNINSTALL_BASE + '\\vibetest-' + uuid.uuid4().hex[:8]
    _write_hkcu(
        sub,
        {
            'DisplayName': '悦互联测试',
            'InstallLocation': (os.path.dirname(fake_yuehulian_install) + '\\'),
        },
    )
    try:
        hit = wal.locate(wal.YUEHULIAN_SPEC)
        assert hit is not None
        assert hit.hint == 'uninstall'
        assert hit.exe_path.lower() == fake_yuehulian_install.lower()
    finally:
        _delete_hkcu(sub)


def test_remembered_process_path_beats_everything(fake_ziniao_install):
    """The launch flow kills the client before needing the path: the
    remembered pre-kill exe wins over registry/shortcut/drive hints."""
    wal.remember_process_path('ziniao', fake_ziniao_install)
    hit = wal.locate(wal.ZINIAO_SPEC)
    assert hit is not None
    assert hit.hint == 'process'
    assert hit.exe_path.lower() == fake_ziniao_install.lower()


def test_yuehulian_process_queries_see_custom_dir_client(scratch):
    """A client running from a Chinese custom install dir (no
    'YueHuLian' anywhere in its path) is detected by is_running() and
    main_pid() via the located install-dir prefix.

    The "client" is a copy of ping.exe masquerading as 用户端.exe, kept
    alive for ~90 s.
    """
    install_dir = scratch.mkdir('悦互联测试进程')
    main = os.path.join(install_dir, '用户端.exe')
    shutil.copyfile(os.path.join(_SYSTEM32, 'PING.EXE'), main)
    sub = _UNINSTALL_BASE + '\\vibetest-' + uuid.uuid4().hex[:8]
    _write_hkcu(
        sub,
        {
            'DisplayName': '悦互联测试',
            'InstallLocation': install_dir + '\\',
        },
    )
    pid = None
    try:
        pid = int(
            _ps_scalar(
                '(Start-Process -PassThru -FilePath '
                + _ps_quote(main)
                + " -ArgumentList '-n','90','127.0.0.1').Id"
            )
        )
        layer = ProcessLayer()
        # Poll only until the process shows up in the process table.
        deadline = time.monotonic() + 30
        while layer.main_pid() != pid and time.monotonic() < deadline:
            time.sleep(0.5)
        assert layer.main_pid() == pid
        assert layer.is_running() is True
    finally:
        if pid is not None:
            _ps_scalar(
                f'Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue'
            )
        _delete_hkcu(sub)  # the files go with the scratch directory
