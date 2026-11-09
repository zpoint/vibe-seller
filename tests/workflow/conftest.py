"""Workflow test fixtures: real DB, fake agent, mocked browser/filesystem."""

import asyncio
import base64
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Force-import every model so Base.metadata.create_all() below sees
# them all. SQLAlchemy only registers a table on Base.metadata when
# the model class has been imported — without this `from app import
# models`, tables for any model not separately imported (e.g.
# StoreEmailLink, EmailAccount, Event, ...) silently aren't created
# and the tests blow up later with "no such table: <name>". The
# `# noqa: F401` is because we don't use `models` as a symbol — the
# import is only for its side effect on Base.metadata.
from app import models  # noqa: F401
from app.auth import create_token
from app.database import get_db
from app.main import app
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.user import User
from app.password import hash_password
from tests.workflow.db_calls import db_calls, wait_db_idle
from tests.workflow.fake_agent import FakeAgent

# Save BEFORE any monkeypatching
real_sleep = asyncio.sleep

# Every module that does `from app.database import async_session`
# at module level — must be patched so they all use the test DB.
ASYNC_SESSION_MODULES = [
    'app.database',
    'app.rest_secret_guard',
    'app.routers.secrets',
    'app.routers.tasks',
    'app.routers.vision',
    'app.task_runner',
    'app.task_finalize',
    'app.task_runner_auto',
    'app.task_runner_context',
    'app.task_runner_exec',
    'app.task_session_lifecycle',
    'app.routers.schedules',
    'app.routers.schedule_planning',
    'app.routers.email_accounts',
    'app.ai.claude_backend',
    'app.ai.claude_backend_manager',
    'app.workspace.knowledge_sync',
    'app.workspace.skills_sync',
    'app.scheduler.task_queue',
    'app.scheduler.heartbeat',
    'app.browser.idle_sweep',
    'app.scheduler.cron',
    'app.scheduler.fanout',
    'app.scheduler.plan_reaper',
    'app.scheduler.finalize_reaper',
    'app.scheduler.stall_reaper',
    'app.routers.workspace_assistant',
    'app.browser.daemon_reaper',
    'app.telemetry_tasks',
    'app.task_usage',
    # The central sync scheduler's state table and the ads skill's
    # AppSettings/Store reads must land in the test DB too.
    'app.sync.state',
    'app.ads_skill',
]


# ── Core DB ──────────────────────────────────────────


_db_calls = db_calls


