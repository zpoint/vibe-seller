"""Workflow test: daemon reaper kills orphaned browser-use daemons.

Spawns real OS processes that mimic browser-use daemons, then
verifies the reaper kills orphans and spares active-task daemons.

Requires Unix (ps/pgrep); skipped on Windows.
"""

import asyncio
import os
import signal
import subprocess
import sys
import uuid

import pytest

from app.browser.daemon_reaper import reap_orphaned_daemons
from app.config import AI_BOT_USER_ID
from app.models.task import Task
from app.task_states import TaskStatus

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(os.name == 'nt', reason='Unix-only (pgrep/kill)'),
]

_WAIT_TIMEOUT = 5  # seconds to wait for process death


def _spawn_sigterm_resistant_daemon(task_id: str) -> subprocess.Popen:
    """Spawn a daemon that traps SIGTERM (like real browser-use).

    Real browser-use daemons trap SIGTERM and call
    ``browser_session.stop()`` which hangs on a dead CDP
    WebSocket.  This helper reproduces that behaviour so we can
    verify the reaper escalates to SIGKILL.
    """
    return subprocess.Popen(
        [
            sys.executable,
            '-c',
            'import signal, time; '
            'signal.signal(signal.SIGTERM, lambda *_: None); '
            'time.sleep(300)',
            'browser_use.skill_cli.daemon',
            f'--cdp-url=ws://127.0.0.1:9222/client-{task_id}',
        ],
    )


def _spawn_fake_daemon(task_id: str) -> subprocess.Popen:
    """Spawn a long-running process with task ID in args.

    Mimics ``browser_use.skill_cli.daemon --cdp-url
    ws://...client-<task_id>``.
    """
    return subprocess.Popen(
        [
            sys.executable,
            '-c',
            'import time; time.sleep(300)',
            # Fake args that match the reaper's pattern
            'browser_use.skill_cli.daemon',
            f'--cdp-url=ws://127.0.0.1:9222/client-{task_id}',
        ],
    )


