"""Tests for Ziniao browser utility functions."""

from unittest import mock

import pytest

from app.browser import win_app_locator as win_locator
from app.browser.ziniao_utils import (
    build_launch_cmd,
    find_ziniao_exe_windows,
    get_platform,
    get_ziniao_host,
    is_wsl,
    is_ziniao_installed_mac,
)


@pytest.fixture(autouse=True)
def _clean_locator_state():
    """Locator cache / remembered paths never leak across tests."""
    win_locator.clear_locator_cache()
    yield
    win_locator.clear_locator_cache()


class TestIsWSL:
    """Tests for is_wsl() detection."""

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    def test_returns_false_on_windows(self):
        """Should return False on Windows."""
        assert is_wsl() is False

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    def test_returns_false_on_mac(self):
        """Should return False on macOS."""
        assert is_wsl() is False

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    @mock.patch(
        'builtins.open', mock.mock_open(read_data='Linux version 5.15.0')
    )
    def test_returns_false_on_native_linux(self):
        """Should return False on native Linux (not WSL)."""
        assert is_wsl() is False

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    @mock.patch(
        'builtins.open',
        mock.mock_open(
            read_data='Linux version 5.15.0-microsoft-standard-WSL2'
        ),
    )
    def test_returns_true_on_wsl2(self):
        """Should return True on WSL2."""
        assert is_wsl() is True

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    @mock.patch(
        'builtins.open',
        mock.mock_open(read_data='Linux version 4.19.104-microsoft-standard'),
    )
    def test_returns_true_on_wsl1(self):
        """Should return True on WSL1."""
        assert is_wsl() is True

    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    def test_returns_false_on_file_error(self):
        """Should return False if /proc/version cannot be read."""

        def raise_error(*args, **kwargs):
            raise OSError('File not found')

        with mock.patch('builtins.open', side_effect=raise_error):
            assert is_wsl() is False


class TestGetZiniaoHost:
    """Tests for get_ziniao_host() function."""

    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_returns_localhost_when_not_wsl(self, mock_is_wsl):
        """Should return 127.0.0.1 on non-WSL systems."""
        mock_is_wsl.return_value = False
        assert get_ziniao_host() == '127.0.0.1'

    @mock.patch('subprocess.run')
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_returns_gateway_ip_on_wsl(self, mock_is_wsl, mock_run):
        """Should return the Windows host IP (default gateway) on WSL."""
        mock_is_wsl.return_value = True
        mock_run.return_value = mock.MagicMock(
            returncode=0,
            stdout='default via 172.23.208.1 dev eth0\n',
        )
        assert get_ziniao_host() == '172.23.208.1'

    @mock.patch('subprocess.run')
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_falls_back_to_localhost_on_error(self, mock_is_wsl, mock_run):
        """Should fall back to 127.0.0.1 if gateway lookup fails."""
        mock_is_wsl.return_value = True
        mock_run.side_effect = Exception('ip command not found')
        assert get_ziniao_host() == '127.0.0.1'

    @mock.patch('subprocess.run')
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_falls_back_on_nonzero_returncode(self, mock_is_wsl, mock_run):
        """Should fall back to 127.0.0.1 if ip route returns error."""
        mock_is_wsl.return_value = True
        mock_run.return_value = mock.MagicMock(returncode=1, stdout='')
        assert get_ziniao_host() == '127.0.0.1'

    @mock.patch('subprocess.run')
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_falls_back_on_unparseable_output(self, mock_is_wsl, mock_run):
        """Should fall back to 127.0.0.1 on unexpected output."""
        mock_is_wsl.return_value = True
        mock_run.return_value = mock.MagicMock(
            returncode=0,
            stdout='some unexpected output\n',
        )
        assert get_ziniao_host() == '127.0.0.1'


class TestBuildLaunchCmd:
    """Tests for build_launch_cmd() function."""

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    def test_windows_command(self):
        """Should build correct command for Windows."""
        result = build_launch_cmd('C:\\Ziniao\\ziniao.exe', 8080)

        assert result == [
            'C:\\Ziniao\\ziniao.exe',
            '--run_type=web_driver',
            '--ipc_type=http',
            '--port=8080',
        ]

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    @mock.patch(
        'app.browser.ziniao_utils.find_ziniao_exe_windows',
        return_value='C:\\Program Files\\ziniao\\ziniao.exe',
    )
    def test_windows_bare_name_resolved(self, mock_find):
        """Default bare 'ziniao' should resolve to a real install path."""
        result = build_launch_cmd('ziniao', 8080)
        assert result[0] == 'C:\\Program Files\\ziniao\\ziniao.exe'
        assert '--run_type=web_driver' in result

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    @mock.patch(
        'app.browser.ziniao_utils.find_ziniao_exe_windows',
        return_value=None,
    )
    def test_windows_not_found_raises(self, mock_find):
        """Windows should raise a clear error when ziniao.exe is absent."""
        with pytest.raises(RuntimeError) as exc_info:
            build_launch_cmd('ziniao', 8080)
        assert 'Could not find ziniao.exe' in str(exc_info.value)

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    def test_mac_command(self):
        """Should build correct command for macOS."""
        result = build_launch_cmd('ziniao', 8080)

        assert result == [
            'open',
            '-a',
            'ziniao',
            '--args',
            '--run_type=web_driver',
            '--ipc_type=http',
            '--port=8080',
        ]

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_wsl_raises_error(self, mock_is_wsl):
        """WSL should raise RuntimeError guiding user to bat."""
        mock_is_wsl.return_value = True

        with pytest.raises(RuntimeError) as exc_info:
            build_launch_cmd('/path/to/ziniao', 8080)

        assert 'WSL cannot launch Ziniao' in str(exc_info.value)
        assert '/api/ziniao/launcher' in str(exc_info.value)

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', True)
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_native_linux_command(self, mock_is_wsl):
        """Should build correct command for native Linux."""
        mock_is_wsl.return_value = False

        result = build_launch_cmd('/opt/ziniao/ziniaobrowser', 8080)

        assert result == [
            '/opt/ziniao/ziniaobrowser',
            '--no-sandbox',
            '--run_type=web_driver',
            '--ipc_type=http',
            '--port=8080',
        ]

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    def test_unsupported_platform(self):
        """Should raise RuntimeError on unsupported platform."""
        with pytest.raises(RuntimeError) as exc_info:
            build_launch_cmd('/path/to/ziniao', 8080)

        assert 'Unsupported platform' in str(exc_info.value)


