"""E2E: the mirror semantics + the schedule, against a real server.

Companion to ``test_sync_scheduler_e2e.py``. Same harness: the server
on :7811 with an isolated home, the fake GitHub out-of-process and
driven over its ``/__control`` plane.

This file RESTARTS the server mid-suite — that is how it gets a chosen
cadence, a chosen ``install_id`` and a chosen ``last_ok_at``. It stops
only what it starts: ``kill -TERM`` on the pid file, never ``stop.sh``
(that also stops port clients and reaps every task agent on the box).
"""

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7811')
PORT = int(os.environ.get('E2E_PORT') or BASE_URL.rsplit(':', 1)[-1] or 7811)
FAKE = os.environ.get('E2E_FAKE_GITHUB_URL', 'http://127.0.0.1:7812').rstrip(
    '/'
)
VIBE_HOME = Path(
    os.environ.get('E2E_VIBE_HOME') or os.environ.get('VIBE_HOME') or ''
)
SKILLS = VIBE_HOME / '.claude' / 'skills'
RUN_RE = re.compile(r'sync:\S+ reason=\S+ status=\S+')
# While these rows run, nothing ticks on its own: every sync is one the
# test asked for, so `status == 'updated'` means THIS run wrote it.
LONG = 'skills=3600,knowledge=3600,presets=3600,email=3600,daemon_reaper=3600'


def control(**body) -> None:
    httpx.post(
        f'{FAKE}/__control',
        headers={'x-control': json.dumps(body)},
        timeout=10,
    ).raise_for_status()


def counts() -> dict:
    return httpx.get(f'{FAKE}/__counts', timeout=10).json()['counts']


def server_log() -> Path:
    explicit = os.environ.get('E2E_SERVER_LOG')
    return (
        Path(explicit)
        if explicit
        else sorted(Path('logs').glob('backend_*.log'))[-1]
    )


def run_lines(name: str | None = None) -> list[str]:
    lines = [
        ln
        for ln in server_log().read_text(errors='replace').splitlines()
        if RUN_RE.search(ln)
    ]
    return [ln for ln in lines if f'sync:{name} ' in ln] if name else lines


def wait_for(name: str, count: int, timeout: float = 90) -> list[str]:
    deadline = time.time() + timeout
    lines: list[str] = []
    while time.time() < deadline:
        lines = run_lines(name)
        if len(lines) >= count:
            return lines
        time.sleep(2)
    raise AssertionError(f'{name}: {len(lines)} run lines, wanted {count}')