def _wait_for_death(
    proc: subprocess.Popen, timeout: float = _WAIT_TIMEOUT
) -> bool:
    """Wait for process to exit. Returns True if it died."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _is_alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def _ensure_dead(proc: subprocess.Popen):
    """Reap the process, killing it first if still alive."""
    if _is_alive(proc):
        proc.kill()
    proc.wait()


@pytest.fixture
def fake_task_id():
    """A task ID that does NOT exist in the DB."""
    return '00000000-dead-dead-dead-000000000000'


@pytest.fixture
async def active_task_id(override_async_session):
    """Create a real task in RUNNING status and return its ID."""
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        title='Active task for reaper test',
        status=TaskStatus.RUNNING,
        created_by=AI_BOT_USER_ID,
    )
    async with override_async_session() as db:
        db.add(task)
        await db.commit()
    return task_id


class TestDaemonReaper:
    async def test_reaper_kills_orphaned_daemon(
        self,
        override_async_session,
        fake_task_id,
    ):
        """Daemon for a non-existent task should be killed."""
        proc = _spawn_fake_daemon(fake_task_id)
        try:
            assert _is_alive(proc)

            killed = await reap_orphaned_daemons()

            assert _wait_for_death(proc), (
                'Orphaned daemon should have been killed'
            )
            assert killed >= 1
        finally:
            _ensure_dead(proc)

    async def test_reaper_spares_active_task_daemon(self, active_task_id):
        """Daemon for an active (RUNNING) task should NOT be killed."""
        proc = _spawn_fake_daemon(active_task_id)
        try:
            assert _is_alive(proc)

            await reap_orphaned_daemons()
            # Brief pause then confirm still alive
            await asyncio.sleep(0.5)

            assert _is_alive(proc), 'Active task daemon should NOT be killed'
        finally:
            _ensure_dead(proc)

    async def test_reaper_kills_sigterm_resistant_daemon(
        self,
        override_async_session,
        fake_task_id,
    ):
        """Daemon that traps SIGTERM should be killed via SIGKILL."""
        proc = _spawn_sigterm_resistant_daemon(fake_task_id)
        try:
            # Wait for the process to start and register its
            # SIGTERM handler before we test resilience.
            await asyncio.sleep(0.5)
            assert _is_alive(proc)

            # Sanity: SIGTERM alone does NOT kill it.
            os.kill(proc.pid, signal.SIGTERM)
            # Poll to confirm it stays alive (break early if it
            # dies so we get a fast, clear assertion failure).
            for _ in range(20):
                await asyncio.sleep(0.1)
                if not _is_alive(proc):
                    break
            assert _is_alive(proc), 'Daemon should survive SIGTERM'

            # Reaper should escalate to SIGKILL.
            killed = await reap_orphaned_daemons()

            assert _wait_for_death(proc), (
                'Reaper should SIGKILL after SIGTERM timeout'
            )
            assert killed >= 1
        finally:
            _ensure_dead(proc)

    async def test_reaper_ignores_daemons_without_task_id(
        self,
        override_async_session,
    ):
        """Daemons without --cdp-url (e.g. aux sessions) are skipped."""
        proc = subprocess.Popen(
            [
                sys.executable,
                '-c',
                'import time; time.sleep(300)',
                'browser_use.skill_cli.daemon',
                '--session=demo-northshore-aux',
            ],
        )
        try:
            assert _is_alive(proc)

            await reap_orphaned_daemons()
            await asyncio.sleep(0.5)

            assert _is_alive(proc), 'Daemon without task ID should be skipped'
        finally:
            _ensure_dead(proc)


def _spawn_bh_daemon() -> subprocess.Popen:
    """Spawn a process whose cmdline matches the browser-use 0.13 daemon
    (``browser_harness.daemon``). Identity is carried in a pid file, not
    argv — so the reaper maps it via ``bu-<BU_NAME>.pid``."""
    return subprocess.Popen(
        [
            sys.executable,
            '-c',
            'import time; time.sleep(300)',
            '-m',
            'browser_harness.daemon',  # marker for find_processes_by_pattern
        ],
    )


class TestDaemonReaperPidFile:
    """browser-use 0.13 daemons: reaped via pid files keyed on BU_NAME.

    Also covers the resume-from-process-kill upgrade scenario: on boot
    the reaper kills orphans (dead tasks) but spares active-task daemons.
    """

    async def test_reaper_kills_orphaned_013_daemon(
        self, monkeypatch, override_async_session, tmp_path, fake_task_id
    ):
        monkeypatch.setattr('app.browser.bh_daemons.BH_RUNTIME_DIR', tmp_path)
        proc = _spawn_bh_daemon()
        bu_name = f'acme-store-{fake_task_id[:8]}'
        pf = tmp_path / f'bu-{bu_name}.pid'
        sock = tmp_path / f'bu-{bu_name}.sock'
        pf.write_text(str(proc.pid))
        sock.touch()
        try:
            killed = await reap_orphaned_daemons()
            assert _wait_for_death(proc), 'orphaned 0.13 daemon must die'
            assert killed >= 1
            # Runtime files for the reaped daemon are cleaned up.
            assert not pf.exists()
            assert not sock.exists()
        finally:
            _ensure_dead(proc)

    async def test_reaper_spares_active_013_daemon(
        self, monkeypatch, active_task_id, tmp_path
    ):
        monkeypatch.setattr('app.browser.bh_daemons.BH_RUNTIME_DIR', tmp_path)
        proc = _spawn_bh_daemon()
        bu_name = f'acme-store-{active_task_id[:8]}'
        pf = tmp_path / f'bu-{bu_name}.pid'
        pf.write_text(str(proc.pid))
        try:
            await reap_orphaned_daemons()
            await asyncio.sleep(0.5)
            assert _is_alive(proc), 'active-task 0.13 daemon must survive'
            assert pf.exists(), 'active daemon pid file must be kept'
        finally:
            _ensure_dead(proc)

    async def test_reaper_cleans_stale_pidfile_no_live_daemon(
        self, monkeypatch, override_async_session, tmp_path, fake_task_id
    ):
        """A pid file whose PID is not a live browser_harness.daemon is
        stale (crash / PID reuse) — deleted, nothing killed."""
        monkeypatch.setattr('app.browser.bh_daemons.BH_RUNTIME_DIR', tmp_path)
        bu_name = f'acme-store-{fake_task_id[:8]}'
        pf = tmp_path / f'bu-{bu_name}.pid'
        sock = tmp_path / f'bu-{bu_name}.sock'
        # A PID that is (almost certainly) not a live bh daemon.
        pf.write_text('999999')
        sock.touch()
        await reap_orphaned_daemons()
        assert not pf.exists(), 'stale pid file must be cleaned up'
        assert not sock.exists()

    async def test_reaper_ignores_013_daemon_without_task_suffix(
        self, monkeypatch, override_async_session, tmp_path
    ):
        """A bare-slug / aux BU_NAME (no -{8hex} task suffix) is a
        manual/aux session and must be skipped."""
        monkeypatch.setattr('app.browser.bh_daemons.BH_RUNTIME_DIR', tmp_path)
        proc = _spawn_bh_daemon()
        pf = tmp_path / 'bu-acme-store-aux.pid'
        pf.write_text(str(proc.pid))
        try:
            await reap_orphaned_daemons()
            await asyncio.sleep(0.5)
            assert _is_alive(proc), 'aux 0.13 daemon must be skipped'
        finally:
            _ensure_dead(proc)
