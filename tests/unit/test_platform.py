"""Tests for app.platform cross-platform abstractions.

These are @pytest.mark.unit tests that run on ALL platforms.
They mock IS_WINDOWS to test both code paths regardless of
the actual OS.
"""

import os
from pathlib import Path
import socket
from unittest.mock import AsyncMock, patch

import psutil
import pytest

import app.platform as plat

pytestmark = pytest.mark.unit


# -- venv path helpers ------------------------------------------------


class TestVenvBinDir:
    def test_unix(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', False)
        result = plat.venv_bin_dir(Path('/x/.venv'))
        assert result.name == 'bin'

    def test_windows(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        result = plat.venv_bin_dir(Path('/x/.venv'))
        assert result.name == 'Scripts'


class TestVenvPython:
    def test_unix(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', False)
        p = plat.venv_python(Path('/x/.venv'))
        assert p == Path('/x/.venv/bin/python3')

    def test_windows(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        p = plat.venv_python(Path('/x/.venv'))
        assert p == Path('/x/.venv/Scripts/python.exe')


class TestVenvExecutable:
    def test_unix_plain(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', False)
        assert plat.venv_executable(Path('/v'), 'uv') == Path('/v/bin/uv')

    def test_unix_pip3(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', False)
        assert plat.venv_executable(Path('/v'), 'pip3') == Path('/v/bin/pip3')

    def test_windows_plain(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        assert plat.venv_executable(Path('/v'), 'uv') == Path(
            '/v/Scripts/uv.exe'
        )

    def test_windows_pip3_maps_to_pip(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        assert plat.venv_executable(Path('/v'), 'pip3') == Path(
            '/v/Scripts/pip.exe'
        )

    def test_windows_python3_maps_to_python(self, monkeypatch):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        assert plat.venv_executable(Path('/v'), 'python3') == Path(
            '/v/Scripts/python.exe'
        )


class TestAgentVenvPython:
    """The agent venv must reuse the bundled interpreter when one is
    declared, instead of letting `uv venv` download a second Python."""

    def test_reuses_bundled_python_when_set(self, monkeypatch, tmp_path):
        bundled = tmp_path / 'python' / 'python.exe'
        bundled.parent.mkdir(parents=True)
        bundled.write_text('')  # must exist on disk
        monkeypatch.setenv('VIBE_SELLER_BUNDLED_PYTHON', str(bundled))
        assert plat.agent_venv_python() == str(bundled)

    def test_falls_back_to_version_when_unset(self, monkeypatch):
        monkeypatch.delenv('VIBE_SELLER_BUNDLED_PYTHON', raising=False)
        assert plat.agent_venv_python() == '3.11'

    def test_falls_back_when_path_missing(self, monkeypatch, tmp_path):
        # Declared but the file does not exist → don't trust it.
        monkeypatch.setenv(
            'VIBE_SELLER_BUNDLED_PYTHON', str(tmp_path / 'nope.exe')
        )
        assert plat.agent_venv_python() == '3.11'


# -- PATH helpers -----------------------------------------------------


class TestPrependToPath:
    def test_prepends_single_dir(self):
        env = {'PATH': '/usr/bin'}
        plat.prepend_to_path(env, Path('/a'))
        parts = env['PATH'].split(plat.os.pathsep)
        assert parts[0] == '/a'
        assert parts[-1] == '/usr/bin'

    def test_prepends_multiple_dirs(self):
        env = {'PATH': '/usr/bin'}
        plat.prepend_to_path(env, Path('/a'), Path('/b'))
        parts = env['PATH'].split(plat.os.pathsep)
        assert parts[0] == '/a'
        assert parts[1] == '/b'
        assert parts[2] == '/usr/bin'

    def test_empty_path(self):
        env = {}
        plat.prepend_to_path(env, Path('/a'))
        assert env['PATH'] == '/a'


# -- Process management -----------------------------------------------


class TestIsProcessAlive:
    def test_alive(self, monkeypatch):
        monkeypatch.setattr(psutil, 'pid_exists', lambda pid: True)
        assert plat.is_process_alive(1234) is True

    def test_dead(self, monkeypatch):
        monkeypatch.setattr(psutil, 'pid_exists', lambda pid: False)
        assert plat.is_process_alive(1234) is False


class TestKillProcess:
    @pytest.mark.asyncio
    async def test_already_dead(self):
        with patch.object(
            psutil, 'Process', side_effect=psutil.NoSuchProcess(999)
        ):
            result = await plat.kill_process(999)
            assert result is True

    @pytest.mark.asyncio
    async def test_dies_after_terminate(self):
        mock_proc = AsyncMock()
        mock_proc.terminate = lambda: None
        with (
            patch.object(psutil, 'Process', return_value=mock_proc),
            patch.object(psutil, 'pid_exists', return_value=False),
        ):
            result = await plat.kill_process(123, timeout=0.1)
            assert result is True


class TestFindProcessesByPattern:
    @pytest.mark.asyncio
    async def test_finds_matching(self):
        class FakeProc:
            info = {
                'pid': 42,
                'cmdline': ['python', '-m', 'browser_use.skill_cli.daemon'],
            }

        with patch.object(psutil, 'process_iter', return_value=[FakeProc()]):
            result = await plat.find_processes_by_pattern(
                'browser_use.skill_cli.daemon'
            )
            assert 42 in result

    @pytest.mark.asyncio
    async def test_skips_non_matching(self):
        class FakeProc:
            info = {'pid': 99, 'cmdline': ['python', 'other.py']}

        with patch.object(psutil, 'process_iter', return_value=[FakeProc()]):
            result = await plat.find_processes_by_pattern(
                'browser_use.skill_cli.daemon'
            )
            assert len(result) == 0


class TestFindPidListeningOnPort:
    """Port-based lookup is what makes `vibe-seller stop` (and the tray
    'restart') reliable when the pid file is missing/stale."""

    def test_finds_own_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            assert plat.find_pid_listening_on_port(port) == os.getpid()
        finally:
            s.close()

    def test_none_when_nobody_listening(self):
        # Bind to grab a free port, then close so no LISTEN socket remains.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        assert plat.find_pid_listening_on_port(port) is None


# -- File permissions -------------------------------------------------


class TestSafeChmod:
    def test_unix_calls_chmod(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plat, 'IS_WINDOWS', False)
        f = tmp_path / 'test.txt'
        f.touch()
        plat.safe_chmod(f, 0o600)
        assert f.stat().st_mode & 0o777 == 0o600

    def test_windows_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plat, 'IS_WINDOWS', True)
        f = tmp_path / 'test.txt'
        f.touch()
        plat.safe_chmod(f, 0o600)
        # The key test is that no exception is raised.