def sync_job(name: str, timeout: float = 90) -> dict:
    resp = httpx.post(f'{BASE_URL}/api/sync/{name}/run', timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def status() -> dict:
    return httpx.get(f'{BASE_URL}/api/sync/status', timeout=10).json()


def job_row(name: str) -> dict:
    return next(j for j in status()['jobs'] if j['name'] == name)


def wait_health(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if (
                httpx.get(f'{BASE_URL}/api/health', timeout=2).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise AssertionError(f'server on {BASE_URL} did not come up')


def restart_server(env: dict | None = None) -> None:
    """Restart the server under test. NEVER plain ``stop.sh``."""
    pidfile = Path(f'.pids_{PORT}')
    if pidfile.exists():
        pid = int(pidfile.read_text().strip() or 0)
        if pid:
            kids = subprocess.run(
                ['pgrep', '-P', str(pid)], capture_output=True, text=True
            ).stdout.split()
            os.kill(pid, signal.SIGTERM)
            for kid in kids:
                try:
                    os.kill(int(kid), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(1)
        pidfile.unlink(missing_ok=True)
    subprocess.run(
        ['./start.sh', str(PORT)],
        env={**os.environ, **(env or {})},
        check=False,
        capture_output=True,
    )
    wait_health()


def set_sync_state(job: str, *, last_ok_at: str) -> None:
    """Write the job's ``last_ok_at`` the way an operator's DB edit would."""
    db = sqlite3.connect(VIBE_HOME / 'data' / 'vibe_seller.db')
    try:
        db.execute(
            'UPDATE sync_state SET last_ok_at = ? WHERE job_name = ?',
            (last_ok_at, job),
        )
        db.commit()
    finally:
        db.close()


def install_id() -> str:
    return (VIBE_HOME / 'data' / 'install_id').read_text().strip()


def set_install_id(value: str) -> None:
    (VIBE_HOME / 'data' / 'install_id').write_text(value)


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture(scope='module', autouse=True)
def quiet_cadence():
    restart_server(
        env={'VIBE_SYNC_CADENCE_OVERRIDE': LONG, 'VIBE_SYNC_JITTER': '0'}
    )
    yield


# ── 1. an upstream change lands ───────────────────────────────────


def test_upstream_change_lands_on_disk():
    before = job_row('skills').get('version')
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md'],
        content={'widget-006/SKILL.md': 'brand new bytes'},
    )
    control(action='upstream_changed')
    body = sync_job('skills')

    assert body['status'] == 'updated', body
    assert (SKILLS / 'widget-006' / 'SKILL.md').read_text() == (
        'brand new bytes'
    )
    row = job_row('skills')
    assert row['version'] and row['version'] != before


# ── 2. an upstream delete lands; a user skill survives ────────────


def test_upstream_delete_removes_what_it_dropped():
    (SKILLS / 'doomed-skill').mkdir(parents=True, exist_ok=True)
    (SKILLS / 'doomed-skill' / 'SKILL.md').write_text('gone soon')
    meta = SKILLS / '.sync_meta.json'
    payload = json.loads(meta.read_text()) if meta.exists() else {}
    payload.setdefault('synced_skills', []).extend([
        'doomed-skill',
        'widget-006',
    ])
    payload['synced_skills'] = sorted(set(payload['synced_skills']))
    meta.write_text(json.dumps(payload))
    (SKILLS / 'widget-006').mkdir(parents=True, exist_ok=True)
    (SKILLS / 'widget-006' / 'EXTRA.md').write_text('gone soon too')
    (SKILLS / 'user-made').mkdir(parents=True, exist_ok=True)
    (SKILLS / 'user-made' / 'SKILL.md').write_text('mine')
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md'],
        content={'widget-006/SKILL.md': 'just this one'},
    )
    control(action='upstream_changed')
    body = sync_job('skills')

    assert body['status'] == 'updated', body
    # the dropped whole skill AND the dropped file are gone on disk
    assert not (SKILLS / 'doomed-skill').exists()
    assert not (SKILLS / 'widget-006' / 'EXTRA.md').exists()
    # what sync does not own is untouched
    assert (SKILLS / 'user-made' / 'SKILL.md').read_text() == 'mine'
    assert (SKILLS / 'widget-006' / 'SKILL.md').read_text() == ('just this one')
    assert any('deleted=' in ln for ln in run_lines('skills'))


# ── 3. a local edit: kept on a tick, reverted when upstream moves ──


def test_a_local_edit_survives_an_unchanged_upstream():
    """A TICK whose probe says "unchanged" must not touch the disk."""
    target = SKILLS / 'widget-006' / 'SKILL.md'
    target.write_text('locally edited')
    restart_server(
        env={'VIBE_SYNC_CADENCE_OVERRIDE': 'skills=5', 'VIBE_SYNC_JITTER': '0'}
    )
    try:
        before = len(run_lines('skills'))
        wait_for('skills', before + 1, timeout=90)
        assert target.read_text() == 'locally edited'
    finally:
        restart_server(
            env={'VIBE_SYNC_CADENCE_OVERRIDE': LONG, 'VIBE_SYNC_JITTER': '0'}
        )


def test_a_local_edit_is_reverted_when_upstream_moves():
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md'],
        content={'widget-006/SKILL.md': 'upstream wins'},
    )
    control(action='upstream_changed')
    body = sync_job('skills')

    assert body['status'] == 'updated', body
    assert (SKILLS / 'widget-006' / 'SKILL.md').read_text() == ('upstream wins')


# ── 4. the mass-delete guard ──────────────────────────────────────


def test_an_empty_manifest_deletes_nothing():
    before = sorted(p.name for p in SKILLS.iterdir() if p.is_dir())
    control(action='set_manifest', files=[], content={})
    control(action='upstream_changed')
    body = sync_job('skills')

    assert body['status'] == 'failed', body
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == before


# ── 5. a failed download never deletes the file ───────────────────


def test_a_failed_download_keeps_the_local_file():
    target = SKILLS / 'widget-006' / 'SKILL.md'
    target.write_text('keep these bytes')
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md', 'widget-006/EXTRA.md'],
        content={'widget-006/EXTRA.md': 'extra'},
    )
    control(action='fail_path', path='widget-006/SKILL.md')
    control(action='upstream_changed')
    try:
        body = sync_job('skills')
        assert body['status'] == 'failed', body
        assert target.read_text() == 'keep these bytes'
        assert any('status=failed' in ln for ln in run_lines('skills'))
    finally:
        control(action='clear_failures')


