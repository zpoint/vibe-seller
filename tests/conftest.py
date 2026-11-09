"""
Shared pytest fixtures for all tests.
"""

import atexit
import os
import shlex
import shutil
import sys
import tempfile

from tests.collection_order import long_budget_order

# Disable telemetry across the entire test suite. Set BEFORE any
# `app.*` import so app.telemetry never opens a PostHog client when
# the FastAPI lifespan runs under tests. Without this, every workflow
# test (which boots the lifespan, creates stores, fires tasks)
# generates real PostHog events under whatever install_id happens to
# be on disk. ``setdefault`` lets a developer override locally with
# ``VIBE_SELLER_TELEMETRY=1 pytest …`` if they explicitly want to
# exercise the telemetry path against a self-hosted PostHog instance.
os.environ.setdefault('VIBE_SELLER_TELEMETRY', '0')

# -- Test-home isolation (BEFORE any `app.*` import) ------------------
#
# app.config resolves the runtime home (``VIBE_SELLER_DIR``) from
# VIBE_HOME at import time. Without this block, a plain
# ``pytest -m unit`` run makes the TEST PROCESS's home the developer's
# real ``~/.vibe-seller`` — and module-level constants derived from it
# (``_CHAT_STAGING_DIR``, ``HISTORY_DIR``, the import-time
# ``backend_<port>.log``, …) read and WRITE production state. Observed:
# empty ``chat_staging/<task>/`` dirs accumulating in the real home on
# every run for months.
#
# The invariant: the test process's runtime home is never the
# developer's real home, unless it is an e2e run pointed at a server
# that lives there.
#
# ``--e2e`` is read from argv (plus PYTEST_ADDOPTS) because
# pytest_addoption runs AFTER conftest import — this block must precede
# the first ``app.*`` import, which is right below. PYTEST_ADDOPTS
# matters because options injected there never appear in ``sys.argv``:
# without it a ``PYTEST_ADDOPTS='--e2e' pytest tests/e2e`` run would
# execute e2e tests while this block forced a throwaway home and
# skipped the e2e-specific env derivation.


# The controller's decision, published for its pytest-xdist workers.
# A worker is a child process whose sys.argv is execnet's command line —
# it never carries ``--e2e`` — so it must not re-derive the decision; it
# inherits this from the environment the controller spawned it with.
_E2E_MARKER = 'VIBE_TEST_E2E'


def e2e_requested() -> bool:
    """Whether pytest will run with ``--e2e``, read the way this
    pre-import block can: the decision the controller published (xdist
    workers), else the raw command line plus PYTEST_ADDOPTS (both
    spellings pytest honours before its own config parsing)."""
    if os.environ.get(_E2E_MARKER) == '1':
        return True
    tokens = list(sys.argv[1:]) + shlex.split(
        os.environ.get('PYTEST_ADDOPTS', '')
    )
    return any('--e2e' in token for token in tokens)


_E2E_RUN = e2e_requested()
if _E2E_RUN:
    os.environ[_E2E_MARKER] = '1'

if _E2E_RUN:
    # E2e tests drive a SEPARATELY RUNNING server. The test process
    # resolves DB/workspace/log paths through app.config, so its home
    # must be THAT server's home: E2E_VIBE_HOME wins, then an explicit
    # VIBE_HOME, else the default (~/.vibe-seller — CI's server lives
    # there; keep CI working unchanged). Same FILESYSTEM required: the
    # e2e helpers seed/read workspace files by local path — a remote
    # E2E_BASE_URL cannot work with E2E_VIBE_HOME (see
    # docs/testing.md § Test-home isolation).
    if os.environ.get('E2E_VIBE_HOME'):
        os.environ['VIBE_HOME'] = os.environ['E2E_VIBE_HOME']
    # BACKEND_PORT from the target URL so anything the test process
    # derives port-wise (the import-time ``backend_<port>.log``) names
    # the same file the server writes — a test-side log can never
    # masquerade as the server's decoy. A URL without an explicit
    # port means the scheme's default (80/443); naming THAT is still
    # more honest than leaving BACKEND_PORT unset.
    from urllib.parse import urlparse

    _parsed = urlparse(os.environ.get('E2E_BASE_URL', ''))
    if _parsed.port:
        os.environ.setdefault('BACKEND_PORT', str(_parsed.port))
    elif _parsed.scheme:
        os.environ.setdefault(
            'BACKEND_PORT', '443' if _parsed.scheme == 'https' else '80'
        )
