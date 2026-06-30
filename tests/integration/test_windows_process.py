"""Windows-specific process management integration tests.

Marked @pytest.mark.windows — only runs on Windows or with
``--windows`` flag.  These tests spawn real processes and
verify cross-platform kill/find logic.
"""

import asyncio
import subprocess
import sys

import pytest

from app.platform import (
    find_processes_by_pattern,
    is_process_alive,
    kill_process,
)

pytestmark = [pytest.mark.integration, pytest.mark.windows]

# Unique marker so we only find our test processes
_TEST_MARKER = '__vibe_seller_test_windows_proc__'


def _spawn_sleeper() -> subprocess.Popen:
    """Spawn a long-running Python process with a unique marker."""
    return subprocess.Popen(
        [
            sys.executable,
            '-c',
            f'import time; __marker__ = "{_TEST_MARKER}"; time.sleep(300)',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class TestWindowsKillProcess:
    async def test_kill_running_process(self):
        proc = _spawn_sleeper()
        try:
            assert is_process_alive(proc.pid)
            result = await kill_process(proc.pid, timeout=5.0)
            assert result is True
            # Give OS a moment to reap
            await asyncio.sleep(0.5)
            assert not is_process_alive(proc.pid)
        finally:
            proc.kill()
            proc.wait()

    async def test_kill_already_dead(self):
        proc = _spawn_sleeper()
        proc.kill()
        proc.wait()
        # Should not raise
        result = await kill_process(proc.pid)
        assert result is True


class TestWindowsFindProcesses:
    async def test_find_by_pattern(self):
        proc = _spawn_sleeper()
        try:
            # Give psutil a moment to see the process
            await asyncio.sleep(0.5)
            found = await find_processes_by_pattern(_TEST_MARKER)
            assert proc.pid in found
        finally:
            proc.kill()
            proc.wait()

    async def test_not_found(self):
        found = await find_processes_by_pattern('__nonexistent_pattern_12345__')
        assert len(found) == 0
