"""Unit tests for the sync jobs (app/sync/jobs.py).

The jobs' contract: ask upstream cheaply (conditional GET), download
only what moved, report one of the four statuses, and flag a gate
change for restart. Driven against ``tests/fixtures/fake_github.py``
with an httpx MockTransport — no sockets, no database.
"""

import pytest

from app.sync import github as github_probe, jobs as sync_jobs
from app.sync.jobs import SKILLS_JOB
from app.sync.scheduler import (
    REASON_MANUAL,
    SyncContext,
    SyncJob,
    SyncScheduler,
)
from app.workspace.skills_sync import SkillsSyncManager
from tests.fixtures.fake_github import FakeGitHub
from tests.fixtures.fake_sync_state import FakeState

pytestmark = pytest.mark.unit

SKILLS_RAW = 'http://fake/raw/app/skills_v2'


@pytest.fixture
def fake():
    return FakeGitHub()


@pytest.fixture
def state(monkeypatch):
    return FakeState().install(monkeypatch)


@pytest.fixture
def skills_mgr(tmp_path, monkeypatch):
    """A SkillsSyncManager writing to a tmp tree, probing the fake."""
    monkeypatch.setattr('app.workspace.skills_sync.SKILLS_REPO_URL', SKILLS_RAW)
    monkeypatch.setattr(github_probe, 'GITHUB_API_BASE', 'http://fake/api')
    manager = SkillsSyncManager()
    manager._dest_dir = tmp_path / 'skills'
    return manager


@pytest.fixture
def wired(monkeypatch, fake, skills_mgr):
    """Point the jobs' client factory at ``fake``."""
    monkeypatch.setattr(sync_jobs, '_client_factory', fake.client)
    return fake


def seed_skills_remote(fake: FakeGitHub, *, gated: bool = False) -> None:
    """A manifest with one skill, plus the skill's file."""
    files = ['widget-006/SKILL.md']
    content = {'widget-006/SKILL.md': '# widget\n'}
    if gated:
        files.append('widget-006/gates/ad_completeness_review.py')
        content['widget-006/gates/ad_completeness_review.py'] = (
            'def check(result_text, task_id=None, rules=None):\n'
            '    return None\n'
        )
    fake.set_manifest('app/skills_v2', files)
    fake.add_tree('app/skills_v2', content)


class _NullAPS:
    """A scheduler that just records nothing — the run is the point."""

    def add_job(self, func, **kwargs):
        pass


async def run_skills(ctx, manager):
    """Run the real skills job through the real scheduler.

    Through the scheduler rather than straight at the job body because
    the state row — ``last_ok_at``, the validator, ``needs_restart`` —
    is written by the scheduler's record step, and half of what these
    tests assert lives there.
    """
    sched = SyncScheduler(_NullAPS())
    await sched.register(
        SyncJob(
            name=SKILLS_JOB,
            cadence=sync_jobs.Cadence.DAILY,
            run=lambda c: sync_jobs._run_tree(c, SKILLS_JOB, manager),
        )
    )
    return await sched.trigger(
        SKILLS_JOB, reason=ctx.reason or REASON_MANUAL, force=ctx.force
    )


# ── probe: conditional GET, 304, unsolicited 304 ───────────────────


async def test_probe_sends_if_none_match(wired, fake, state, skills_mgr):
    state.seed(SKILLS_JOB, etag='W/"v1"')
    fake.changed = False
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'unchanged'
    assert any(h.get('if-none-match') == 'W/"v1"' for h in fake.seen_headers)


async def test_a_304_counts_as_a_successful_run(wired, fake, state, skills_mgr):
    """The old cooldown forgot this and every task hit GitHub after 24h."""
    state.seed(SKILLS_JOB, etag='W/"v1"')
    fake.changed = False
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'unchanged'
    stored = await state.get_dict(SKILLS_JOB)
    assert stored['last_ok_at'] is not None
    assert fake.counts['files'] == 0  # nothing downloaded


