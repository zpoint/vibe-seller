"""Tests for Ziniao browser utility functions."""

from unittest import mock

import pytest

from app.browser.ziniao_utils import (
    build_launch_cmd,
    force_kill_ziniao,
    get_platform,
    get_ziniao_host,
    get_ziniao_status,
    is_wsl,
    is_ziniao_installed_mac,
    is_ziniao_process_running,
    kill_and_relaunch_ziniao,
)


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


class TestIsZiniaoProcessRunning:
    """Tests for is_ziniao_process_running() function."""

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('subprocess.run')
    def test_detects_running_on_windows(self, mock_run):
        """Should detect ziniao.exe in Windows tasklist."""
        mock_run.return_value = mock.MagicMock(
            stdout='ziniao.exe  1234 Console  1  100,000 K\n'
        )
        assert is_ziniao_process_running() is True

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('subprocess.run')
    def test_not_running_on_windows(self, mock_run):
        """Should return False when ziniao.exe not in tasklist."""
        mock_run.return_value = mock.MagicMock(
            stdout='INFO: No tasks are running.\n'
        )
        assert is_ziniao_process_running() is False

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    @mock.patch('os.path.isfile', return_value=True)
    @mock.patch('subprocess.run')
    def test_detects_running_on_wsl(self, mock_run, mock_isfile, mock_is_wsl):
        """Should detect ziniao.exe via cmd.exe from WSL."""
        mock_is_wsl.return_value = True
        mock_run.return_value = mock.MagicMock(
            stdout='ziniao.exe  1234 Console  1  100,000 K\n'
        )
        assert is_ziniao_process_running() is True

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl')
    def test_returns_false_on_native_linux(self, mock_is_wsl):
        """Should return False on native Linux."""
        mock_is_wsl.return_value = False
        assert is_ziniao_process_running() is False

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', True)
    @mock.patch('subprocess.run')
    def test_returns_false_on_error(self, mock_run):
        """Should return False if tasklist command fails."""
        mock_run.side_effect = Exception('command not found')
        assert is_ziniao_process_running() is False

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    @mock.patch('subprocess.run')
    def test_detects_running_on_mac(self, mock_run):
        """Should detect Ziniao via pgrep on Mac."""
        mock_run.return_value = mock.MagicMock(returncode=0)
        assert is_ziniao_process_running() is True
        mock_run.assert_called_once_with(
            ['pgrep', '-f', 'ziniao.app/Contents/MacOS'],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    @mock.patch('subprocess.run')
    def test_not_running_on_mac(self, mock_run):
        """Should return False when pgrep finds nothing."""
        mock_run.return_value = mock.MagicMock(returncode=1)
        assert is_ziniao_process_running() is False

    @mock.patch('app.browser.ziniao_utils.IS_WINDOWS', False)
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('app.browser.ziniao_utils.IS_LINUX', False)
    @mock.patch('subprocess.run')
    def test_mac_pgrep_error(self, mock_run):
        """Should return False if pgrep fails."""
        mock_run.side_effect = Exception('pgrep not found')
        assert is_ziniao_process_running() is False


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

    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_not_found(self, mock_run):
        """Should return False when mdfind returns empty."""
        mock_run.return_value = mock.MagicMock(stdout='')
        assert is_ziniao_installed_mac() is False

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    def test_not_mac(self):
        """Should return False on non-Mac."""
        assert is_ziniao_installed_mac() is False

    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_mdfind_error(self, mock_run):
        """Should return False if mdfind fails."""
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


@pytest.mark.unit
class TestGetZiniaoStatus:
    """Tests for get_ziniao_status()."""

    USER_INFO = {
        'company': 'test',
        'username': 'user',
        'password': 'pass',
    }

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    async def test_running_webdriver(self, mock_connect):
        """API responds 0 -> running_webdriver."""
        mock_connect.return_value = (
            {'statusCode': '0', 'browserList': []},
            '127.0.0.1',
        )
        result = await get_ziniao_status(16851, self.USER_INFO)
        assert result == {'status': 'running_webdriver'}

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    async def test_no_permission(self, mock_connect):
        """API responds -10003 -> no_permission."""
        mock_connect.return_value = (
            {'statusCode': '-10003', 'err': 'no perm'},
            '127.0.0.1',
        )
        result = await get_ziniao_status(16851, self.USER_INFO)
        assert result == {'status': 'no_permission'}

    @pytest.mark.asyncio
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=True,
    )
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    async def test_running_normal(self, mock_connect, mock_proc):
        """API unreachable + process found -> running_normal."""
        mock_connect.return_value = (None, '127.0.0.1')
        result = await get_ziniao_status(16851, self.USER_INFO)
        assert result == {'status': 'running_normal'}

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_installed_mac',
        return_value=True,
    )
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=False,
    )
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    async def test_not_running(self, mock_connect, mock_proc, mock_inst):
        """No API + no process + installed -> not_running."""
        mock_connect.return_value = (None, '127.0.0.1')
        result = await get_ziniao_status(16851, self.USER_INFO)
        assert result == {'status': 'not_running'}

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_installed_mac',
        return_value=False,
    )
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=False,
    )
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    async def test_not_installed(self, mock_connect, mock_proc, mock_inst):
        """No API + no process + not installed -> not_installed."""
        mock_connect.return_value = (None, '127.0.0.1')
        result = await get_ziniao_status(16851, self.USER_INFO)
        assert result == {'status': 'not_installed'}


