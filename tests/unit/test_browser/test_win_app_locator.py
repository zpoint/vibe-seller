"""Unit tests for the shared Windows app locator (all OS, fake system).

The locator's system probes (registry, shortcuts, drives, path
translation) are injected as :class:`LocatorRunners` fakes and the
filesystem is real ``tmp_path`` mapped from fake Windows paths, so
validation, ordering, parsing and caching run exactly as in
production. Real-Windows behaviour (winreg, PowerShell .lnk
resolution, Chinese install dirs) is covered by
``test_win_app_locator_windows.py`` on the windows CI matrix.
"""

import os
from types import SimpleNamespace

import pytest

from app.browser import win_app_locator as wal
from tests.unit.test_browser import lnk_fixtures as fx

# The per-user Programs candidates are built from LOCALAPPDATA. Every
# test gets this KNOWN value (mapped under tmp_path like any other fake
# drive path): the machine's real one — Windows CI sets it, a dev box
# may even hold a real install under it — must never decide a result.
_LOCALAPPDATA = 'C:\\Users\\u\\AppData\\Local'
_ZINIAO = 'D:\\紫鸟\\ziniao\\ziniao.exe'


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """No locator state or ambient env leaks between tests."""
    wal.clear_locator_cache()
    monkeypatch.setenv('LOCALAPPDATA', _LOCALAPPDATA)
    yield
    wal.clear_locator_cache()


def _fake_fs(tmp_path):
    """``to_local`` mapping ``X:\\a\\b`` under tmp_path + file factories."""

    def to_local(win: str) -> str:
        p = str(win).strip().strip('"')
        if len(p) >= 2 and p[1] == ':':
            rel = p[2:].replace('\\', '/')
            return str(tmp_path / p[0].lower() / rel.lstrip('/'))
        return p

    def touch(win: str, size: int = 64) -> None:
        local = to_local(win)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, 'wb') as f:
            f.write(b'MZ' + b'\0' * max(size - 2, 0))

    def mkdir(win: str) -> None:
        os.makedirs(to_local(win), exist_ok=True)

    def lnk(win: str, *, ansi: bytes = b'?', wide: str | None = None) -> None:
        """A shortcut file at ``win`` pointing at ``wide`` (or ``ansi``).

        The default is what an English Windows writes for a Chinese
        target: a ``?``-mangled ANSI copy next to the real UTF-16 one.
        """
        touch(win)
        with open(to_local(win), 'wb') as f:
            f.write(fx.lnk(fx.link_info(ansi, wide=wide)))

    return SimpleNamespace(to_local=to_local, touch=touch, mkdir=mkdir, lnk=lnk)


def _runners(fs, *, app_paths=(), uninstall=(), shortcuts=(), drives=()):
    return wal.LocatorRunners(
        app_paths=lambda: list(app_paths),
        uninstall_entries=lambda: list(uninstall),
        shortcut_targets=lambda: list(shortcuts),
        fixed_drives=lambda: list(drives),
        to_local=fs.to_local,
    )


def _uninstall(
    display_name='',
    install_location='',
    display_icon='',
    uninstall_string='',
    source='HKCU\\Uninstall\\test',
):
    return wal.UninstallEntry(
        source=source,
        display_name=display_name,
        install_location=install_location,
        display_icon=display_icon,
        uninstall_string=uninstall_string,
    )