# ── 6. needs_restart end to end, including the restart ────────────


def test_a_gate_change_needs_a_restart_until_the_server_restarts():
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md', 'widget-006/gates/gate_one.py'],
        content={
            'widget-006/SKILL.md': 'doc',
            'widget-006/gates/gate_one.py': 'def check(*a, **k): return None',
        },
    )
    control(action='upstream_changed')
    sync_job('skills')

    assert job_row('skills')['needs_restart'] is True
    assert status()['needs_restart'] is True

    restart_server(
        env={'VIBE_SYNC_CADENCE_OVERRIDE': LONG, 'VIBE_SYNC_JITTER': '0'}
    )
    assert job_row('skills')['needs_restart'] is False
    assert status()['needs_restart'] is False


# ── 7. live schedule twist ────────────────────────────────────────


def test_twisting_last_ok_at_moves_the_next_run_and_never_duplicates():
    job = 'knowledge'
    anchor = utcnow()
    set_sync_state(job, last_ok_at=anchor)
    restart_server(
        env={
            'VIBE_SYNC_CADENCE_OVERRIDE': 'knowledge=600',
            'VIBE_SYNC_JITTER': '0',
        }
    )
    row = job_row(job)
    expected = datetime.fromisoformat(row['last_ok_at']) + timedelta(
        seconds=600
    )
    assert row['next_run_at'][:16] == expected.isoformat()[:16], row
    assert len([j for j in status()['jobs'] if j['name'] == job]) == 1


# ── 8. restart semantics ──────────────────────────────────────────


def test_not_overdue_restart_does_not_tick_and_overdue_ticks_at_once():
    job = 'knowledge'
    set_sync_state(job, last_ok_at=utcnow())
    restart_server(
        env={
            'VIBE_SYNC_CADENCE_OVERRIDE': 'knowledge=3600',
            'VIBE_SYNC_JITTER': '0',
        }
    )
    row = job_row(job)
    before_run = row['last_run_at']
    time.sleep(8)
    assert job_row(job)['last_run_at'] == before_run, job_row(job)

    set_sync_state(job, last_ok_at='2020-01-01T00:00:00+00:00')
    before = len(run_lines(job))
    restart_server(
        env={
            'VIBE_SYNC_CADENCE_OVERRIDE': 'knowledge=3600',
            'VIBE_SYNC_JITTER': '0',
        }
    )
    wait_for(job, before + 1, timeout=60)


# ── 9. per-install offset ─────────────────────────────────────────


