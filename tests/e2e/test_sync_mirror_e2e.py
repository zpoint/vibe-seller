"""E2E: the mirror semantics against a real server + a fake GitHub.

Companion to ``test_sync_scheduler_e2e.py`` (scheduling); these are
about what lands on DISK. Same harness: the server on :7811 with an
isolated home, the fake GitHub started out-of-process and driven over
its ``/__control`` plane.

Stop the server ONLY with ``kill -TERM $(cat .pids_7811)``.
"""

import json
import os
from pathlib import Path
import re
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7811')
FAKE = os.environ.get('E2E_FAKE_GITHUB_URL', 'http://127.0.0.1:7812').rstrip(
    '/'
)
VIBE_HOME = Path(
    os.environ.get('E2E_VIBE_HOME') or os.environ.get('VIBE_HOME') or ''
)
SKILLS = VIBE_HOME / '.claude' / 'skills'
KNOWLEDGE = VIBE_HOME / 'knowledge' / 'project'
RUN_RE = re.compile(r'sync:\S+ reason=\S+ status=\S+')


def control(**body) -> None:
    httpx.post(
        f'{FAKE}/__control',
        headers={'x-control': json.dumps(body)},
        timeout=10,
    ).raise_for_status()


def counts() -> dict:
    return httpx.get(f'{FAKE}/__counts', timeout=10).json()['counts']


def run_lines(name: str | None = None) -> list[str]:
    explicit = os.environ.get('E2E_SERVER_LOG')
    root = Path(explicit).parent if explicit else Path('logs')
    log = sorted(root.glob('backend_*.log'))[-1]
    lines = [
        ln
        for ln in log.read_text(errors='replace').splitlines()
        if RUN_RE.search(ln)
    ]
    if name:
        lines = [ln for ln in lines if f'sync:{name} ' in ln]
    return lines


def wait_for(name: str, count: int, timeout: float = 90) -> list[str]:
    deadline = time.time() + timeout
    lines: list[str] = []
    while time.time() < deadline:
        lines = run_lines(name)
        if len(lines) >= count:
            return lines
        time.sleep(2)
    raise AssertionError(f'{name}: {len(lines)} run lines, wanted {count}')


def sync_job(name: str, timeout: float = 60) -> dict:
    resp = httpx.post(f'{BASE_URL}/api/sync/{name}/run', timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def status() -> dict:
    return httpx.get(f'{BASE_URL}/api/sync/status', timeout=10).json()


# ── 1. an upstream change lands ───────────────────────────────────


def test_upstream_change_lands_on_disk():
    control(
        action='set_manifest',
        files=['widget-006/SKILL.md'],
        content={'widget-006/SKILL.md': 'brand new bytes'},
        commit=None,
    )
    control(action='upstream_changed')
    body = sync_job('skills')

    assert body['status'] == 'updated', body
    assert (SKILLS / 'widget-006' / 'SKILL.md').read_text() == (
        'brand new bytes'
    )
    row = next(j for j in status()['jobs'] if j['name'] == 'skills')
    assert row['version']


# ── 2. an upstream delete lands, a user skill survives ────────────


def test_upstream_delete_removes_what_it_dropped():
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
    assert (SKILLS / 'user-made' / 'SKILL.md').read_text() == 'mine'
    detail = body.get('detail') or {}
    assert detail.get('deleted', 0) >= 1
    assert any('deleted=' in ln for ln in wait_for('skills', 1))


# ── 3. a local edit: kept when upstream is quiet, reverted when not ─


def test_a_local_edit_survives_an_unchanged_upstream():
    target = SKILLS / 'widget-006' / 'SKILL.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('locally edited')
    body = sync_job('skills')

    assert body['status'] in ('unchanged', 'updated'), body
    # Nothing upstream changed (the validator still matches), so the
    # edit stands: a 304 has no manifest in hand and changes nothing.
    assert target.read_text() == 'locally edited'


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
    target.parent.mkdir(parents=True, exist_ok=True)
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
        assert any('status=failed' in ln for ln in wait_for('skills', 1))
    finally:
        control(action='clear_failures')


# ── 6. needs_restart end to end ───────────────────────────────────


def test_a_gate_change_needs_a_restart_and_is_visible():
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

    row = next(j for j in status()['jobs'] if j['name'] == 'skills')
    assert row['needs_restart'] is True
    assert status()['needs_restart'] is True


# ── 7. the FAST jobs keep ticking ─────────────────────────────────


def test_email_and_the_daemon_reaper_fire():
    seen = set()
    deadline = time.time() + 90
    while time.time() < deadline and {'email', 'daemon_reaper'} - seen:
        for name in ('email', 'daemon_reaper'):
            if run_lines(name):
                seen.add(name)
        time.sleep(2)
    assert {'email', 'daemon_reaper'} <= seen, seen