class _TrackedAsyncSession(AsyncSession):
    """``AsyncSession`` whose awaitable calls register with
    ``_db_calls``.

    Every test-database session goes through the fixture's
    ``async_sessionmaker`` below, so this subclass is the single
    choke point for "is a task inside a database call right now".
    The counter is incremented before the call suspends and
    decremented after it resumes, on the event-loop thread — so
    ``in_flight == 0`` really means "every task is suspended at a
    non-DB await", which is exactly when cancelling is safe.
    """

    async def _counted(self, fn, /, *args, **kwargs):
        _db_calls.in_flight += 1
        try:
            return await fn(*args, **kwargs)
        finally:
            _db_calls.in_flight -= 1

    async def get(self, *args, **kwargs):
        return await self._counted(super().get, *args, **kwargs)

    async def execute(self, *args, **kwargs):
        return await self._counted(super().execute, *args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return await self._counted(super().scalar, *args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return await self._counted(super().scalars, *args, **kwargs)

    async def stream(self, *args, **kwargs):
        return await self._counted(super().stream, *args, **kwargs)

    async def execute_stream(self, *args, **kwargs):
        return await self._counted(super().execute_stream, *args, **kwargs)

    async def commit(self, *args, **kwargs):
        return await self._counted(super().commit, *args, **kwargs)

    async def rollback(self, *args, **kwargs):
        return await self._counted(super().rollback, *args, **kwargs)

    async def flush(self, *args, **kwargs):
        return await self._counted(super().flush, *args, **kwargs)

    async def refresh(self, *args, **kwargs):
        return await self._counted(super().refresh, *args, **kwargs)

    async def close(self, *args, **kwargs):
        return await self._counted(super().close, *args, **kwargs)

    async def run_sync(self, *args, **kwargs):
        return await self._counted(super().run_sync, *args, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return await self._counted(super().__aexit__, *args)


async def drain_background_tasks(deadline_seconds: float = 10.0) -> None:
    """Make the database idle, then stop whatever is still running.

    WHY. The engine fixture drops the database at teardown, and a
    background pipeline (auto_run_task, the follow-up spawn, the plan
    executor) may still be using it. Two things must hold before the
    drop:

    1. No call is IN FLIGHT. A cut-short call invalidates the shared
       connection and — the database living in it — the database
       itself (see ``tests/workflow/db_calls.py``).
    2. Nothing can touch the database afterwards. A pipeline parked
       on an event (the FakeAgent waiting for plan approval is the
       common case here) would wake later and write to a dropped
       database; the error surfaces against an unrelated test.

    So the drain waits only while calls are in flight — chained
    writes are covered naturally, because each next call re-increments
    the counter before the loop can observe zero — and then cancels
    what is left and awaits its unwind. The cancel lands at a non-DB
    await, which is the safe point; the unwind is awaited so no
    cancelled task is still unwinding when the drop runs.

    The previous drain waited for every pending task to COMPLETE with
    a 10 s deadline. A task that never completes — the parked
    FakeAgent, leaked on purpose by tests that just create a plan-mode
    task and return — therefore cost every leaking test the full
    deadline: 28 tests x 10 s, 280 s of a 860 s run. "Wait for the
    database, not for the tasks" is the fix.

    ``deadline_seconds`` is a backstop only: if a call stays in flight
    past it (a hung worker), the drain falls through to cancelling
    anyway — bounded beats stuck.
    """
    current = asyncio.current_task()
    await wait_db_idle(deadline_seconds)
    pending = [
        t for t in asyncio.all_tasks() if t is not current and not t.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        # Bounded unwind: a task that swallows its cancellation must
        # not be able to hang the teardown forever. Two seconds is
        # orders of magnitude past a real unwind (rollback + finally).
        await asyncio.wait(pending, timeout=2.0)


@pytest_asyncio.fixture
async def test_engine(tmp_path):
    """Per-test SQLite FILE with ``isolation_level='AUTOCOMMIT'``.

    StaticPool + aiosqlite has a stale-read race: SQLAlchemy
    sessions open BEGIN DEFERRED under SQLite's default
    isolation, so a read in session B can snapshot before
    session A's concurrent COMMIT lands. Observed: PUT commits
    ``plan_status='stale'`` and returns 200, then an immediate
    GET on a new session reads ``plan_status='ready'``. Same
    class of bug hit ``plan_status='ready'`` reads after
    FakeAgent commits.

    AUTOCOMMIT removes the implicit BEGIN — each statement sees
    the latest committed state. StaticPool still shared so fixture
    setup/teardown uses one engine; reads are now coherent.

    A FILE, not ``sqlite+aiosqlite://`` in-memory: an in-memory
    database LIVES in its one connection, so anything that
    invalidates that connection destroys every row and table —
    and SQLAlchemy invalidates on a cut-short call
    (``_exec_single_context`` catches ``BaseException``, which
    includes ``CancelledError``, and runs
    ``_handle_dbapi_exception``). Measured: ``FakeAgent.stop()``
    cancelling its run mid-write silently emptied the schema
    (intermittent ``no such table: app_settings`` on the NEXT
    request of the same test — reproduced on ~1 in 3 runs of the
    lifecycle-guidance class). With the database on disk, a
    replaced connection reopens the same data and a cancelled
    call costs one connection, not the test.
    """
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "test.db"}',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        isolation_level='AUTOCOMMIT',
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    # Make the database idle BEFORE it goes away: no call in flight,
    # nothing left that can wake up and write (see
    # drain_background_tasks).
    await drain_background_tasks()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def override_async_session(test_engine, monkeypatch):
    """Patch async_session in ALL modules to use the test DB."""
    test_maker = async_sessionmaker(
        test_engine, class_=_TrackedAsyncSession, expire_on_commit=False
    )
    for mod in ASYNC_SESSION_MODULES:
        try:
            monkeypatch.setattr(f'{mod}.async_session', test_maker)
        except AttributeError:
            pass  # module might not exist in all installs

    async def _test_get_db():
        async with test_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _test_get_db
    yield test_maker
    app.dependency_overrides.pop(get_db, None)


# ── Fake agent ───────────────────────────────────────


@pytest.fixture
def fake_agent():
    return FakeAgent()


@pytest.fixture
def install_fake_agent(fake_agent, monkeypatch):
    """Patch agent_manager in all import sites. Returns the FakeAgent."""
    monkeypatch.setattr(
        'app.ai.claude_backend_manager.agent_manager', fake_agent
    )
    monkeypatch.setattr(
        'app.ai.claude_backend_manager.agent_backend', fake_agent
    )
    monkeypatch.setattr('app.routers.tasks.agent_manager', fake_agent)
    monkeypatch.setattr('app.routers.tasks_files.agent_manager', fake_agent)
    monkeypatch.setattr(
        'app.routers.tasks_conversation.agent_manager', fake_agent
    )
    monkeypatch.setattr('app.task_finalize.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_runner_auto.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_runner_followup.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_runner_exec.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_session_lifecycle.agent_manager', fake_agent)
    monkeypatch.setattr('app.routers.app_settings.agent_manager', fake_agent)
    # The stall reaper must see the same backend the rest of the app
    # does — its alive/deferral decision reads session state
    # (is_process_alive / get_session) off the manager.
    monkeypatch.setattr('app.scheduler.stall_reaper.agent_manager', fake_agent)
    return fake_agent


# ── Mock browser ─────────────────────────────────────


@pytest.fixture
def mock_browser_wf(monkeypatch):
    class _Mock:
        def __init__(self):
            self.config_calls = []

        async def ensure_session(self, store, db):
            pass

        async def write_browser_config_for_store(self, store, db):
            self.config_calls.append(store.id)

        async def write_web_browser_config(self, db):
            self.config_calls.append('_web')

        async def write_task_browser_config(self, store, db):
            self.config_calls.append(store.id if store else '_web')

        async def start_web_session(self, db):
            pass

        async def start_session(self, store, db):
            pass

        async def stop_session(self, store, db):
            pass

        def is_session_active(self, store_id):
            return False

        def remove_browser_entry(self, store_name, backend, store_id=None):
            pass

    mock = _Mock()
    monkeypatch.setattr('app.browser.manager.browser_manager', mock)
    monkeypatch.setattr('app.routers.tasks_conversation.browser_manager', mock)
    monkeypatch.setattr('app.task_runner_auto.browser_manager', mock)
    monkeypatch.setattr('app.task_runner_exec.browser_manager', mock)
    monkeypatch.setattr('app.routers.stores.browser_manager', mock)
    return mock


# ── Mock knowledge sync ──────────────────────────────


@pytest.fixture
def mock_knowledge_sync(monkeypatch):
    class _Mock:
        def __init__(self):
            # Map store_slug → (l2_stale, l3_stale).
            # Default: not stale (skip catalog agent).
            self.stale_overrides: dict[str | None, tuple[bool, bool]] = {}
            # Tracks whether rotate_catalogs deleted the global
            # L2 catalog.  When True, catalog_needs_update returns
            # l2_stale=True for every store (simulates the real
            # filesystem race where one store's rotation makes all
            # others see L2 as missing).
            self._l2_rotated = False

        async def fetch(self):
            return {'copied': 0, 'status': 'ok'}

        def get_sync_status(self):
            return {'synced': True, 'files': 0}

        def catalog_needs_update(
            self, store_slug: str | None = None
        ) -> tuple[bool, bool]:
            l2, l3 = self.stale_overrides.get(store_slug, (False, False))
            # If L2 was "deleted" by a prior rotate call,
            # every subsequent store sees L2 as stale —
            # mirrors the real filesystem race.
            if self._l2_rotated:
                l2 = True
            return l2, l3

        def rotate_catalogs(
            self,
            store_slug=None,
            *,
            l2_stale=True,
            l3_stale=True,
        ):
            # Track whether L2 was deleted so catalog_needs_update
            # can reflect the cascading staleness bug.
            if l2_stale:
                self._l2_rotated = True
            return {}

    mock = _Mock()
    monkeypatch.setattr('app.task_runner_auto.knowledge_sync', mock)
    monkeypatch.setattr('app.routers.workspace.knowledge_sync', mock)
    monkeypatch.setattr('app.sync.jobs.knowledge_sync', mock)
    return mock


# ── Mock skills sync ────────────────────────────────


@pytest.fixture
def mock_skills_sync(monkeypatch):
    class _Mock:
        async def fetch(self):
            return {'copied': 0, 'status': 'ok'}

        async def probe_remote(self, client, etag=None):
            return {
                'ok': True,
                'not_modified': False,
                'commit': 'a' * 40,
                'etag': 'W/"v1"',
                'error': None,
            }

        async def do_remote_sync(self, client):
            return {'status': 'success', 'copied': 0, 'skipped': 0}

    mock = _Mock()
    monkeypatch.setattr('app.sync.jobs.skills_sync', mock)
    return mock


# ── Mock workspace ───────────────────────────────────


@pytest.fixture
def mock_workspace(monkeypatch, tmp_path):
    class _Mock:
        root = tmp_path

        async def ensure_init(self):
            (self.root / '.claude' / 'skills').mkdir(
                parents=True, exist_ok=True
            )
            (self.root / 'knowledge').mkdir(parents=True, exist_ok=True)
            (self.root / 'stores').mkdir(parents=True, exist_ok=True)

        async def _auto_commit(self, message):
            pass

        async def list_tree(self):
            items = []
            for p in sorted(self.root.rglob('*')):
                if p.is_file():
                    items.append({
                        'path': str(p.relative_to(self.root)),
                        'is_dir': False,
                        'size': p.stat().st_size,
                    })
            return items

        async def get_structured(self):
            skills = []
            skills_dir = self.root / '.claude' / 'skills'
            if skills_dir.is_dir():
                for p in sorted(skills_dir.iterdir()):
                    if p.is_dir() and not p.name.startswith('.'):
                        skills.append({
                            'slug': p.name,
                            'path': str(p.relative_to(self.root)),
                            'files': [],
                            'file_count': 0,
                            'description': '',
                            'source': 'custom',
                            'origin_url': '',
                        })
            return {
                'skills': skills,
                'store_profiles': [],
                'project_knowledge': [],
                'local_knowledge': [],
                'root_files': [],
            }

        def resolve_file(self, path):
            # The real manager's contract for the raw route (and the
            # MCP read tool's size probe): resolve, refuse traversal,
            # require an existing file.
            if '..' in path:
                raise ValueError(f'Path traversal blocked: {path}')
            full = self.root / path
            if not full.is_file():
                raise FileNotFoundError(path)
            return full

        async def read_file(self, path):
            if '..' in path:
                raise ValueError(f'Path traversal blocked: {path}')
            full = self.root / path
            if not full.exists():
                raise FileNotFoundError(path)
            return full.read_text()

        async def write_file(self, path, content):
            if '..' in path:
                raise ValueError(f'Path traversal blocked: {path}')
            full = self.root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)

        async def delete_file(self, path):
            if '..' in path:
                raise ValueError(f'Path traversal blocked: {path}')
            full = self.root / path
            if not full.exists():
                raise FileNotFoundError(path)
            full.unlink()

        async def create_skill(self, name, description='', origin_url=''):
            d = self.root / '.claude' / 'skills' / name
            d.mkdir(parents=True, exist_ok=True)
            (d / 'SKILL.md').write_text(f'# {name}\n{description}')
            return f'.claude/skills/{name}'

        async def delete_skill(self, slug):
            d = self.root / '.claude' / 'skills' / slug
            if not d.is_dir():
                raise FileNotFoundError(f'Skill not found: {slug}')
            if slug.startswith('_'):
                raise ValueError('Cannot delete built-in skills')
            shutil.rmtree(d)

        def _synced(self):
            meta = self.root / '.claude' / 'skills' / '.sync_meta.json'
            if meta.exists():
                return set(
                    json.loads(meta.read_text()).get('synced_skills', [])
                )
            return set()

        async def list_skills(self):
            skills_dir = self.root / '.claude' / 'skills'
            if not skills_dir.is_dir():
                return []
            synced = self._synced()
            out = []
            for p in sorted(skills_dir.iterdir()):
                if not p.is_dir() or p.name.startswith('.'):
                    continue
                source = 'builtin' if p.name in synced else 'custom'
                name, desc = p.name, ''
                md = p / 'SKILL.md'
                if md.is_file():
                    for line in md.read_text().splitlines():
                        m = re.match(r'(name|description):\s*(.*)', line)
                        if m and m.group(1) == 'name':
                            name = m.group(2).strip() or p.name
                        elif m:
                            desc = m.group(2).strip().strip('"')
                out.append({
                    'slug': p.name,
                    'name': name,
                    'description': desc,
                    'source': source,
                    'updatable': source in ('custom', 'imported'),
                })
            return out

        async def save_skill(
            self,
            slug,
            skill_md,
            files=None,
            files_bin=None,
            executables=None,
            origin_url='',
            replace=False,
            overwrite=False,
        ):
            result, _existed = self.save_skill_blocking(
                slug,
                skill_md,
                files,
                files_bin,
                executables,
                origin_url,
                replace,
                overwrite,
            )
            return result

        def save_skill_blocking(
            self,
            slug,
            skill_md,
            files=None,
            files_bin=None,
            executables=None,
            origin_url='',
            replace=False,
            overwrite=False,
        ):
            """Sync twin of the real manager's install core."""
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or ''):
                raise ValueError(f'Invalid skill slug: {slug!r}')
            if slug in self._synced():
                raise ValueError(f'{slug!r} is a built-in skill')
            d = self.root / '.claude' / 'skills' / slug
            existed = d.is_dir()
            if replace and existed and not overwrite:
                raise ValueError(
                    f'a skill named {slug} already exists '
                    '(source=custom); confirm with the user, then '
                    're-call with overwrite=true or choose another slug'
                )
            payloads = {}
            for rel, content in (files or {}).items():
                payloads[rel] = content.encode()
            for rel, content in (files_bin or {}).items():
                if rel in payloads:
                    raise ValueError(f'Skill file {rel!r} is both')
                try:
                    payloads[rel] = base64.b64decode(content, validate=True)
                except Exception as exc:
                    raise ValueError(
                        f'Skill file {rel!r} is not valid base64'
                    ) from exc
            for rel in payloads:
                pp = Path(rel)
                if (
                    pp.is_absolute()
                    or not pp.parts
                    or '..' in pp.parts
                    or '\\' in rel
                    or pp.name.lower() == 'skill.md'
                ):
                    raise ValueError(f'Invalid skill file path: {rel!r}')
            for rel in executables or []:
                if rel not in payloads:
                    raise ValueError(f'executables names {rel!r}, not written')
            if replace and existed:
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
            (d / 'SKILL.md').write_text(skill_md)
            for rel, content in payloads.items():
                dest = d / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                if rel in set(executables or []):
                    dest.chmod(0o755)
            if origin_url:
                lock = self.root / '.claude' / 'skills' / 'skills.lock.json'
                data = json.loads(lock.read_text()) if lock.exists() else {}
                data.setdefault('skills', {}).setdefault(slug, {})[
                    'origin_url'
                ] = origin_url
                data['skills'][slug]['source'] = 'url'
                lock.write_text(json.dumps(data))
            result = {
                'slug': slug,
                'path': f'.claude/skills/{slug}',
                'action': 'updated' if existed else 'created',
            }
            return result, existed

        async def create_store_profile(
            self,
            slug,
            name,
            platform='',
            country='',
            backend='chrome',
        ):
            d = self.root / 'stores' / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / 'STORE.md').write_text(f'# {name}\n')
            return f'stores/{slug}'

        async def file_history(self, path, max_count=50):
            return []

        async def file_at_commit(self, path, commit):
            return ''

        async def reset_file_to_commit(self, path, commit):
            pass

        async def prepare_task_workspace(
            self, task_id, *, store_id=None, clean=False
        ):
            task_dir = self.root / 'tasks' / task_id
            if clean and task_dir.exists():
                shutil.rmtree(task_dir)
            task_dir.mkdir(parents=True, exist_ok=True)
            return task_dir

    mock = _Mock()
    monkeypatch.setattr('app.routers.workspace.workspace_manager', mock)
    monkeypatch.setattr('app.routers.stores.workspace_manager', mock)
    monkeypatch.setattr(
        'app.routers.tasks_conversation.workspace_manager', mock
    )
    monkeypatch.setattr('app.task_runner.workspace_manager', mock)
    monkeypatch.setattr('app.task_runner_auto.workspace_manager', mock)
    monkeypatch.setattr('app.task_runner_exec.workspace_manager', mock)
    monkeypatch.setattr('app.scheduler.finalize_reaper.workspace_manager', mock)
    # The hosted MCP endpoint reads knowledge through the same manager.
    monkeypatch.setattr('app.agent_mcp.tools_workspace.workspace_manager', mock)
    return mock