else:
    # Every other run gets a FRESH throwaway home, forced — a stale
    # VIBE_HOME in the environment must not quietly keep pointing the
    # suite at a real (or production) home.
    #
    # Idempotent across this file being imported as TWO module
    # objects (pytest loads it as ``conftest`` for the session and
    # ``tests.conftest`` for tests/unit/test_conftest_home_isolation
    # — verified: the two are not the same module object). The second
    # import must reuse the home the first one forced — app.config has
    # already latched it — and must not register a second atexit
    # rmtree. The marker env var carries the value across both.
    _FORCED_HOME = os.environ.get('VIBE_TEST_FORCED_HOME', '')
    if _FORCED_HOME and os.path.isdir(_FORCED_HOME):
        _TEST_HOME = _FORCED_HOME
    else:
        _TEST_HOME = tempfile.mkdtemp(prefix='vibe-seller-test-home-')
        os.environ['VIBE_TEST_FORCED_HOME'] = _TEST_HOME
        atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)
    os.environ['VIBE_HOME'] = _TEST_HOME

# Arm NO sync timers across the suite (jobs still register, so a test
# can trigger one). Left armed, every test that boots the app would poll
# GitHub (and an ads service) on its own; e2e runs opt back in
# explicitly, because their server is pointed at a fake GitHub.
if not _E2E_RUN:
    os.environ.setdefault('VIBE_SYNC_DISABLED', '1')
# Same for the boot-time browser-engine check: it runs a Playwright
# subprocess and may download Chromium. Its own tests enable it.
os.environ.setdefault('VIBE_ENSURE_BROWSER_ENGINE', 'false')

from collections.abc import AsyncGenerator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
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
from app.ai import provider_presets_sync as presets_sync  # noqa: E402
from app.ai.turn_hold_budget import _ledgers  # noqa: E402, PLC2701
from app.auth import create_token  # noqa: E402
from app.config import VIBE_SELLER_DIR  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.user import User  # noqa: E402


def assert_test_home_isolated(
    vibe_seller_dir: Path,
    real_home: Path,
    *,
    e2e_run: bool,
    forced_home: str | None = None,
) -> None:
    """The loud guard behind the test-home isolation above.

    In a NON-e2e run, the resolved runtime home must be the forced
    throwaway home — never the developer's real ``~/.vibe-seller``
    and never any OTHER pre-existing home (e.g. an exported
    ``VIBE_HOME`` that something latched by importing ``app.*``
    before this conftest ran). Either shape means the isolation did
    not take effect, and every module-level path derived from it —
    chat staging, task history, the backend log, profiles.json —
    silently reads and writes that home's real state. Fail the whole
    session instead.

    An e2e run is EXEMPT: its ``VIBE_HOME`` deliberately resolves to
    the server's home, which on CI and a dev box IS ``~/.vibe-seller``.
    Kept as a plain function so its logic is unit-tested
    (tests/unit/test_conftest_home_isolation.py).
    """
    if e2e_run:
        return
    resolved = Path(vibe_seller_dir).resolve()
    default_home = (Path(real_home) / '.vibe-seller').resolve()
    if resolved == default_home:
        raise RuntimeError(
            'pytest is running against the REAL ~/.vibe-seller '
            f'({default_home}) in a non-e2e run — refusing. The test '
            'home isolation in tests/conftest.py did not take effect; '
            'most likely something imported app.* before conftest, or '
            'the isolation block was changed. Fix the import order, '
            'do not point the suite at a real home.'
        )
    if forced_home is not None and resolved != Path(forced_home).resolve():
        raise RuntimeError(
            f'pytest resolved the runtime home to {resolved} in a '
            f'non-e2e run, which is neither the real ~/.vibe-seller '
            f'nor the forced test home ({forced_home}) — refusing. '
            'Something imported app.* (latching VIBE_SELLER_DIR) '
            'before tests/conftest.py forced the throwaway home — '
            'most likely a -p plugin or a conftest ordering change. '
            'Fix the import order; do not point the suite at a real '
            'home.'
        )


