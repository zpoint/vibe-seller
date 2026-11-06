"""Integration tests for the central sync scheduler.

Real database (the throwaway test home), real SQLAlchemy models, real
skills tree on a tmp path — and the fake GitHub transport for the
network. These pin the two bugs the scheduler exists to kill:

* a probe that finds nothing new must still move ``last_ok_at``
  (otherwise every task launch re-polls GitHub);
* a sync must never expose a half-written skill to a task workspace
  copy.
"""

from datetime import UTC, datetime
import json
import shutil

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import async_session, init_db
from app.models.sync_state import SyncState
from app.scheduler.cron import scheduler as aps
from app.sync import jobs as sync_jobs, migrate
from app.sync.scheduler import (
    Cadence,
    SyncJob,
    SyncResult,
    SyncScheduler,
)
from app.sync.state import STATUS_UNCHANGED, get_dict, record
from app.workspace.manager import WorkspaceManager
from app.workspace.skills_sync import SkillsSyncManager
from tests.fixtures.fake_github import FakeGitHub

pytestmark = pytest.mark.integration


class _NullAPS:
    def add_job(self, func, **kwargs):
        self.last = kwargs


@pytest_asyncio.fixture
async def clean_state():
    await init_db()
    async with async_session() as db:
        for row in (await db.execute(select(SyncState))).scalars():
            await db.delete(row)
        await db.commit()
    yield


@pytest.fixture
def fake():
    github = FakeGitHub()
    github.set_manifest('app/skills_v2', ['widget-006/SKILL.md'])
    github.add_tree('app/skills_v2', {'widget-006/SKILL.md': '# widget\n'})
    return github


