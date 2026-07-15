"""Workflow test fixtures: real DB, fake agent, mocked browser/filesystem."""

import asyncio
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
from tests.workflow.fake_agent import FakeAgent

# Save BEFORE any monkeypatching
real_sleep = asyncio.sleep

# Every module that does `from app.database import async_session`
# at module level — must be patched so they all use the test DB.
ASYNC_SESSION_MODULES = [
    'app.database',
    'app.routers.tasks',
    'app.task_runner',
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
    'app.scheduler.cron',
    'app.scheduler.fanout',
    'app.scheduler.plan_reaper',
    'app.scheduler.finalize_reaper',
    'app.routers.workspace_assistant',
    'app.browser.daemon_reaper',
    'app.telemetry_tasks',
]


# ── Core DB ──────────────────────────────────────────


@pytest_asyncio.fixture
async def test_engine():
    """In-memory SQLite with ``isolation_level='AUTOCOMMIT'``.

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
    """
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        isolation_level='AUTOCOMMIT',
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    # Cancel lingering background tasks (e.g. auto_run_task)
    # before tearing down the DB to avoid "closed database" errors.
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task() and not t.done():
            t.cancel()
    await asyncio.sleep(0.2)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def override_async_session(test_engine, monkeypatch):
    """Patch async_session in ALL modules to use the test DB."""
    test_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
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
    monkeypatch.setattr(
        'app.routers.tasks_conversation.agent_manager', fake_agent
    )
    monkeypatch.setattr('app.task_runner_auto.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_runner_exec.agent_manager', fake_agent)
    monkeypatch.setattr('app.task_session_lifecycle.agent_manager', fake_agent)
    monkeypatch.setattr('app.routers.app_settings.agent_manager', fake_agent)
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

        async def fetch_remote(self):
            return {'status': 'ok', 'fetched': 0}

        async def check_and_sync_remote(self):
            pass

        def get_sync_status(self):
            return {'synced': True, 'files': 0}

        def get_sync_meta(self):
            return {
                'last_sync': None,
                'commit': None,
                'last_remote_sync': None,
                'remote_commit': None,
            }

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
    return mock


# ── Mock skills sync ────────────────────────────────


@pytest.fixture
def mock_skills_sync(monkeypatch):
    class _Mock:
        async def fetch(self):
            return {'copied': 0, 'status': 'ok'}

        async def fetch_remote(self):
            return {'status': 'ok', 'fetched': 0}

        async def check_and_sync_remote(self):
            pass

        def get_sync_meta(self):
            return {
                'last_sync': None,
                'commit': None,
                'last_remote_sync': None,
                'remote_commit': None,
            }

    mock = _Mock()
    monkeypatch.setattr('app.routers.workspace.skills_sync', mock)
    monkeypatch.setattr('app.task_runner_auto.skills_sync', mock)
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

        async def save_skill(self, slug, skill_md, files=None):
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or ''):
                raise ValueError(f'Invalid skill slug: {slug!r}')
            if slug in self._synced():
                raise ValueError(f'{slug!r} is a built-in skill')
            d = self.root / '.claude' / 'skills' / slug
            existed = d.is_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / 'SKILL.md').write_text(skill_md)
            for rel, content in (files or {}).items():
                pp = Path(rel)
                if (
                    pp.is_absolute()
                    or not pp.parts
                    or '..' in pp.parts
                    or pp.name == 'SKILL.md'
                ):
                    raise ValueError(f'Invalid skill file path: {rel!r}')
                (d / pp).parent.mkdir(parents=True, exist_ok=True)
                (d / pp).write_text(content)
            return {
                'slug': slug,
                'path': f'.claude/skills/{slug}',
                'action': 'updated' if existed else 'created',
            }

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


async def wait_for_task(
    client: AsyncClient,
    task_id: str,
    target: str = 'completed',
    timeout: float = 10.0,
) -> dict:
    """Poll GET /api/tasks/{id} until status matches target or fails.

    Uses the REAL asyncio.sleep (not the patched fast version) so the
    background pipeline has time to make progress between polls.
    """
    # Give the background pipeline a chance to start before polling
    await real_sleep(0.05)
    for _ in range(int(timeout / 0.1)):
        await real_sleep(0.1)
        resp = await client.get(f'/api/tasks/{task_id}')
        data = resp.json()
        if data.get('status') == target:
            return data
        if data.get('status') == 'failed' and target != 'failed':
            return data
    return data