class TestConfiguredHint:
    """Hint 1: the caller-configured path."""

    def test_full_exe_path_is_trusted_as_is(self, tmp_path):
        """An explicit .exe path is operator intent — returned unvalidated."""
        fs = _fake_fs(tmp_path)
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            configured_path='E:\\自定义\\ziniao.exe',
            runners=_runners(fs),
        )
        assert hit.exe_path == 'E:\\自定义\\ziniao.exe'
        assert hit.hint == 'configured'

    def test_dir_resolves_main_exe_one_level_down(self, tmp_path):
        """D:\\紫鸟 containing D:\\紫鸟\\ziniao\\ziniao.exe resolves."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\紫鸟\\ziniao\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            configured_path='D:\\紫鸟',
            runners=_runners(fs),
        )
        assert hit.exe_path == 'D:\\紫鸟\\ziniao\\ziniao.exe'
        assert hit.hint == 'configured'

    def test_dead_dir_falls_through(self, tmp_path):
        fs = _fake_fs(tmp_path)
        assert (
            wal.locate(
                wal.ZINIAO_SPEC,
                configured_path='D:\\nope',
                runners=_runners(fs),
            )
            is None
        )


class TestProcessHint:
    """Hint 2: the exe path observed before the kill step stopped it."""

    def test_remembered_path_wins_when_file_remains(self, tmp_path):
        """Kill removes processes, not the install — the remembered exe
        is the top hint even though nothing is running at lookup time."""
        fs = _fake_fs(tmp_path)
        exe = 'D:\\紫鸟\\ziniao\\ziniao.exe'
        fs.touch(exe)
        wal.remember_process_path('ziniao', f'"{exe}"')
        hit = wal.locate(wal.ZINIAO_SPEC, runners=_runners(fs))
        assert hit.exe_path == exe
        assert hit.hint == 'process'
        assert 'seen before kill' in hit.detail

    def test_remembered_path_validated_by_name(self, tmp_path):
        """A remembered helper (chrome.exe) is not the main exe."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\x\\resources\\chrome.exe')
        wal.remember_process_path('ziniao', 'D:\\x\\resources\\chrome.exe')
        assert wal.locate(wal.ZINIAO_SPEC, runners=_runners(fs)) is None

    def test_remembered_path_must_still_exist(self, tmp_path):
        fs = _fake_fs(tmp_path)  # uninstalled after being remembered
        wal.remember_process_path('ziniao', 'D:\\gone\\ziniao.exe')
        assert wal.locate(wal.ZINIAO_SPEC, runners=_runners(fs)) is None


class TestHintOrder:
    """First hint with a valid hit wins, in the documented order."""

    def test_full_precedence_chain(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\AF\\ziniao.exe')
        fs.touch('D:\\紫鸟\\ziniao\\ziniao.exe')
        fs.touch('D:\\SF\\ziniao.exe')
        fs.touch('D:\\ziniao\\ziniao.exe')
        all_runners = _runners(
            fs,
            app_paths=[('ziniao.exe', 'C:\\AF\\ziniao.exe')],
            uninstall=[
                _uninstall(
                    display_name='紫鸟浏览器',
                    install_location='D:\\紫鸟\\',
                )
            ],
            shortcuts=[('D:\\SF\\ziniao.exe', 'C:\\d\\紫鸟.lnk')],
            drives=['C:', 'D:'],
        )
        assert wal.locate(wal.ZINIAO_SPEC, runners=all_runners).hint == (
            'app_paths'
        )
        # The cache carries the hit across calls; each step down the
        # chain must start from a clean slate.
        wal.clear_locator_cache()
        no_app_paths = _runners(
            fs,
            uninstall=[
                _uninstall(
                    display_name='紫鸟浏览器',
                    install_location='D:\\紫鸟\\',
                )
            ],
            shortcuts=[('D:\\SF\\ziniao.exe', 'C:\\d\\紫鸟.lnk')],
            drives=['C:', 'D:'],
        )
        assert (
            wal.locate(wal.ZINIAO_SPEC, runners=no_app_paths).hint
            == 'uninstall'
        )
        wal.clear_locator_cache()
        no_registry = _runners(
            fs,
            shortcuts=[('D:\\SF\\ziniao.exe', 'C:\\d\\紫鸟.lnk')],
            drives=['C:', 'D:'],
        )
        assert (
            wal.locate(wal.ZINIAO_SPEC, runners=no_registry).hint == 'shortcut'
        )
        wal.clear_locator_cache()
        no_shortcuts = _runners(fs, drives=['C:', 'D:'])
        hit = wal.locate(wal.ZINIAO_SPEC, runners=no_shortcuts)
        assert hit.hint == 'conventional'
        assert hit.exe_path == 'D:\\ziniao\\ziniao.exe'

    def test_remembered_process_beats_registry(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\PF\\ziniao.exe')
        fs.touch('D:\\proc\\ziniao.exe')
        wal.remember_process_path('ziniao', 'D:\\proc\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs, app_paths=[('ziniao.exe', 'C:\\PF\\ziniao.exe')]
            ),
        )
        assert hit.hint == 'process'


class TestAppPathsHint:
    def test_matched_by_subkey_name(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\紫鸟\\ziniao\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs, app_paths=[('ziniao.exe', 'D:\\紫鸟\\ziniao\\ziniao.exe')]
            ),
        )
        assert hit.hint == 'app_paths'
        assert 'App Paths\\ziniao.exe' in hit.detail

    def test_matched_by_token_in_value(self, tmp_path):
        """Yuehulian has no fixed exe name — a token-matched App Paths
        value with a valid exe inside its dir still resolves."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\悦互联\\用户端.exe', size=2048)
        hit = wal.locate(
            wal.YUEHULIAN_SPEC,
            runners=_runners(
                fs, app_paths=[('用户端.exe', 'D:\\悦互联\\用户端.exe')]
            ),
        )
        assert hit.exe_path == 'D:\\悦互联\\用户端.exe'

    def test_wrong_basename_rejected(self, tmp_path):
        """App Paths\\other.exe pointing at a non-ziniao exe: value has
        no token, subkey name doesn't match — skipped."""
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\tools\\helper.exe')
        assert (
            wal.locate(
                wal.ZINIAO_SPEC,
                runners=_runners(
                    fs, app_paths=[('helper.exe', 'C:\\tools\\helper.exe')]
                ),
            )
            is None
        )