assert_test_home_isolated(
    VIBE_SELLER_DIR,
    Path.home(),
    e2e_run=_E2E_RUN,
    forced_home=os.environ.get('VIBE_TEST_FORCED_HOME'),
)

# -- E2E test gating --------------------------------------------------
# Tests marked @pytest.mark.e2e are deselected (not collected) unless
# --e2e is passed.  This prevents e2e conftest from loading and
# trying to connect to a server that doesn't exist.


@pytest.fixture(autouse=True)
def _isolate_wrapper_bin_dir(tmp_path_factory, monkeypatch):
    """Keep wrapper writes/reaps out of the developer's real
    ``~/.vibe-seller/bin/``.

    Workflow tests create stores through the real ``BrowserManager``
    (e.g. "Retry Test Store"), which generates a wrapper on disk. With
    no isolation those landed in the developer's live bin dir and
    *stayed* — orphan wrappers for stores that never existed outside a
    test run, each pinning a frozen ``proxy_port`` that a real store can
    later be allocated. Boot-time reaping is worse: it deletes, and a
    test DB holds only its own stores, so a reap aimed at a real bin dir
    wipes the wrappers of stores an actual task is using right now
    (observed — it removed every live store's wrapper mid-run).

    Both directions are the same bug: the suite must not share this
    mutable state with the machine it runs on.
    """
    bin_dir = tmp_path_factory.mktemp('vs_bin')
    monkeypatch.setattr('app.browser.wrapper._BIN_DIR', bin_dir, raising=False)
    monkeypatch.setattr(
        'app.browser.manager.BROWSER_USE_BIN_DIR', bin_dir, raising=False
    )
    return bin_dir


@pytest.fixture(autouse=True)
def _isolate_presets_sync(tmp_path_factory, monkeypatch):
    """Keep the provider-preset catalog cache out of the machine.

    ``app.ai.provider_presets_sync`` seeds its served catalog from
    ``$VIBE_SELLER_DIR/cache/provider_presets.json`` — a developer who
    has run the app has a REAL one there, and tests asserting on
    preset contents (kimi's model id, the MiMo group, …) would then
    depend on whatever the background sync last fetched. Point the
    module at a per-test dir and reset its in-memory catalog so every
    test starts from the bundled data.
    """
    presets_sync.reset()
    monkeypatch.setattr(
        presets_sync, 'VIBE_SELLER_DIR', tmp_path_factory.mktemp('vs_presets')
    )
    yield
    presets_sync.reset()


def pytest_addoption(parser):
    parser.addoption(
        '--e2e',
        action='store_true',
        default=False,
        help='Run e2e tests (requires running server + LLM secrets)',
    )
    parser.addoption(
        '--windows',
        action='store_true',
        default=False,
        help=('Run Windows-specific tests (auto-enabled on Windows)'),
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--e2e'):
        items[:] = [item for item in items if 'e2e' not in item.keywords]

    run_windows = config.getoption('--windows') or os.name == 'nt'
    if not run_windows:
        items[:] = [i for i in items if 'windows' not in i.keywords]

    # Long-budget tests lead their worker's queue (worksteal's
    # contiguous split gives each worker at most one) AND land on
    # distinct provider keys when the map allows — two heavy
    # generations sharing one key queue at the provider (CI 201i).
    # See tests/collection_order.py.
    workers = getattr(config.option, 'numprocesses', None) or 1
    providers = [
        p for p in os.environ.get('E2E_PROVIDER_MAP', '').split(',') if p
    ]
    items[:] = long_budget_order(
        items, providers[:workers] if providers else None, workers=workers
    )


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


@pytest.fixture(autouse=True)
def _isolate_turn_hold_ledger():
    """Keep one test's turn-hold spend out of the next test's turn.

    The re-drive budget is keyed by task so a respawn inside a turn
    inherits it (``app/ai/turn_hold_budget.py``). In production a turn ends
    and an orchestrator resets it; unit tests build ``AgentSession``
    directly, never enter an orchestrator, and reuse the same
    ``task_id='test-task'`` — so without this a test that spends the
    budget hands the next one a session that is already exhausted.
    """
    _ledgers.clear()
    yield
    _ledgers.clear()
