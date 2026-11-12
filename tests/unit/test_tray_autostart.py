"""Unit tests for the Windows tray's "Start at login" switch.

Three tiers, mirroring how the feature is built:

(a) codec — ``encode_approval`` / ``is_approved`` are pure and run on
    any OS (the 12-byte / FILETIME contract is in their docstrings);
(b) ship/coupling pin — parses the REAL vibe-seller.iss / build.ps1.
    A missing ``[Files]`` entry would crash the tray at import on
    every login, and LNK_NAME must stay the file name the
    ``{userstartup}`` ``[Icons]`` entry creates;
(c) real Windows — registry round trip under a scratch key plus a
    shortcut re-created through WScript.Shell, in temp dirs.

Loads the module the way test_tray_dialog.py loads ``dialogs``:
``installer/windows`` on sys.path.
"""

import ast
import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import sys
import uuid

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_WIN_DIR = _REPO / 'installer' / 'windows'


def _load_autostart():
    if str(_WIN_DIR) not in sys.path:
        sys.path.insert(0, str(_WIN_DIR))
    import autostart  # noqa: PLC0415

    return autostart


autostart = _load_autostart()

ISS_TEXT = (_WIN_DIR / 'vibe-seller.iss').read_text(encoding='utf-8')
BUILD_TEXT = (_WIN_DIR / 'build.ps1').read_text(encoding='utf-8')

_FIXED = datetime(2026, 3, 4, 5, 6, 7, 890123, tzinfo=UTC)
_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def _ticks(dt: datetime) -> int:
    """FILETIME ticks, computed independently of the module's helper."""
    delta = dt - _EPOCH
    return (
        delta.days * 86_400 + delta.seconds
    ) * 10_000_000 + delta.microseconds * 10


# -- (a) codec, any OS ------------------------------------------------


def test_is_approved_absent_or_empty_means_enabled():
    assert autostart.is_approved(None) is True
    assert autostart.is_approved(b'') is True


@pytest.mark.parametrize('flag', [0x02, 0x06])
def test_is_approved_even_first_byte_means_enabled(flag):
    # Windows also writes 06 — the rule is bit 0, not "== 0x02".
    assert autostart.is_approved(bytes([flag]) + b'\x00' * 11) is True


@pytest.mark.parametrize('flag', [0x03, 0x07])
def test_is_approved_odd_first_byte_means_disabled(flag):
    assert autostart.is_approved(bytes([flag]) + b'\x00' * 11) is False


def test_encode_approval_enabled_is_flag_plus_zeros():
    assert autostart.encode_approval(True) == b'\x02' + b'\x00' * 11


