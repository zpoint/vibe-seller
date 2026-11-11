"""
E2E test configuration.

Provides:
- Playwright fixtures (authenticated_page, screenshot_on_failure)
- api_client fixture with background SSE auto-answer for LLM tests
- Per-worker provider profile assignment (E2E_PROVIDER_MAP)
"""

import logging
import os
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e import fail_fast
import tests.e2e.e2e_helpers as e2e_helpers
from tests.e2e.e2e_helpers import (
    BASE_URL,  # noqa: F401 — re-exported: the shared-server modules
    # that do `from tests.e2e.conftest import BASE_URL` want the SHARED
    # server's address, which is what this name binds at import; a
    # module that owns its server reads `e2e_helpers.BASE_URL` (the
    # current target) instead, and never via this name.
    admin_credentials,
    provider_list,
)

ARTIFACTS_DIR = Path(__file__).parent / 'artifacts'

logger = logging.getLogger('e2e')


def pytest_configure(config):
    # --maxfail must STOP an xdist run, not drain it (see fail_fast).
    fail_fast.register(config)


def pytest_report_header(config):
    """Show planned worker-to-provider mapping in test header."""
    provider_map = os.environ.get('E2E_PROVIDER_MAP', '')
    if not provider_map:
        return None
    providers = [p.strip() for p in provider_map.split(',') if p.strip()]
    lines = [
        f'E2E provider map: {", ".join(f"gw{i}={p}" for i, p in enumerate(providers))}'
    ]
    return lines


@pytest.fixture
def authenticated_page(page: Page):
    """Navigate to base URL and authenticate before each test.

    The URL is read at fixture time, not import time: a module that
    owns its server has aimed ``e2e_helpers.BASE_URL`` at it, and the
    page must load the app the test is driving.
    """
    # First go to the page (needed for cookie context)
    page.goto(e2e_helpers.BASE_URL)

    # Login via API to get auth cookie set. Credentials come from the
    # shared helper (env-overridable) so a developer's instance, whose
    # admin password is no longer the seeded one, can run UI e2e too.
    page.evaluate(
        """async (creds) => {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(creds)
        });
        if (!response.ok) {
            throw new Error('Login failed: ' + await response.text());
        }
        return await response.json();
    }""",
        admin_credentials(),
    )

    # Reload page to get authenticated state
    page.goto(e2e_helpers.BASE_URL)

    # Wait for app to be ready (Vibe Seller header visible)
    expect(page.locator('h1')).to_have_text('Vibe Seller')

    return page


@pytest.fixture
def screenshot_on_failure(request):
    """Capture screenshot + DOM snapshot on test failure."""
    yield
    rep = getattr(request.node, 'rep_call', None)
    if rep and rep.failed:
        try:
            page = request.getfixturevalue('page')
        except pytest.FixtureLookupError:
            return
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        name = request.node.name.replace('/', '_')
        page.screenshot(
            path=str(ARTIFACTS_DIR / f'{name}.png'),
        )
        dom = page.content()
        (ARTIFACTS_DIR / f'{name}.html').write_text(dom)
        logger.info('Saved artifacts for failed test: %s', name)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach test result to request.node for screenshot_on_failure."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)


# ------------------------------------------------------------------
# The CURRENT server: the shared one, or the one a module owns
# ------------------------------------------------------------------


@pytest.fixture(scope='session', autouse=True)
def _sse_auto_answer():
    """Session-wide SSE listener that auto-answers AskUserQuestion.

    autouse=True: runs for ALL tests (including Playwright-only tests
    that create tasks via UI). Prevents any test from hanging when an
    LLM agent asks unexpected questions during planning or execution.

    Scope: session — one SSE thread for the entire pytest run, on the
    SHARED server. A module that owns its server gets its own listener
    from ``e2e_helpers.point_at_server``; nothing on an owned server is
    answered by this one.
    """
    shutdown = e2e_helpers.start_auto_answer(e2e_helpers.BASE_URL)
    yield
    shutdown()


@pytest.fixture(scope='session', autouse=True)
def _setup_worker_profile():
    """Assign each xdist worker a provider profile ON THE SHARED SERVER.

    Controlled by E2E_PROVIDER_MAP env var (comma-separated
    provider names, one per worker).  Example: kimi,kimi,minimax

    Spreads rate-limit load across providers so parallel workers
    don't all hammer the same API. The profile itself — its env, its
    default, its schedule pins — is built by the ONE implementation in
    ``e2e_helpers.setup_worker_profile``, which a module that owns its
    server calls too (``e2e_helpers.point_at_server``).

    Both the admin default and the schedule pins this installs are
    SERVER-global, so on the shared server every worker writes them and
    the last writer wins; that is acceptable only because they are the
    no-Claude fallback every worker needs anyway. A test that fires a
    system schedule still pins it to its own profile at trigger time.
    """
    providers = provider_list()
    if not os.environ.get('E2E_PROVIDER_MAP', ''):
        return
    if not providers:
        pytest.fail('E2E_PROVIDER_MAP is set but contains no valid entries')

    worker = os.environ.get('PYTEST_XDIST_WORKER', '')
    if worker:
        worker_num = int(worker.replace('gw', ''))
        if worker_num >= len(providers):
            pytest.fail(
                f'E2E_PROVIDER_MAP has {len(providers)} entries '
                f'but worker {worker} (index {worker_num}) is '
                f'out of range'
            )
    else:
        # Single-worker mode (no xdist): use first provider
        worker_num = 0
        worker = 'gw0'

    e2e_helpers.DEFAULT_PROFILE_ID = e2e_helpers.setup_worker_profile(
        e2e_helpers.BASE_URL, providers[worker_num], worker
    )


@pytest.fixture(scope='module')
def api_client():
    """Authenticated httpx client for API-level e2e tests.

    The SSE auto-answer is handled by the session-scoped
    _sse_auto_answer fixture (autouse).

    It logs in against whatever server is CURRENT: a module that owns
    its server has aimed ``e2e_helpers.BASE_URL`` at it for its whole
    run, so the same fixture serves both kinds.
    """
    client = httpx.Client(timeout=30)
    e2e_helpers.login_client(client)
    yield client
    client.close()