class TestFindZiniaoExeWindows:
    """Tests for find_ziniao_exe_windows()."""

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    def test_returns_none_off_windows(self):
        """Should return None on non-Windows platforms."""
        assert find_ziniao_exe_windows() is None

    def test_finds_in_program_files(self, tmp_path, monkeypatch):
        """Should resolve the standard Program Files install path.

        Drives the real locator over a fake filesystem: a 'C:' tree
        under tmp_path stands in for the Windows disk.
        """
        exe = tmp_path / 'c' / 'Program Files' / 'ziniao' / 'ziniao.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'MZ')

        def to_local(win):
            p = str(win).strip().strip('"')
            if len(p) >= 2 and p[1] == ':':
                rel = p[2:].replace('\\', '/')
                return str(tmp_path / p[0].lower() / rel.lstrip('/'))
            return p

        monkeypatch.setattr('app.browser.ziniao_utils.IS_WINDOWS', True)
        monkeypatch.setattr(win_locator, 'win_path_to_local', to_local)
        monkeypatch.setattr(win_locator, 'fixed_drives', lambda: ['C:'])
        expected = 'C:\\Program Files\\ziniao\\ziniao.exe'
        assert find_ziniao_exe_windows() == expected

    def test_uses_process_seen_before_kill(self, tmp_path, monkeypatch):
        """The launch flow kills the client BEFORE it needs the exe
        path, so a live lookup is always too late: the path remembered
        from the pre-kill process listing must be what launches — even
        though the process itself is long gone."""
        exe = tmp_path / 'd' / '紫鸟' / 'ziniao' / 'ziniao.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'MZ')

        def to_local(win):
            p = str(win).strip().strip('"')
            if len(p) >= 2 and p[1] == ':':
                rel = p[2:].replace('\\', '/')
                return str(tmp_path / p[0].lower() / rel.lstrip('/'))
            return p

        monkeypatch.setattr('app.browser.ziniao_utils.IS_WINDOWS', True)
        monkeypatch.setattr(win_locator, 'win_path_to_local', to_local)
        # No registry/shortcut/drive hints can find it: only the
        # remembered process path points here.
        monkeypatch.setattr(win_locator, 'fixed_drives', lambda: [])
        win_locator.remember_process_path(
            'ziniao', 'D:\\紫鸟\\ziniao\\ziniao.exe'
        )
        cmd = build_launch_cmd('ziniao', 8080)
        assert cmd[0] == 'D:\\紫鸟\\ziniao\\ziniao.exe'
        assert '--run_type=web_driver' in cmd


class TestIsZiniaoInstalledMac:
    """Tests for is_ziniao_installed_mac()."""

    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_found(self, mock_run):
        """Should return True when mdfind finds the app."""
        mock_run.return_value = mock.MagicMock(
            stdout='/Applications/ziniao.app\n'
        )
        assert is_ziniao_installed_mac() is True

    @mock.patch('app.browser.ziniao_utils._MAC_APP_DIRS', ())
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_not_found(self, mock_run):
        """Should return False when mdfind returns empty and no
        conventional Applications folder holds the app."""
        mock_run.return_value = mock.MagicMock(stdout='')
        assert is_ziniao_installed_mac() is False

    @mock.patch('app.browser.ziniao_utils._MAC_APP_DIRS', ())
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_conventional_dirs_fallback(self, mock_run, tmp_path):
        """Spotlight can miss an installed app (privacy exclusions,
        indexing disabled or unfinished) — the conventional
        Applications folders, which LaunchServices itself scans, are
        the fallback."""
        mock_run.return_value = mock.MagicMock(stdout='')
        apps = tmp_path / 'Applications'
        (apps / 'ziniao.app').mkdir(parents=True)
        with mock.patch('app.browser.ziniao_utils._MAC_APP_DIRS', (str(apps),)):
            assert is_ziniao_installed_mac() is True

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    def test_not_mac(self):
        """Should return False on non-Mac."""
        assert is_ziniao_installed_mac() is False

    @mock.patch('app.browser.ziniao_utils._MAC_APP_DIRS', ())
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_mdfind_error(self, mock_run):
        """Should return False if mdfind fails and the fallback dirs
        are empty."""
        mock_run.side_effect = Exception('mdfind error')
        assert is_ziniao_installed_mac() is False


class TestGetPlatform:
    """Tests for get_platform()."""

    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    def test_mac(self):
        assert get_platform() == 'mac'

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    def test_windows(self):
        assert get_platform() == 'windows'
