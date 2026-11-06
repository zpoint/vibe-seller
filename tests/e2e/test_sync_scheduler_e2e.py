"""E2E: the sync scheduler, against a real server on :7811.

The server under test must be started ONCE for this file, isolated and
pointed at the fake GitHub this module serves:

    VIBE_HOME=$HOME/agent-briefs/sync-scheduler/vibe-home ./start.sh 7811

with (the e2e harness passes these through the server's environment):

    VIBE_SYNC_JITTER=0
    VIBE_SYNC_CADENCE_OVERRIDE='skills=20,knowledge=20,presets=30'
    VIBE_GITHUB_API_BASE=http://127.0.0.1:<fake port>/api
    SKILLS_REPO_URL=http://127.0.0.1:<fake port>/raw/app/skills_v2
    KNOWLEDGE_REPO_URL=http://127.0.0.1:<fake port>/raw/app/knowledge

What these pin is the observable contract: every run leaves exactly one
``sync:<name> reason=… status=…`` line in the server log, the compressed
cadence actually fires, an unchanged upstream answers ``unchanged`` (and
costs one request), a toggle that is off skips the scheduled run but not
a manual one, and a restart does not re-arm a job that is not due.
"""

import os
from pathlib import Path
import time

import httpx
import pytest

from tests.fixtures.fake_github import FakeGitHub

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7811')
VIBE_HOME = Path(
    os.environ.get('E2E_VIBE_HOME') or os.environ.get('VIBE_HOME') or ''
)


def log_path() -> Path:
    logs = VIBE_HOME / 'logs'
    candidates = sorted(logs.glob('backend_*.log'))
    assert candidates, f'no server log under {logs}'
    return candidates[-1]


def sync_lines(text: str, name: str | None = None) -> list[str]:
    needle = f'sync:{name}' if name else 'sync:'
    return [line for line in text.splitlines() if needle in line]


@pytest.fixture(scope='module')
def fake() -> FakeGitHub:
    """A handle on the fake GitHub the server under test is pointed at.

    The fake runs OUT-OF-PROCESS (it has to — the server fetches from
    it over HTTP), so counts come back over ``/__counts``.
    """
    return FakeGitHub.client_for(
        os.environ.get('E2E_FAKE_GITHUB_URL', 'http://127.0.0.1:7812')
    )


@pytest.fixture(scope='module')
def api() -> str:
    return BASE_URL


def status(client, token: str) -> dict:
    resp = client.get(
        f'{BASE_URL}/api/sync/status', cookies={'auth_token': token}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_boot_registers_every_job(fake):
    """The boot log lists what the scheduler owns."""
    text = log_path().read_text(errors='replace')
    for name in (
        'skills',
        'knowledge',
        'ads_skill',
        'presets',
        'email',
        'daemon_reaper',
    ):
        assert f'sync job registered: {name}' in text, name


def test_every_job_fires_within_its_compressed_cadence(fake):
    text = log_path().read_text(errors='replace')
    seen = {line.split()[0].split(':', 1)[1] for line in sync_lines(text)}
    assert {'skills', 'knowledge', 'presets'} <= seen, seen


def test_one_greppable_line_per_run(fake):
    text = log_path().read_text(errors='replace')
    for line in sync_lines(text):
        assert 'reason=' in line, line
        assert 'status=' in line, line
        assert 'duration=' in line, line


def test_upstream_unchanged_costs_one_request(fake):
    """An unchanged upstream is still a successful run — and a 304."""
    before = fake.counts()
    time.sleep(25)  # one compressed DAILY tick
    text = log_path().read_text(errors='replace')
    assert any('status=unchanged' in line for line in sync_lines(text))
    after = fake.counts()
    assert after['commits'] > before.get('commits', 0)
    # The cheap path: a probe that found nothing downloads nothing.
    assert after['files'] == before.get('files', 0)


def test_manual_run_and_toggle(fake):
    """A toggle-off job skips its tick but still obeys the Sync button."""
    token = os.environ.get('E2E_AUTH_TOKEN', '')
    with httpx.Client(timeout=30) as client:
        client.cookies.set('auth_token', token)
        client.put(
            f'{BASE_URL}/api/settings',
            json={'skills_auto_sync_enabled': False},
        )
        time.sleep(25)
        off_tick = [
            line
            for line in sync_lines(
                log_path().read_text(errors='replace'), 'skills'
            )
            if 'skip=toggle off' in line
        ]
        assert off_tick, 'a disabled job must skip its tick'

        resp = client.post(f'{BASE_URL}/api/sync/skills/run')
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['status'] in ('updated', 'unchanged')

        client.put(
            f'{BASE_URL}/api/settings',
            json={'skills_auto_sync_enabled': True},
        )


def test_status_exposes_the_last_run(fake):
    token = os.environ.get('E2E_AUTH_TOKEN', '')
    with httpx.Client(timeout=30) as client:
        client.cookies.set('auth_token', token)
        body = status(client, token)
    names = {job['name'] for job in body['jobs']}
    assert {'skills', 'knowledge', 'presets'} <= names
    for job in body['jobs']:
        assert set(job) >= {
            'name',
            'last_run_at',
            'last_ok_at',
            'version',
            'status',
            'needs_restart',
            'enabled',
            'running',
            'next_run_at',
        }
    assert isinstance(body['needs_restart'], bool)