def test_the_offset_is_per_install_and_matches_the_formula():
    job = 'skills'
    window = 3600  # DAILY jitter window
    anchor = utcnow()
    set_sync_state(job, last_ok_at=anchor)
    env = {
        'VIBE_SYNC_CADENCE_OVERRIDE': 'skills=86400',
        'VIBE_SYNC_JITTER': '1',
    }
    restart_server(env=env)
    first_id, first = install_id(), job_row(job)['next_run_at']

    set_install_id('another-install-id')
    try:
        restart_server(env=env)
        second_id, second = install_id(), job_row(job)['next_run_at']
    finally:
        set_install_id(first_id)

    def expected(install: str) -> str:
        digest = hashlib.sha256(f'{install}:{job}'.encode()).hexdigest()
        offset = int(digest[:8], 16) % window
        base = datetime.fromisoformat(anchor) + timedelta(seconds=86400)
        return (base + timedelta(seconds=offset)).isoformat()

    assert first_id != second_id
    assert first != second, 'two installs must not share their offset'
    assert first[:16] == expected(first_id)[:16], (first, expected(first_id))
    assert second[:16] == expected(second_id)[:16], (
        second,
        expected(second_id),
    )


# ── 10. concurrent task launches cost zero GitHub requests ────────


def test_concurrent_task_launches_do_not_poll_github():
    """N tasks at once share the in-flight sync; they never start one."""
    before = counts().get('commits', 0)
    token = os.environ.get('E2E_AUTH_TOKEN', '')
    codes = []
    with httpx.Client(timeout=60) as client:
        if token:
            client.cookies.set('auth_token', token)
        store = client.post(
            f'{BASE_URL}/api/stores',
            json={
                'name': 'launches',
                'platform': 'amazon',
                'countries': ['US'],
                'browser_backend': 'chrome',
            },
        )
        store_id = store.json().get('id') if store.status_code < 300 else None
        for i in range(5):
            codes.append(
                client.post(
                    f'{BASE_URL}/api/tasks',
                    json={
                        'title': f'launch {i}',
                        'description': 'concurrent launch probe',
                        'store_id': store_id,
                    },
                ).status_code
            )
    assert any(c < 300 for c in codes), codes
    time.sleep(3)
    assert counts().get('commits', 0) == before, counts()


# ── 11. ads authorization fires the job immediately ───────────────


def test_ads_authorization_runs_the_ads_skill_at_once():
    before = len(run_lines('ads_skill'))
    token = os.environ.get('E2E_AUTH_TOKEN', '')
    with httpx.Client(timeout=60) as client:
        if token:
            client.cookies.set('auth_token', token)
        client.post(
            f'{BASE_URL}/api/stores',
            json={
                'name': 'acme-api-store',
                'platform': 'amazon',
                'countries': ['US'],
                'browser_backend': 'chrome',
            },
        )
        resp = client.post(f'{BASE_URL}/api/ads/stores/sync', json={})
    if resp.status_code >= 300:
        pytest.skip(f'ads service not configured here: {resp.status_code}')
    if not any(
        'configured'
        in json.dumps(
            httpx.get(f'{BASE_URL}/api/ads/config', timeout=10).json()
        )
        for _ in [0]
    ):
        pytest.skip('no ads service bound')
    wait_for('ads_skill', before + 1, timeout=60)
    assert any('reason=event' in ln for ln in run_lines('ads_skill'))


# ── 12. the FAST jobs keep ticking ────────────────────────────────


def test_email_and_the_daemon_reaper_fire():
    restart_server(
        env={
            'VIBE_SYNC_CADENCE_OVERRIDE': 'email=5,daemon_reaper=5',
            'VIBE_SYNC_JITTER': '0',
        }
    )
    seen = set()
    deadline = time.time() + 90
    while time.time() < deadline and {'email', 'daemon_reaper'} - seen:
        for name in ('email', 'daemon_reaper'):
            if run_lines(name):
                seen.add(name)
        time.sleep(2)
    assert {'email', 'daemon_reaper'} <= seen, seen