class TestUninstallHint:
    def test_install_location_preferred_with_trailing_slash(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\紫鸟\\ziniao\\ziniao.exe')
        fs.touch('D:\\icon\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs,
                uninstall=[
                    _uninstall(
                        display_name='紫鸟',
                        install_location='D:\\紫鸟\\',
                        display_icon='D:\\icon\\ziniao.exe,0',
                    )
                ],
            ),
        )
        assert hit.hint == 'uninstall'
        assert hit.exe_path == 'D:\\紫鸟\\ziniao\\ziniao.exe'
        assert 'InstallLocation' in hit.detail
        assert hit.detail.startswith('HKCU\\Uninstall\\test:')

    def test_display_icon_strips_index_and_quotes(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\app\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs,
                uninstall=[
                    _uninstall(
                        display_icon='"D:\\app\\ziniao.exe",1',
                    )
                ],
            ),
        )
        assert hit.exe_path == 'D:\\app\\ziniao.exe'
        assert 'DisplayIcon:' in hit.detail  # not 'DisplayIcon dir'

    def test_uninstall_string_parent_dir_last_resort(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\Program Files\\ziniao\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs,
                uninstall=[
                    _uninstall(
                        display_name='ziniao',
                        uninstall_string=(
                            '"C:\\Program Files\\ziniao\\uninst.exe" /S'
                        ),
                    )
                ],
            ),
        )
        assert hit.exe_path == 'C:\\Program Files\\ziniao\\ziniao.exe'
        assert 'UninstallString dir' in hit.detail

    def test_decoy_uninstaller_rejected(self, tmp_path):
        """A DisplayIcon whose path merely contains 'ziniao' but IS an
        uninstaller must not be returned (the old bug)."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\ziniao-tools\\uninst.exe')
        assert (
            wal.locate(
                wal.ZINIAO_SPEC,
                runners=_runners(
                    fs,
                    uninstall=[
                        _uninstall(
                            display_icon='D:\\ziniao-tools\\uninst.exe,0'
                        )
                    ],
                ),
            )
            is None
        )

    def test_never_matched_by_uninstall_string_alone(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\shared\\ziniao\\ziniao.exe')
        assert (
            wal.locate(
                wal.ZINIAO_SPEC,
                runners=_runners(
                    fs,
                    uninstall=[
                        _uninstall(
                            display_name='别的软件',
                            uninstall_string=(
                                '"C:\\shared\\ziniao\\uninst.exe" /S'
                            ),
                        )
                    ],
                ),
            )
            is None
        )


class TestShortcutHint:
    def test_strong_match_by_target_basename(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\紫鸟\\ziniao\\ziniao.exe')
        hit = wal.locate(
            wal.ZINIAO_SPEC,
            runners=_runners(
                fs,
                shortcuts=[
                    (
                        'D:\\紫鸟\\ziniao\\ziniao.exe',
                        'C:\\Users\\u\\Desktop\\随便什么.lnk',
                    )
                ],
            ),
        )
        assert hit.hint == 'shortcut'
        assert hit.exe_path == 'D:\\紫鸟\\ziniao\\ziniao.exe'

    def test_weak_match_chinese_shortcut_name(self, tmp_path):
        """悦互联's exe name is a locale string — matching rides on the
        shortcut name + a token in the target path."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\悦互联测试\\用户端.exe', size=2048)
        hit = wal.locate(
            wal.YUEHULIAN_SPEC,
            runners=_runners(
                fs,
                shortcuts=[
                    (
                        'D:\\悦互联测试\\用户端.exe',
                        'C:\\Users\\Public\\Desktop\\悦互联.lnk',
                    )
                ],
            ),
        )
        assert hit.hint == 'shortcut'
        assert hit.exe_path == 'D:\\悦互联测试\\用户端.exe'

    def test_weak_match_requires_token_in_target(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\other\\client.exe')
        assert (
            wal.locate(
                wal.ZINIAO_SPEC,
                runners=_runners(
                    fs,
                    shortcuts=[
                        (
                            'C:\\other\\client.exe',
                            'C:\\Users\\u\\Desktop\\紫鸟.lnk',
                        )
                    ],
                ),
            )
            is None
        )

    def test_reads_shortcut_files_under_the_known_folders(
        self, tmp_path, monkeypatch
    ):
        """The hint end to end on its REAL runner. PowerShell answers
        with nothing but the shortcut folders (a Chinese user name,
        blank and quoted lines); every ``.lnk`` under them is read
        from disk — a Chinese path never crosses a code page or COM."""
        fs = _fake_fs(tmp_path)
        fs.mkdir('C:\\Users\\用户\\Desktop')
        fs.mkdir(
            'C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\vendor'
        )
        fs.lnk('C:\\Users\\用户\\Desktop\\紫鸟浏览器.lnk', wide=_ZINIAO)
        fs.lnk(
            'C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs'
            '\\vendor\\ziniao.lnk',
            ansi=b'E:\\Apps\\ziniao.exe',
        )
        asked = []

        def powershell(script, timeout=30.0):
            asked.append(script)
            return (
                'C:\\Users\\用户\\Desktop\r\n'
                '\r\n'
                '"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs"\r\n'
            )

        monkeypatch.setattr(wal, 'run_powershell', powershell)
        monkeypatch.setattr(wal, 'win_path_to_local', fs.to_local)

        found = wal._default_shortcut_targets()

        assert sorted(found) == [
            (_ZINIAO, 'C:\\Users\\用户\\Desktop\\紫鸟浏览器.lnk'),
            (
                'E:\\Apps\\ziniao.exe',
                'C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs'
                '\\vendor\\ziniao.lnk',
            ),
        ]
        # The one PowerShell call asks for folders only — no COM object
        # touches a shortcut path (WScript.Shell is code-page bound).
        assert len(asked) == 1
        assert 'WScript' not in asked[0]
        for folder in (
            'Desktop',
            'CommonDesktopDirectory',
            'Programs',
            'CommonPrograms',
        ):
            assert f"GetFolderPath('{folder}')" in asked[0]


class TestValidation:
    def test_rejects_uninstaller_chrome_and_resources(self, tmp_path):
        fs = _fake_fs(tmp_path)
        for p in (
            'D:\\a\\uninst.exe',
            'D:\\a\\Uninstall ziniao.exe',
            'D:\\a\\chrome.exe',
            'D:\\a\\crashpad_handler.exe',
            'D:\\a\\resources\\some.exe',
        ):
            fs.touch(p)
            assert not wal.is_main_exe(wal.ZINIAO_SPEC, p, fs.to_local), p

    def test_rejects_wrong_basename_and_missing(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\a\\client.exe')
        assert not wal.is_main_exe(
            wal.ZINIAO_SPEC, 'D:\\a\\client.exe', fs.to_local
        )
        assert not wal.is_main_exe(
            wal.ZINIAO_SPEC, 'D:\\a\\ziniao.exe', fs.to_local
        )
        assert not wal.is_main_exe(
            wal.ZINIAO_SPEC, 'D:\\a\\notes.txt', fs.to_local
        )

    def test_yuehulian_picks_largest_non_rejected_exe(self, tmp_path):
        """No fixed exe name: the Electron binary (largest) wins over
        helpers; kernels under resources\\ are never picked."""
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\悦互联\\用户端.exe', size=4096)
        fs.touch('D:\\悦互联\\helper.exe', size=1024)
        fs.touch('D:\\悦互联\\uninst.exe', size=8192)
        fs.touch('D:\\悦互联\\resources\\chrome.exe', size=999999)
        assert (
            wal.main_exe_in_dir(wal.YUEHULIAN_SPEC, 'D:\\悦互联', fs.to_local)
            == 'D:\\悦互联\\用户端.exe'
        )


class TestConventionalHint:
    def test_candidate_order_and_names(self):
        """Every fixed drive's roots in order, then the per-user
        Programs dir — the whole list, from a KNOWN LOCALAPPDATA."""
        got = wal.conventional_dirs(wal.ZINIAO_SPEC, drives=['C:', 'D:'])
        assert got == [
            'C:\\Program Files\\ziniao',
            'C:\\Program Files\\紫鸟',
            'C:\\Program Files\\紫鸟浏览器',
            'C:\\Program Files (x86)\\ziniao',
            'C:\\Program Files (x86)\\紫鸟',
            'C:\\Program Files (x86)\\紫鸟浏览器',
            'D:\\Program Files\\ziniao',
            'D:\\Program Files\\紫鸟',
            'D:\\Program Files\\紫鸟浏览器',
            'D:\\Program Files (x86)\\ziniao',
            'D:\\Program Files (x86)\\紫鸟',
            'D:\\Program Files (x86)\\紫鸟浏览器',
            'C:\\ziniao',
            'C:\\紫鸟',
            'C:\\紫鸟浏览器',
            'D:\\ziniao',
            'D:\\紫鸟',
            'D:\\紫鸟浏览器',
            'C:\\Users\\u\\AppData\\Local\\Programs\\ziniao',
            'C:\\Users\\u\\AppData\\Local\\Programs\\紫鸟',
            'C:\\Users\\u\\AppData\\Local\\Programs\\紫鸟浏览器',
        ]

    def test_no_programs_candidates_without_localappdata(self, monkeypatch):
        """No per-user profile dir (WSL, a stripped-down account): the
        drive candidates alone, nothing dangling."""
        monkeypatch.delenv('LOCALAPPDATA')
        got = wal.conventional_dirs(wal.ZINIAO_SPEC, drives=['C:'])
        assert got == [
            'C:\\Program Files\\ziniao',
            'C:\\Program Files\\紫鸟',
            'C:\\Program Files\\紫鸟浏览器',
            'C:\\Program Files (x86)\\ziniao',
            'C:\\Program Files (x86)\\紫鸟',
            'C:\\Program Files (x86)\\紫鸟浏览器',
            'C:\\ziniao',
            'C:\\紫鸟',
            'C:\\紫鸟浏览器',
        ]

    def test_finds_non_default_drive(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\ziniao\\ziniao.exe')
        hit = wal.locate(wal.ZINIAO_SPEC, runners=_runners(fs, drives=['D:']))
        assert hit.hint == 'conventional'
        assert hit.exe_path == 'D:\\ziniao\\ziniao.exe'

    def test_finds_user_programs_dir(self, tmp_path, monkeypatch):
        fs = _fake_fs(tmp_path)
        fs.touch('C:\\Users\\u\\AppData\\Local\\Programs\\ziniao\\ziniao.exe')
        monkeypatch.setenv('LOCALAPPDATA', 'C:\\Users\\u\\AppData\\Local')
        hit = wal.locate(wal.ZINIAO_SPEC, runners=_runners(fs, drives=['C:']))
        assert hit.hint == 'conventional'
        assert (
            hit.exe_path
            == 'C:\\Users\\u\\AppData\\Local\\Programs\\ziniao\\ziniao.exe'
        )


class TestCache:
    def test_second_call_skips_runners_until_file_disappears(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\ziniao\\ziniao.exe')
        calls = {'n': 0}

        def counting_shortcuts():
            calls['n'] += 1
            return []

        runners = wal.LocatorRunners(
            app_paths=lambda: [],
            uninstall_entries=lambda: [],
            shortcut_targets=counting_shortcuts,
            fixed_drives=lambda: ['D:'],
            to_local=fs.to_local,
        )
        first = wal.locate(wal.ZINIAO_SPEC, runners=runners)
        assert first.hint == 'conventional'
        wal.locate(wal.ZINIAO_SPEC, runners=runners)
        assert calls['n'] == 1  # cached: runners untouched
        os.remove(fs.to_local('D:\\ziniao\\ziniao.exe'))
        wal.locate(wal.ZINIAO_SPEC, runners=runners)
        assert calls['n'] == 2  # invalidated: probed again

    def test_clear_locator_cache_forces_rediscovery(self, tmp_path):
        fs = _fake_fs(tmp_path)
        fs.touch('D:\\ziniao\\ziniao.exe')
        runners = _runners(fs, drives=['D:'])
        assert wal.locate(wal.ZINIAO_SPEC, runners=runners)
        wal.clear_locator_cache()
        assert wal.locate(wal.ZINIAO_SPEC, runners=runners)


class TestNotFoundMessage:
    def test_lists_searched_surfaces_and_actions(self, monkeypatch):
        monkeypatch.setattr(wal, 'fixed_drives', lambda: ['C:', 'D:'])
        msg = wal.not_found_message(wal.ZINIAO_SPEC)
        assert 'Could not find ziniao.exe.' in msg
        for surface in (
            'running client',
            'App Paths',
            'Uninstall',
            'shortcuts',
            'C:, D:',
        ):
            assert surface in msg, surface
        # ADDENDUM: no "set the exe path" advice — there is no UI for it.
        assert 'client_path' not in msg
        assert 'setting' not in msg
        assert 'Open Ziniao once by hand' in msg
        assert 'reinstall' in msg

    def test_yuehulian_message_names_the_app(self):
        assert '悦互联' in wal.not_found_message(wal.YUEHULIAN_SPEC)


class TestPsUninstallWildcard:
    """The WSL PowerShell fallback must query the Uninstall CHILD keys.

    ``Get-ItemProperty`` on the CONTAINER key returns the container's
    own (empty) values — not its children — so the pre-fix query
    returned 0 entries for every app on every WSL machine, and no
    installed client was ever found through the registry hint from
    WSL (native Windows reads winreg and was unaffected). The fake
    PowerShell below mimics the real behaviour: child rows only for
    ``Uninstall\\*`` keys, nothing for bare container keys.
    """

    ROW = (
        '示例浏览器 1.0\tC:\\Apps\\demo\t\t'
        '"C:\\Apps\\demo\\uninst.exe"\t'
        'Microsoft.PowerShell.Core\\Registry::HKEY_CURRENT_USER'
        '\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
        '\\demo\r\n'
    )

    def test_entries_found_when_children_are_queried(self, monkeypatch):
        def powershell(script, timeout=30.0):
            # PowerShell's real shape: rows only for child-key queries.
            if '\\Uninstall\\*' in script:
                return self.ROW
            return ''

        monkeypatch.setattr(wal, 'run_powershell', powershell)
        entries = wal._ps_uninstall_entries()
        assert len(entries) == 1
        assert entries[0].display_name == '示例浏览器 1.0'
        assert entries[0].install_location == 'C:\\Apps\\demo'
        assert entries[0].uninstall_string == '"C:\\Apps\\demo\\uninst.exe"'