@pytest.mark.unit
class TestForceKillZiniao:
    """Tests for force_kill_ziniao()."""

    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    def test_mac_uses_pkill(self, mock_run):
        """Mac should use pkill -9 with ziniao.app path."""
        force_kill_ziniao()
        mock_run.assert_called_once_with(
            ['pkill', '-9', '-f', 'ziniao.app/Contents/MacOS'],
            capture_output=True,
            timeout=10,
        )

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl', return_value=True)
    @mock.patch('os.path.isfile', return_value=True)
    @mock.patch('subprocess.run')
    def test_wsl_uses_taskkill(self, mock_run, mock_isfile, mock_is_wsl):
        """WSL should use taskkill.exe /F /IM ziniao.exe."""
        force_kill_ziniao()
        mock_run.assert_called_once_with(
            [
                '/mnt/c/Windows/System32/taskkill.exe',
                '/F',
                '/IM',
                'ziniao.exe',
            ],
            capture_output=True,
            timeout=10,
            cwd='/mnt/c/',
        )

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl', return_value=True)
    @mock.patch('os.path.isfile', return_value=False)
    def test_wsl_raises_if_taskkill_missing(self, mock_isfile, mock_is_wsl):
        """Should raise if taskkill.exe not found (WSL interop disabled)."""
        with pytest.raises(RuntimeError) as exc_info:
            force_kill_ziniao()
        assert 'taskkill.exe not found' in str(exc_info.value)
        assert 'WSL interop' in str(exc_info.value)

    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl', return_value=False)
    def test_unsupported_platform_raises(self, mock_is_wsl):
        """Should raise on unsupported platform."""
        with pytest.raises(RuntimeError) as exc_info:
            force_kill_ziniao()
        assert 'only supported on Mac and WSL' in str(exc_info.value)


@pytest.mark.unit
class TestKillAndRelaunchWSL:
    """Tests for kill_and_relaunch_ziniao() WSL behavior."""

    USER_INFO = {
        'company': 'test',
        'username': 'user',
        'password': 'pass',
    }

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl', return_value=True)
    @mock.patch('os.path.isfile', return_value=True)
    @mock.patch('subprocess.run')
    @mock.patch('subprocess.Popen')
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=False,
    )
    @mock.patch('asyncio.sleep', return_value=None)
    async def test_wsl_kills_and_returns_without_relaunch(
        self,
        mock_sleep,
        mock_proc_running,
        mock_popen,
        mock_run,
        mock_isfile,
        mock_is_wsl,
    ):
        """WSL: kills via taskkill, skips relaunch, returns True."""
        result = await kill_and_relaunch_ziniao(16851, 'ziniao', self.USER_INFO)
        assert result is True
        # taskkill was called
        mock_run.assert_called_once_with(
            [
                '/mnt/c/Windows/System32/taskkill.exe',
                '/F',
                '/IM',
                'ziniao.exe',
            ],
            capture_output=True,
            timeout=10,
            cwd='/mnt/c/',
        )
        # Popen (relaunch) was NOT called
        mock_popen.assert_not_called()

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.IS_MAC', False)
    @mock.patch('app.browser.ziniao_utils.is_wsl', return_value=True)
    @mock.patch('os.path.isfile', return_value=True)
    @mock.patch('subprocess.run')
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=True,
    )
    @mock.patch('asyncio.sleep', return_value=None)
    async def test_wsl_termination_timeout(
        self,
        mock_sleep,
        mock_proc_running,
        mock_run,
        mock_isfile,
        mock_is_wsl,
    ):
        """WSL: raises RuntimeError mentioning Task Manager on timeout."""
        with pytest.raises(RuntimeError) as exc_info:
            await kill_and_relaunch_ziniao(16851, 'ziniao', self.USER_INFO)
        assert 'Task Manager' in str(exc_info.value)
        assert 'Activity Monitor' not in str(exc_info.value)

    @pytest.mark.asyncio
    @mock.patch('app.browser.ziniao_utils.IS_MAC', True)
    @mock.patch('subprocess.run')
    @mock.patch('subprocess.Popen')
    @mock.patch(
        'app.browser.ziniao_utils.is_ziniao_process_running',
        return_value=False,
    )
    @mock.patch('app.browser.ziniao_utils.try_connect_ziniao')
    @mock.patch('asyncio.sleep', return_value=None)
    async def test_mac_kills_and_relaunches(
        self,
        mock_sleep,
        mock_connect,
        mock_proc_running,
        mock_popen,
        mock_run,
    ):
        """Mac: kills, relaunches, polls API — regression test."""
        mock_connect.return_value = (
            {'statusCode': '0'},
            '127.0.0.1',
        )
        result = await kill_and_relaunch_ziniao(16851, 'ziniao', self.USER_INFO)
        assert result is True
        # pkill was called
        mock_run.assert_called_once_with(
            ['pkill', '-9', '-f', 'ziniao.app/Contents/MacOS'],
            capture_output=True,
            timeout=10,
        )
        # Popen (relaunch) WAS called
        mock_popen.assert_called_once()