async def test_an_unsolicited_304_is_a_failure(wired, fake, state, skills_mgr):
    fake.force_unsolicited_304 = True
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'failed'
    assert '304' in (result.error or '')
    assert (await state.get_dict(SKILLS_JOB))['last_ok_at'] is None


async def test_no_etag_header_clears_the_stored_validator(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake)
    state.seed(SKILLS_JOB, etag='W/"stale"', version='a' * 40)
    fake.upstream_changed()
    fake.etag = ''  # upstream stopped sending a validator
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.etag == ''
    stored = await state.get_dict(SKILLS_JOB)
    assert stored['etag'] is None


# ── download only what moved ───────────────────────────────────────


async def test_unchanged_commit_downloads_nothing(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake)
    state.seed(SKILLS_JOB, version=fake.commit)
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'unchanged'
    assert fake.counts['files'] == 0


async def test_a_moved_commit_is_downloaded(wired, fake, state, skills_mgr):
    seed_skills_remote(fake)
    state.seed(SKILLS_JOB, version='0' * 40)
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'updated'
    assert result.version == fake.commit
    assert result.detail == {'copied': 1, 'skipped': 0}
    assert (skills_mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == (
        '# widget\n'
    )


async def test_force_syncs_even_when_nothing_moved(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake)
    await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)  # populate
    fake.counts.clear()
    result = await run_skills(
        SyncContext(SKILLS_JOB, 'manual', force=True), skills_mgr
    )
    assert result.status == 'unchanged'  # same bytes, but they were fetched
    assert fake.counts['files'] == 2  # MANIFEST + the one file


async def test_a_missing_manifest_is_a_failed_run(
    wired, fake, state, skills_mgr
):
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.status == 'failed'
    assert 'MANIFEST' in (result.error or '')
    assert (await state.get_dict(SKILLS_JOB))['last_ok_at'] is None


# ── needs_restart ──────────────────────────────────────────────────


async def test_a_gate_change_marks_restart_needed(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake, gated=True)
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.needs_restart is True
    assert (await state.get_dict(SKILLS_JOB))['needs_restart'] is True


async def test_a_plain_skill_change_needs_no_restart(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake)
    result = await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert result.needs_restart is False
    assert (await state.get_dict(SKILLS_JOB))['needs_restart'] is False


async def test_restart_flag_is_sticky_until_cleared(
    wired, fake, state, skills_mgr
):
    seed_skills_remote(fake, gated=True)
    await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    fake.upstream_changed()
    seed_skills_remote(fake)  # later run: no gates touched
    await run_skills(SyncContext(SKILLS_JOB, 'tick'), skills_mgr)
    assert (await state.get_dict(SKILLS_JOB))['needs_restart'] is True
    await state.clear_restart_flags()
    assert (await state.get_dict(SKILLS_JOB))['needs_restart'] is False


# ── the github probe itself ────────────────────────────────────────


async def test_probe_rejects_a_304_it_never_asked_for(fake):
    fake.force_unsolicited_304 = True
    async with fake.client() as client:
        probe = await github_probe.probe_commit(client, 'app/knowledge')
    assert probe['ok'] is False
    assert 'without If-None-Match' in probe['error']


async def test_probe_reports_the_commit_and_validator(fake):
    async with fake.client() as client:
        probe = await github_probe.probe_commit(
            client, 'app/skills_v2', etag='W/"old"'
        )
    assert probe == {
        'ok': True,
        'not_modified': False,
        'commit': fake.commit,
        'etag': fake.etag,
        'error': None,
    }


async def test_probe_keeps_the_stored_validator_on_a_304(fake):
    fake.changed = False
    async with fake.client() as client:
        probe = await github_probe.probe_commit(
            client, 'app/skills_v2', etag=fake.etag
        )
    assert probe['ok'] is True
    assert probe['not_modified'] is True
    assert probe['etag'] == fake.etag
