"""
Shared pytest fixtures for all tests.
"""

import os

# Disable telemetry across the entire test suite. Set BEFORE any
# `app.*` import so app.telemetry never opens a PostHog client when
# the FastAPI lifespan runs under tests. Without this, every workflow
# test (which boots the lifespan, creates stores, fires tasks)
# generates real PostHog events under whatever install_id happens to
# be on disk. ``setdefault`` lets a developer override locally with
# ``VIBE_SELLER_TELEMETRY=1 pytest …`` if they explicitly want to
# exercise the telemetry path against a self-hosted PostHog instance.
os.environ.setdefault('VIBE_SELLER_TELEMETRY', '0')

from collections.abc import AsyncGenerator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
import uuid  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker  # noqa: E402

# Force-import every model so Base.metadata.create_all() in the
# async_db_session fixture below sees every table. SQLAlchemy only
# registers a table on Base.metadata when its model class has been
# imported — without this `from app import models`, tables for any
# model not separately imported (e.g. StoreEmailLink, EmailAccount,
# Event, ...) silently aren't created and a test that touches them
# fails later with "no such table: <name>".
from app import models  # noqa: F401, E402
from app.auth import create_token  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.user import User  # noqa: E402

# -- E2E test gating --------------------------------------------------
# Tests marked @pytest.mark.e2e are deselected (not collected) unless
# --e2e is passed.  This prevents e2e conftest from loading and
# trying to connect to a server that doesn't exist.


def pytest_addoption(parser):
    parser.addoption(
        '--e2e',
        action='store_true',
        default=False,
        help='Run e2e tests (requires running server + LLM secrets)',
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--e2e'):
        items[:] = [item for item in items if 'e2e' not in item.keywords]


# Test database URL - in-memory SQLite for fast tests
TEST_DATABASE_URL = 'sqlite+aiosqlite:///:memory:'


@pytest_asyncio.fixture(scope='function')
async def async_engine():
    """Create async engine for test session."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope='function')
async def async_db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    # Create tables
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    async_session = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session

    # Drop tables
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def override_get_db(async_db_session):
    """Override the get_db dependency to use test session."""

    async def _get_db():
        yield async_db_session

    return _get_db


@pytest_asyncio.fixture
async def test_user(async_db_session: AsyncSession) -> User:
    """Create a test user."""

    user = User(
        id=str(uuid.uuid4()),
        username='testuser',
        email='test@example.com',
        password_hash='$2b$12$test_hash',  # Not a real hash
        role='user',
        is_active=True,
        created_at=datetime.now(UTC).isoformat(),
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_store(async_db_session: AsyncSession, test_user: User) -> Store:
    """Create a test store with Chrome backend."""

    store = Store(
        id=str(uuid.uuid4()),
        name='Test Store',
        browser_backend='chrome',
        browser_config='{"headless": true}',
        platforms='["amazon"]',
        countries='["US"]',
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )
    async_db_session.add(store)
    await async_db_session.commit()
    await async_db_session.refresh(store)
    return store


@pytest_asyncio.fixture
async def test_task(
    async_db_session: AsyncSession, test_store: Store, test_user: User
) -> Task:
    """Create a test task."""

    task = Task(
        id=str(uuid.uuid4()),
        title='Test Task',
        description='Test task description',
        store_id=test_store.id,
        created_by=test_user.id,
        status='pending',
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )
    async_db_session.add(task)
    await async_db_session.commit()
    await async_db_session.refresh(task)
    return task


@pytest.fixture
def auth_token(test_user: User) -> str:
    """Generate JWT token for test user."""
    return create_token(test_user.id, 'user')


@pytest.fixture
def auth_headers(auth_token: str) -> dict:
    """Headers with authentication."""
    return {'Authorization': f'Bearer {auth_token}'}


@pytest_asyncio.fixture
async def async_client(
    async_db_session: AsyncSession, override_get_db
) -> AsyncGenerator[AsyncClient, None]:
    """Create async HTTP client with database override."""
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def authenticated_client(
    async_client: AsyncClient, test_user: User, auth_token: str
) -> AsyncClient:
    """Authenticated async HTTP client using cookies."""
    # Set auth cookie for cookie-based authentication
    async_client.cookies.set('auth_token', auth_token)
    return async_client


@pytest.fixture
def mock_agent_manager(monkeypatch):
    """Mock the agent manager for tests."""

    class MockAgentManager:
        def __init__(self):
            self.running_tasks = {}

        async def run(self, task_id: str, store_id: str, instruction: str):
            self.running_tasks[task_id] = True
            return True

        async def stop(self, task_id: str):
            self.running_tasks.pop(task_id, None)
            return True

        async def submit_answer(self, task_id: str, answer: str):
            return True

        def is_running(self, task_id: str) -> bool:
            return task_id in self.running_tasks

    mock = MockAgentManager()
    monkeypatch.setattr('app.ai.claude_backend.agent_manager', mock)
    return mock


@pytest.fixture
def mock_browser_manager(monkeypatch):
    """Mock the browser manager for tests."""

    class MockBrowserSession:
        def __init__(self):
            self.browser = None
            self.closed = False

        async def close(self):
            self.closed = True

    class MockBrowserManager:
        def __init__(self):
            self.sessions = {}

        async def get_or_create_session(
            self, store_id: str, browser_type: str, config: dict
        ):
            if store_id not in self.sessions:
                self.sessions[store_id] = MockBrowserSession()
            return self.sessions[store_id]

        async def close_session(self, store_id: str):
            if store_id in self.sessions:
                await self.sessions[store_id].close()
                del self.sessions[store_id]

        def is_session_active(self, store_id: str) -> bool:
            return (
                store_id in self.sessions and not self.sessions[store_id].closed
            )

    mock = MockBrowserManager()
    monkeypatch.setattr('app.browser.manager.browser_manager', mock)
    return mock


@pytest.fixture
def sample_browser_config():
    """Sample browser configuration."""
    return {
        'headless': True,
        'args': ['--no-sandbox', '--disable-dev-shm-usage'],
    }