@pytest.fixture
def skills_mgr(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(
        'app.workspace.skills_sync.SKILLS_REPO_URL',
        'http://fake/raw/app/skills_v2',
    )
    monkeypatch.setattr('app.sync.github.GITHUB_API_BASE', 'http://fake/api')
    monkeypatch.setattr(sync_jobs, '_client_factory', fake.client)
    manager = SkillsSyncManager()
    manager._dest_dir = tmp_path / '.claude' / 'skills'
    return manager


async def run_skills(manager, *, force: bool = False):
    sched = SyncScheduler(_NullAPS())
    await sched.register(
        SyncJob(
            name=sync_jobs.SKILLS_JOB,
            cadence=Cadence.DAILY,
            run=lambda c: sync_jobs._run_tree(c, sync_jobs.SKILLS_JOB, manager),
        )
    )
    return await sched.trigger(sync_jobs.SKILLS_JOB, force=force)


# ── sync_state table ───────────────────────────────────────────────


async def test_create_all_on_an_existing_database_is_idempotent():
    await init_db()
    async with async_session() as db:
        await db.execute(SyncState.__table__.delete())
        await db.commit()
    await init_db()  # the boot path again — must not fail, must create
    await record('skills', status='updated', version='a' * 40)
    async with async_session() as db:
        row = await db.get(SyncState, 'skills')
    assert row.version == 'a' * 40


async def test_a_successful_probe_refreshes_last_ok_at(clean_state):
    """The 304 case — pins the old "unchanged resets nothing" bug."""
    await record('skills', status='unchanged', version='a' * 40)
    async with async_session() as db:
        row = await db.get(SyncState, 'skills')
    assert row.last_ok_at is not None
    first = row.last_ok_at

    await record('skills', status='failed', error='nope')
    async with async_session() as db:
        row = await db.get(SyncState, 'skills')
    assert row.last_ok_at == first  # a failure doesn't move it
    assert row.version == 'a' * 40  # nor erase what we know

    await record('skills', status='skipped', detail={'skip': 'toggle off'})
    async with async_session() as db:
        row = await db.get(SyncState, 'skills')
    assert row.last_ok_at == first


async def test_etag_clearing_and_sticky_restart_flag(clean_state):
    await record('skills', status='updated', etag='W/"v1"', needs_restart=True)
    await record('skills', status='updated', etag='')
    async with async_session() as db:
        row = await db.get(SyncState, 'skills')
    assert row.etag is None
    assert row.needs_restart is True  # sticky until boot clears it


# ── the whole job against the real table ───────────────────────────


async def test_unchanged_upstream_still_schedules_the_next_run(
    clean_state, fake, skills_mgr
):
    """A 304 is a successful run: files untouched, clock advanced."""
    first = await run_skills(skills_mgr)
    assert first.status == 'updated'
    async with async_session() as db:
        row = await db.get(SyncState, sync_jobs.SKILLS_JOB)
    first_ok = row.last_ok_at

    fake.changed = False
    fake.counts.clear()
    second = await run_skills(skills_mgr)
    assert second.status == 'unchanged'
    assert fake.counts['files'] == 0
    async with async_session() as db:
        row = await db.get(SyncState, sync_jobs.SKILLS_JOB)
    assert row.last_ok_at > first_ok
    assert row.version == fake.commit


# ── atomic writes vs the task-workspace copy ───────────────────────


def _client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_task_workspace_copy_never_sees_a_partial_skill(
    clean_state, fake, skills_mgr, monkeypatch, tmp_path
):
    """Pins bug 3: copytree of the skills tree during a sync.

    The copy is taken while the LAST file is still downloading — the
    moment where the old per-file writes had already landed the first
    file and not yet the second. Whatever the copy catches, it must be
    one whole tree: either both new files or neither.
    """
    dest = skills_mgr._dest_dir
    fake.set_manifest(
        'app/skills_v2',
        ['widget-006/SKILL.md', 'widget-006/EXTRA.md'],
    )
    fake.add_tree(
        'app/skills_v2',
        {'widget-006/SKILL.md': '# widget\n', 'widget-006/EXTRA.md': 'x\n'},
    )
    await run_skills(skills_mgr)  # populate the old tree

    snapshot = tmp_path / 'snapshot'

    def handler(request):
        if request.url.path.endswith('EXTRA.md') and not snapshot.exists():
            shutil.copytree(dest.parent, snapshot / 'claude')
        return fake.handler(request)

    monkeypatch.setattr(
        sync_jobs, '_client_factory', lambda: _client_with(handler)
    )
    fake.add_tree(
        'app/skills_v2',
        {
            'widget-006/SKILL.md': '# widget (new)\n',
            'widget-006/EXTRA.md': 'y\n',
        },
    )
    fake.upstream_changed()
    await run_skills(skills_mgr)

    copied = snapshot / 'claude' / 'skills' / 'widget-006'
    assert (
        (copied / 'SKILL.md').read_text(),
        (copied / 'EXTRA.md').read_text(),
    ) in (
        ('# widget\n', 'x\n'),  # caught the old tree
        ('# widget (new)\n', 'y\n'),  # caught the new one
    )


async def test_a_leftover_staging_dir_never_reaches_a_task_workspace(
    clean_state, skills_mgr, monkeypatch
):
    """An interrupted sync leaves staging beside the tree, not in it.

    Two guarantees: boot sweeps the leftovers, and even an un-swept
    staging dir is outside ``.claude/``-copied content — it can never
    travel into a task workspace as a second, partial skill.
    """
    dest = skills_mgr._dest_dir
    (dest / 'widget-006').mkdir(parents=True)
    (dest / 'widget-006' / 'SKILL.md').write_text('# widget\n')
    staging = skills_mgr._staging_root
    (staging / 'widget-006_abc').mkdir(parents=True)
    (dest / '.tmp_widget-006_dead').mkdir()
    (dest / '.bak_widget-006').mkdir()

    skills_mgr.sweep_staging()
    assert not staging.exists()
    assert not (dest / '.tmp_widget-006_dead').exists()
    assert not (dest / '.bak_widget-006').exists()

    # Without a sweep (the crash happened after boot): the copy of the
    # .claude tree still must not carry a staging dir.
    (staging / 'widget-006_abc').mkdir(parents=True, exist_ok=True)
    manager = WorkspaceManager(root=dest.parent.parent)

    async def _no_init(**_kwargs):
        return None

    monkeypatch.setattr(manager, 'ensure_init', _no_init)
    task_dir = await manager.prepare_task_workspace('task-0001')
    copied_claude = task_dir / '.claude'
    assert not (copied_claude / '.sync-staging').exists()
    assert (copied_claude / 'skills' / 'widget-006' / 'SKILL.md').exists()


# ── registration against the real APScheduler ──────────────────────


async def test_registered_jobs_are_bound_to_the_real_scheduler(
    clean_state, monkeypatch
):
    monkeypatch.setenv('VIBE_SYNC_JITTER', '0')
    monkeypatch.setattr(sync_jobs, 'registered_sync_jobs', lambda: [])
    sync_scheduler = SyncScheduler(aps)
    names = await sync_jobs.register_jobs(sync_scheduler)
    try:
        aps.start()
        ids = {job.id for job in aps.get_jobs() if job.id.startswith('sync:')}
        assert ids == {f'sync:{name}' for name in names}
    finally:
        aps.shutdown(wait=False)
        sync_scheduler._jobs.clear()


# ── legacy-state migration against the real table ─────────────────


async def test_legacy_meta_imports_into_the_real_table(clean_state, tmp_path):
    """The upgrade path: old file in, sync_state row out, file trimmed."""
    home = tmp_path / 'home'
    skills_meta = home / '.claude' / 'skills' / '.sync_meta.json'
    skills_meta.parent.mkdir(parents=True)
    skills_meta.write_text(
        json.dumps({
            'last_sync_at': '2026-11-01T02:03:04+00:00',
            'last_commit': 'a' * 40,
            'status': 'success',
            'error': None,
            'copied': 3,
            'skipped': 7,
            'synced_skills': ['widget-006'],
            'gws_installed': True,
        })
    )
    knowledge_meta = home / 'knowledge' / '.sync_meta.json'
    knowledge_meta.parent.mkdir(parents=True)
    knowledge_meta.write_text(
        json.dumps({'last_sync_at': '2026-10-01T00:00:00+00:00'})
    )

    original = migrate.VIBE_SELLER_DIR
    migrate.VIBE_SELLER_DIR = home
    try:
        done = await migrate.migrate_legacy_state()
    finally:
        migrate.VIBE_SELLER_DIR = original

    assert done == {'skills': True, 'knowledge': True, 'presets': False}
    async with async_session() as db:
        skills = await db.get(SyncState, 'skills')
        knowledge = await db.get(SyncState, 'knowledge')
    assert skills.last_ok_at == '2026-11-01T02:03:04+00:00'
    assert skills.version == 'a' * 40
    assert knowledge.last_ok_at == '2026-10-01T00:00:00+00:00'
    assert json.loads(skills_meta.read_text()) == {
        'synced_skills': ['widget-006'],
        'gws_installed': True,
    }
    assert not knowledge_meta.exists()

    # The imported timestamp is what the next run is computed from —
    # an upgraded install must not look freshly synced.
    sched = SyncScheduler(_NullAPS())
    await sched.register(
        SyncJob(
            name='knowledge',
            cadence=Cadence.DAILY,
            run=lambda c: _ok(),
        )
    )
    # Anchored on the IMPORTED timestamp (2026-10-01 + one cadence),
    # not on boot — and overdue, so it fires at once rather than a
    # cadence from now.
    next_run = sched._next['knowledge']
    assert next_run.isoformat().startswith('2026-10-02')
    assert next_run < datetime.now(UTC)


async def _ok():
    return SyncResult(status=STATUS_UNCHANGED)


async def test_a_failed_legacy_run_leaves_the_job_overdue_and_refetches(
    clean_state, tmp_path, fake, skills_mgr
):
    """A legacy FAILED run must not be imported as "up to date".

    Its ``last_commit`` is a commit whose content never landed: taking
    it as the stored version would make the next probe answer
    "unchanged" for content we do not have, for ever.
    """
    home = tmp_path / 'home'
    meta = home / '.claude' / 'skills' / '.sync_meta.json'
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps({
            'last_sync_at': '2026-11-01T02:03:04+00:00',
            'last_commit': fake.commit,
            'status': 'failed',
            'error': 'Remote skills sync error: boom',
        })
    )
    original = migrate.VIBE_SELLER_DIR
    migrate.VIBE_SELLER_DIR = home
    try:
        assert (await migrate.migrate_legacy_state())['skills'] is True
    finally:
        migrate.VIBE_SELLER_DIR = original

    row = await get_dict(sync_jobs.SKILLS_JOB)
    assert row['version'] is None
    assert row['last_ok_at'] is None

    # Registration computes the next run from last_ok_at — none, and
    # no stale one — so the job is due at once.
    sched = SyncScheduler(_NullAPS())
    await sched.register(
        SyncJob(
            name=sync_jobs.SKILLS_JOB,
            cadence=Cadence.DAILY,
            run=lambda c: sync_jobs._run_tree(
                c, sync_jobs.SKILLS_JOB, skills_mgr
            ),
        )
    )
    assert sched._next[sync_jobs.SKILLS_JOB] < datetime.now(UTC)

    result = await sched.trigger(sync_jobs.SKILLS_JOB, force=True)
    assert result.status == 'updated'
    assert (skills_mgr._dest_dir / 'widget-006' / 'SKILL.md').exists()


async def test_email_and_reaper_jobs_run_through_the_scheduler(
    clean_state, monkeypatch
):
    """M3: email polling and the daemon reaper left their own loops
    behind — a tick of the one scheduler is what runs them now."""
    calls = []

    async def fake_sync_all():
        calls.append('email')

    async def fake_reap():
        calls.append('reaper')
        return 2

    monkeypatch.setattr(sync_jobs, 'sync_all_email_accounts', fake_sync_all)
    monkeypatch.setattr(sync_jobs, 'reap_orphaned_daemons', fake_reap)

    sched = SyncScheduler(_NullAPS())
    await sched.register_all(sync_jobs.core_jobs())
    assert set(sched.jobs) >= {'email', 'daemon_reaper'}

    await sched.trigger(sync_jobs.EMAIL_JOB, reason='tick')
    result = await sched.trigger(sync_jobs.REAPER_JOB, reason='tick')
    assert calls == ['email', 'reaper']
    assert result.detail == {'reaped': 2}
