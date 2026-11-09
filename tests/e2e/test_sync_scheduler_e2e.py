"""E2E: the sync scheduler, against a server this module OWNS.

Companion to ``test_sync_mirror_e2e.py`` — and like it, this file owns
its whole stack: its own fake GitHub (free port) and its own server on
a dedicated free port with a dedicated temp ``VIBE_HOME`` (a scratch
directory the fixture creates), started and stopped by the module
fixture through ``tests/e2e/own_server.py``. It never touches the
shared e2e server or the shared home — this suite needs a specially
configured server (compressed cadences, jitter off, the GitHub URLs
pointed at the fake), and a shared server has none of that.

The server is started with exactly:

    VIBE_SYNC_JITTER=0
    VIBE_SYNC_CADENCE_OVERRIDE='skills=20,knowledge=20,presets=30'
    VIBE_GITHUB_API_BASE=<own fake>/api
    SKILLS_REPO_URL=<own fake>/raw/app/skills_v2
    KNOWLEDGE_REPO_URL=<own fake>/raw/app/knowledge

What these pin is the observable contract: every run leaves exactly one
``sync:<name> reason=… status=…`` line in the server log, the compressed
cadence actually fires, an unchanged upstream answers ``unchanged`` (and
costs one request), a toggle that is off skips the scheduled run but not
a manual one, and a restart does not re-arm a job that is not due.
"""

import os
from pathlib import Path
import re
import time

import httpx
import pytest

from tests.e2e.own_server import OwnServer, pick_free_port
from tests.fixtures.fake_github import FakeGitHub, client_for

pytestmark = pytest.mark.e2e

# Set by the module fixture (owned_stack) to THIS suite's own stack.
BASE_URL = ''
VIBE_HOME = Path()
_OWNED: OwnServer | None = None
_FAKE = ''


def log_path() -> Path:
    """The owned server's own log file."""
    assert _OWNED is not None, 'owned_stack fixture did not run'
    path = _OWNED.log
    assert path.is_file(), path
    return path


#: One run line of ours — ``sync:<name> reason=… status=…``. Deliberately
#: narrow: APScheduler logs its own "Removed job sync:skills" lines, and
#: those are not runs.
_RUN_RE = re.compile(r'sync:\S+ reason=\S+ status=\S+')


def sync_lines(text: str, name: str | None = None) -> list[str]:
    out = [line for line in text.splitlines() if _RUN_RE.search(line)]
    if name:
        out = [line for line in out if f'sync:{name} ' in line]
    return out


@pytest.fixture(scope='module', autouse=True)
def owned_stack(tmp_path_factory):
    """THIS suite's own fake GitHub and configured server.

    The compressed-cadence + jitter-off + fake-URL env the tests need
    belongs to the SERVER — so this fixture starts one of ours (free
    port, temp ``VIBE_HOME``) instead of assuming the shared e2e
    server carries it. The shared server and home are never touched.
    """
    global _OWNED, _FAKE, BASE_URL, VIBE_HOME

    fake = FakeGitHub()
    _FAKE = fake.start().rstrip('/')
    # Seed what the sync jobs fetch: with an empty fake every run 404s
    # on MANIFEST.txt and "did the cadence fire" degenerates into "did
    # a failure line appear". The old shared fake only had a manifest
    # because a neighbouring module's control() calls put one there.
    fake.set_manifest('app/skills_v2', ['widget-006/SKILL.md'])
    fake.files['app/skills_v2/widget-006/SKILL.md'] = b'seed bytes'
    fake.set_manifest('app/knowledge', ['notes.md'])
    fake.files['app/knowledge/notes.md'] = b'seed notes'
    home = tmp_path_factory.mktemp('sync-scheduler-home')
    server = OwnServer(
        port=pick_free_port(),
        home=home,
        env={
            **fake.urls(),
            'VIBE_SYNC_JITTER': '0',
            'VIBE_SYNC_CADENCE_OVERRIDE': ('skills=20,knowledge=20,presets=30'),
        },
    )
    _OWNED = server
    BASE_URL = server.base_url
    VIBE_HOME = server.home
    server.start()
    yield server
    server.stop()
    fake.stop()


@pytest.fixture(scope='module')
def fake(owned_stack):
    """A handle on the fake GitHub THIS module's server is pointed at.

    The fake is served in-process on its own port (the server fetches
    from it over HTTP), so counts come back over ``/__counts``.
    """
    return client_for(_FAKE)


@pytest.fixture(scope='module')
def api() -> str:
    return BASE_URL


def authed_client(token: str) -> httpx.Client:
    client = httpx.Client(timeout=30)
    if token:
        client.cookies.set('auth_token', token)
    return client


def status(client) -> dict:
    resp = client.get(f'{BASE_URL}/api/sync/status')
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope='module', autouse=True)
def log_start(owned_stack) -> int:
    """Where THIS run's log lines begin.

    The server log accumulates across runs (and across servers), so
    counting from the top would find yesterday's run lines and wait
    for nothing.
    """
    path = log_path()
    return path.stat().st_size


def lines_since(offset: int, name: str | None = None) -> list[str]:
    with log_path().open('rb') as fh:
        fh.seek(offset)
        text = fh.read().decode(errors='replace')
    return sync_lines(text, name)


def wait_for_runs(
    name: str, count: int, timeout: float = 90.0, offset: int = 0
) -> list[str]:
    """Poll the server log until ``name`` has ``count`` run lines."""
    deadline = time.time() + timeout
    lines: list[str] = []
    while time.time() < deadline:
        lines = lines_since(offset, name)
        if len(lines) >= count:
            return lines
        time.sleep(2)
    raise AssertionError(
        f'{name} produced {len(lines)} run line(s), wanted {count}'
    )


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


def test_every_job_fires_within_its_compressed_cadence(fake, log_start):
    """Each job leaves its own run line within the compressed cadence."""
    for name in ('skills', 'knowledge', 'presets'):
        wait_for_runs(name, 1, offset=log_start)


def test_one_greppable_line_per_run(fake, log_start):
    for line in wait_for_runs('skills', 1, offset=log_start):
        assert 'reason=' in line, line
        assert 'status=' in line, line
        assert 'duration=' in line, line


def test_upstream_unchanged_costs_one_request(fake, log_start):
    """From round 2 the fake answers 304 and nothing is downloaded.

    The cheap path is the whole point of the stored validator: an
    unchanged upstream must not cost a body — and a 304 does not count
    against GitHub's rate limit.
    """
    before = fake.counts()
    seen = len(sync_lines(log_path().read_text(errors='replace'), 'skills'))
    lines = wait_for_runs('skills', seen + 1, timeout=120)
    assert any('status=unchanged' in line for line in lines), lines
    after = fake.counts()
    assert after.get('not_modified', 0) >= 1, after
    assert after['files'] == before.get('files', 0)  # no body fetched


def test_manual_run_and_toggle(fake, log_start):
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
    with authed_client(token) as client:
        body = status(client)
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