# ── Isolated profiles ────────────────────────────────


@pytest.fixture
def isolated_profiles(monkeypatch, tmp_path):
    profiles_path = tmp_path / 'profiles.json'
    monkeypatch.setattr('app.ai.profiles.PROFILES_PATH', profiles_path)
    return profiles_path


# ── Fast polling ─────────────────────────────────────


@pytest.fixture
def fast_polling(monkeypatch):
    """Replace asyncio.sleep with a fast version for polling loops."""
    _original = asyncio.sleep

    async def _fast(seconds, *args, **kwargs):
        await _original(min(seconds, 0.01), *args, **kwargs)

    monkeypatch.setattr(asyncio, 'sleep', _fast)


# ── Users ────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(override_async_session) -> User:
    async with override_async_session() as db:
        # Seed auth_required=true so get_current_user uses JWT
        # path (not the auth-disabled bypass that causes
        # StaticPool contention in tests).
        existing = await db.get(AppSettings, 'auth_required')
        if not existing:
            db.add(AppSettings(key='auth_required', value='true'))
        user = User(
            id=str(uuid.uuid4()),
            username='admin',
            email='admin@test.com',
            password_hash=hash_password('admin123'),
            role='admin',
            is_active=True,
            created_at=datetime.now(UTC).isoformat(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


@pytest_asyncio.fixture
async def member_user(override_async_session) -> User:
    async with override_async_session() as db:
        existing = await db.get(AppSettings, 'auth_required')
        if not existing:
            db.add(AppSettings(key='auth_required', value='true'))
        user = User(
            id=str(uuid.uuid4()),
            username='member',
            email='member@test.com',
            password_hash=hash_password('member123'),
            role='member',
            is_active=True,
            created_at=datetime.now(UTC).isoformat(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


# ── Clients ──────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_client(
    override_async_session,
    admin_user,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    mock_workspace,
    isolated_profiles,
    fast_polling,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated admin client with ALL workflow mocks."""
    token = create_token(admin_user.id, 'admin')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


@pytest_asyncio.fixture
async def member_client(
    override_async_session,
    member_user,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    mock_workspace,
    isolated_profiles,
    fast_polling,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated member client with ALL workflow mocks."""
    token = create_token(member_user.id, 'member')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


@pytest_asyncio.fixture
async def unauthed_client(
    override_async_session,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    mock_workspace,
    isolated_profiles,
    fast_polling,
) -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated client with all mocks but no auth cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        yield client


# ── Helpers ──────────────────────────────────────────


TERMINAL_STATUSES = ('completed', 'failed')


async def wait_for_task(
    client: AsyncClient,
    task_id: str,
    target: str | tuple[str, ...] = 'completed',
    timeout: float = 10.0,
) -> dict:
    """Poll GET /api/tasks/{id} until status matches target or fails.

    *target* is one status, or a tuple of statuses any of which ends
    the wait (``TERMINAL_STATUSES`` for "finished, either way").

    Uses the REAL asyncio.sleep (not the patched fast version) so the
    background pipeline has time to make progress between polls.

    Raises when the deadline expires first. A silent return made
    "wait for a state the scenario never reaches" cost the timeout
    and still pass — a dozen tests polled for 10 s at a plan-mode
    task parked at ``planned`` and went on to assert about messages,
    never looking at the status. Waiting must either see the state or
    fail the test.
    """
    # Give the background pipeline a chance to start before polling
    await real_sleep(0.05)
    targets = (target,) if isinstance(target, str) else target
    data: dict = {}
    for _ in range(int(timeout / 0.1)):
        await real_sleep(0.1)
        resp = await client.get(f'/api/tasks/{task_id}')
        data = resp.json()
        if data.get('status') in targets:
            return data
        if data.get('status') == 'failed':
            return data
    raise AssertionError(
        f'task {task_id} never reached {target!r} within {timeout}s '
        f'(last status: {data.get("status")!r})'
    )
