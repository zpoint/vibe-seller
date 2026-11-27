"""The Windows-registry lookup half of the install locator.

Moved alongside :mod:`app.browser.win_app_registry` when it was split
out of :mod:`app.browser.win_app_locator`; the behavior is unchanged,
and the locator-level tests stay in ``test_win_app_locator.py``. The
shared fs/runner helpers come from there (one definition).
"""

from app.browser import win_app_locator as wal, win_app_registry as war
from tests.unit.test_browser.test_win_app_locator import (
    _fake_fs,
    _runners,
    _uninstall,
)


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

        monkeypatch.setattr(
            'app.browser.win_app_registry.run_powershell', powershell
        )
        entries = war._ps_uninstall_entries()
        assert len(entries) == 1
        assert entries[0].display_name == '示例浏览器 1.0'
        assert entries[0].install_location == 'C:\\Apps\\demo'
        assert entries[0].uninstall_string == '"C:\\Apps\\demo\\uninst.exe"'