def test_encode_approval_disabled_carries_filetime():
    value = autostart.encode_approval(False, now=_FIXED)
    assert len(value) == 12
    assert value[:4] == b'\x03\x00\x00\x00'
    filetime = int.from_bytes(value[4:], 'little')
    assert filetime == _ticks(_FIXED)
    # The 8 bytes decode back to the given moment.
    assert _EPOCH + timedelta(microseconds=filetime // 10) == _FIXED


def test_encode_approval_disabled_defaults_to_now():
    assert autostart.is_approved(autostart.encode_approval(False)) is False


def test_codec_round_trip():
    assert autostart.is_approved(autostart.encode_approval(True)) is True
    assert (
        autostart.is_approved(autostart.encode_approval(False, now=_FIXED))
        is False
    )


# -- (b) ship / coupling pin, any OS -----------------------------------


def _iss_sections(text: str) -> dict:
    """{section name: [lines]} for an Inno script (good enough to read
    [Files] / [Icons]; comments kept — entries are matched on content)."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        header = re.match(r'^\[(\w+)\]', line.strip())
        if header:
            current = header.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _joined_entry(lines: list[str], prefix: str) -> str:
    """One Inno entry (continuation backslashes joined), or ''."""
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            parts = []
            j = i
            while j < len(lines):
                raw = lines[j].rstrip()
                cont = raw.endswith('\\')
                parts.append((raw[:-1] if cont else raw).strip())
                if not cont:
                    break
                j += 1
            return ' '.join(parts)
    return ''


def test_iss_installs_autostart_py():
    files = _iss_sections(ISS_TEXT).get('Files', [])
    entries = [
        line
        for line in files
        if 'autostart.py' in line and not line.strip().startswith(';')
    ]
    assert entries, (
        'vibe-seller.iss needs a [Files] entry installing autostart.py '
        'to {app}: tray.py imports it at startup, so a missing entry '
        'crashes the tray at every login'
    )
    assert any('DestDir: "{app}"' in line for line in entries), (
        f'autostart.py must go to {{app}}, got: {entries}'
    )


def test_build_ps1_stages_autostart_py():
    hits = [
        line
        for line in BUILD_TEXT.splitlines()
        if 'autostart.py' in line and '$StagingDir' in line
    ]
    assert hits, 'build.ps1 must copy autostart.py into the staging dir'
    assert any('Copy-Item' in line for line in hits)


def test_lnk_name_matches_the_iss_icons_entry():
    # '#define AppName' + '.lnk' is the exact value name Windows keys
    # the startup flag by; drift here silently breaks the switch.
    match = re.search(r'#define\s+AppName\s+"([^"]+)"', ISS_TEXT)
    assert match, 'no #define AppName in vibe-seller.iss'
    assert f'{match.group(1)}.lnk' == autostart.LNK_NAME

    icons = _iss_sections(ISS_TEXT).get('Icons', [])
    entry = _joined_entry(icons, 'Name: "{userstartup}')
    assert entry, 'the {userstartup} [Icons] entry is gone'
    assert '{#AppName}' in entry
    assert '{#AppExeTray}' in entry


def test_login_shortcut_starts_without_open():
    icons = _iss_sections(ISS_TEXT).get('Icons', [])
    entry = _joined_entry(icons, 'Name: "{userstartup}')
    assert entry
    params = re.search(r'Parameters:\s*([^;]+)', entry)
    assert params, f'no Parameters field in: {entry}'
    assert '--open' not in params.group(1), (
        'the login shortcut must not pass --open (reboots must not '
        'pop a browser window)'
    )
    assert 'tray.py' in params.group(1)


def test_tray_menu_strings_exist_in_both_languages():
    # _t() looks up _STRINGS[_LANG][key]; a key present only in en
    # builds the menu fine on English Windows but raises KeyError at
    # startup on a Chinese one — and _system_is_chinese picks the
    # language, so the tray never comes up there. tray.py imports
    # pystray/app, which Linux lacks, so read the dict via ast.
    tree = ast.parse((_WIN_DIR / 'tray.py').read_text(encoding='utf-8'))
    strings = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, 'id', '') == '_STRINGS' for t in node.targets
        ):
            strings = ast.literal_eval(node.value)
    assert strings is not None, 'no _STRINGS assignment in tray.py'
    en = set(strings['en'])
    zh = set(strings['zh'])
    assert en == zh, f'en-only={en - zh}, zh-only={zh - en}'


# -- (c) real Windows ---------------------------------------------------

_WINDOWS = pytest.mark.skipif(
    sys.platform != 'win32',
    reason='real registry + WScript.Shell tests need Windows',
)


@pytest.fixture
def scratch_key():
    """A private HKCU scratch key — NEVER the real StartupApproved
    value, which belongs to the user."""
    key = 'Software\\VibeSellerTest\\' + uuid.uuid4().hex
    yield key
    import winreg  # noqa: PLC0415

    with contextlib.suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    # Also take the scratch parent, so no run leaves state behind on
    # the machine. DeleteKey refuses a key that still has subkeys
    # (OSError), which is exactly right: a concurrent run's leaf
    # keeps the parent alive, and the last one removes it.
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, 'Software\\VibeSellerTest')


def _make(tmp_path: Path, approved_key: str):
    startup = tmp_path / 'Startup'
    startup.mkdir()
    # A space in the install path, like "...\Programs\Vibe Seller".
    install = tmp_path / 'Vibe Seller'
    obj = autostart.AutoStart(
        startup_dir=startup,
        approved_key=approved_key,
        install_dir=install,
    )
    return obj, startup, install


def _raw_flag(approved_key: str) -> bytes:
    import winreg  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key) as key:
        value, _ = winreg.QueryValueEx(key, autostart.LNK_NAME)
    return bytes(value)


@_WINDOWS
def test_startup_folder_id_is_the_real_known_folder():
    # A wrong FOLDERID GUID makes SHGetKnownFolderPath fail with
    # 0x80070002, and startup_dir()'s registry fallback would hide
    # that: the shell path must be proven to work on its own, and to
    # agree with .NET's own Startup answer.
    import subprocess  # noqa: PLC0415

    folder = autostart._sh_get_known_folder(autostart._FOLDERID_STARTUP)
    assert folder is not None, (
        'SHGetKnownFolderPath failed for _FOLDERID_STARTUP — the GUID '
        'is not FOLDERID_Startup'
    )
    proc = subprocess.run(
        [
            'powershell.exe',
            '-NoProfile',
            '-Command',
            "[Environment]::GetFolderPath('Startup')",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    expected = proc.stdout.strip().rstrip('\r\n')
    assert str(folder) == expected


@_WINDOWS
def test_absent_flag_with_shortcut_reads_enabled(tmp_path, scratch_key):
    obj, startup, _ = _make(tmp_path, scratch_key)
    (startup / autostart.LNK_NAME).write_bytes(b'placeholder lnk')
    assert obj.is_enabled() is True


@_WINDOWS
def test_set_enabled_false_writes_flag_and_keeps_shortcut(
    tmp_path, scratch_key
):
    obj, startup, _ = _make(tmp_path, scratch_key)
    lnk = startup / autostart.LNK_NAME
    lnk.write_bytes(b'placeholder lnk')
    assert obj.set_enabled(False) is False
    assert _raw_flag(scratch_key)[0] == 0x03
    assert lnk.is_file(), 'off must keep the shortcut (Settings still lists it)'
    assert obj.is_enabled() is False


@_WINDOWS
def test_set_enabled_true_writes_even_flag(tmp_path, scratch_key):
    obj, startup, _ = _make(tmp_path, scratch_key)
    (startup / autostart.LNK_NAME).write_bytes(b'placeholder lnk')
    obj.set_enabled(False)
    assert obj.set_enabled(True) is True
    assert _raw_flag(scratch_key)[0] % 2 == 0
    assert obj.is_enabled() is True


@_WINDOWS
def test_missing_shortcut_reads_disabled_and_true_recreates_it(
    tmp_path, scratch_key
):
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    obj, startup, install = _make(tmp_path, scratch_key)
    lnk = startup / autostart.LNK_NAME
    lnk.write_bytes(b'placeholder lnk')
    obj.set_enabled(False)
    lnk.unlink()
    assert obj.is_enabled() is False, (
        'a missing shortcut is not "on", whatever the flag says'
    )

    obj.set_enabled(True)
    assert lnk.is_file(), 'set_enabled(True) must re-create the shortcut'
    assert obj.is_enabled() is True

    # Read the .lnk back through the same COM interface, proving the
    # env-var path passing survived the space in the temp path.
    script = (
        '$sc = (New-Object -ComObject WScript.Shell)'
        '.CreateShortcut($env:VS_LNK);'
        '"TARGET=$($sc.TargetPath)";"ARGS=$($sc.Arguments)";'
        '"WD=$($sc.WorkingDirectory)"'
    )
    env = dict(os.environ)
    env['VS_LNK'] = str(lnk)
    proc = subprocess.run(
        ['powershell.exe', '-NoProfile', '-Command', script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=(
            subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        ),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    props = {}
    for line in proc.stdout.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            props[k] = v
    assert props['TARGET'] == str(install / '.venv' / 'Scripts' / 'pythonw.exe')
    assert props['ARGS'] == f'"{install / "tray.py"}"'
    assert props['WD'] == str(install)
