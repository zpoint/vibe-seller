"""
E2E test configuration.

Provides:
- Playwright fixtures (authenticated_page, screenshot_on_failure)
- api_client fixture with background SSE auto-answer for LLM tests
- Per-worker provider profile assignment (E2E_PROVIDER_MAP)
"""

import json
import logging
import os
from pathlib import Path
import threading
import time

import httpx
from playwright.sync_api import Page, expect
import pytest

import tests.e2e.e2e_helpers as e2e_helpers
from tests.e2e.e2e_helpers import (
    BASE_URL,
    PIPELINE_TIMEOUT,
    build_profile_env,
    build_smart_answers,
    fetch_presets,
)

ARTIFACTS_DIR = Path(__file__).parent / 'artifacts'

logger = logging.getLogger('e2e')

# Questions whose text carries this tag are owned by a UI test that
# answers them itself (issue #211 free-text click-through); the
# session-wide auto-answer must NOT race it. Kept in sync with the
# mock CLI's ASK_QUESTION (tests/e2e/mock_cli.py).
MANUAL_ANSWER_TAG = '(free-text-e2e)'


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
    """Navigate to base URL and authenticate before each test."""
    # First go to the page (needed for cookie context)
    page.goto(BASE_URL)

    # Login via API to get auth cookie set
    page.evaluate("""async () => {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                identifier: 'admin@vibe-seller.local',
                password: 'admin'
            })
        });
        if (!response.ok) {
            throw new Error('Login failed: ' + await response.text());
        }
        return await response.json();
    }""")

    # Reload page to get authenticated state
    page.goto(BASE_URL)

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
# API client with background SSE auto-answer
# ------------------------------------------------------------------


def _login_client(client: httpx.Client) -> None:
    """Login on an httpx client (sets auth cookie).

    Retries on transient errors (502, connection refused) since
    the server may still be starting up.
    """
    for attempt in range(5):
        try:
            client.post(
                f'{BASE_URL}/api/auth/login',
                json={
                    'identifier': 'admin@vibe-seller.local',
                    'password': 'admin',
                },
            ).raise_for_status()
            return
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            if attempt < 4:
                logger.warning('Login attempt %d failed: %s', attempt + 1, exc)
                time.sleep(2)
            else:
                raise


def _answer_question_api(
    client: httpx.Client,
    task_id: str,
    request_id: str,
    answers: dict,
) -> None:
    """POST answer for a pending agent question."""
    try:
        resp = client.post(
            f'{BASE_URL}/api/tasks/{task_id}/questions/answer',
            json={'request_id': request_id, 'answers': answers},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None) if response else None
        logger.error(
            'Failed to auto-answer question %s (task %s): status=%s %r',
            request_id[:8],
            task_id[:8],
            status,
            exc,
        )


@pytest.fixture(scope='session', autouse=True)
def _sse_auto_answer():
    """Session-wide SSE listener that auto-answers AskUserQuestion.

    autouse=True: runs for ALL tests (including Playwright-only tests
    that create tasks via UI). Prevents any test from hanging when an
    LLM agent asks unexpected questions during planning or execution.

    Scope: session — one SSE thread for the entire pytest run.
    """
    sse_client = httpx.Client(timeout=None)
    _login_client(sse_client)
    answer_client = httpx.Client(timeout=30)
    _login_client(answer_client)

    stop = threading.Event()

    def _listener():
        while not stop.is_set():
            try:
                with sse_client.stream(
                    'GET',
                    f'{BASE_URL}/api/sse',
                    timeout=PIPELINE_TIMEOUT,
                ) as resp:
                    for line in resp.iter_lines():
                        if stop.is_set():
                            break
                        if not line or not line.startswith('data:'):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if event.get('type') != 'task_questions':
                            continue
                        task_id = event.get('task_id', '')
                        request_id = event.get('request_id', '')
                        if task_id and request_id:
                            questions = event.get('questions', [])
                            # Defer questions a UI test owns (issue #211
                            # free-text click-through) — answering them
                            # here would beat the operator to it. Tag
                            # kept in sync with mock_cli.ASK_QUESTION.
                            if any(
                                isinstance(q, dict)
                                and MANUAL_ANSWER_TAG
                                in str(q.get('question') or '')
                                for q in questions
                            ):
                                continue
                            logger.info(
                                '[%s] SSE auto-answering question %s',
                                task_id[:8],
                                request_id[:8],
                            )
                            _answer_question_api(
                                answer_client,
                                task_id,
                                request_id,
                                build_smart_answers(questions),
                            )
            except (httpx.ReadError, httpx.ReadTimeout):
                if stop.is_set():
                    break
                time.sleep(1)
            except Exception:
                if stop.is_set():
                    break
                logger.exception('SSE listener error')
                time.sleep(2)

    thread = threading.Thread(target=_listener, daemon=True)
    thread.start()

    yield

    stop.set()
    sse_client.close()
    thread.join(timeout=5)
    answer_client.close()


@pytest.fixture(scope='session', autouse=True)
def _setup_worker_profile():
    """Assign each xdist worker a provider profile.

    Controlled by E2E_PROVIDER_MAP env var (comma-separated
    provider names, one per worker).  Example: kimi,kimi,minimax

    Spreads rate-limit load across providers so parallel workers
    don't all hammer the same API.
    """
    provider_map_str = os.environ.get('E2E_PROVIDER_MAP', '')
    if not provider_map_str:
        return

    providers = [p.strip() for p in provider_map_str.split(',') if p.strip()]
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

    provider = providers[worker_num]

    profile_id = f'e2e-{provider}-{worker}'

    # Build profile env from server preset + env secrets
    preset_client = httpx.Client(timeout=30)
    _login_client(preset_client)
    try:
        presets = fetch_presets(preset_client)
    finally:
        preset_client.close()

    env = build_profile_env(provider, presets)

    client = httpx.Client(timeout=30)
    _login_client(client)
    try:
        resp = client.post(
            f'{BASE_URL}/api/profiles',
            json={
                'id': profile_id,
                'name': f'E2E {provider} ({worker})',
                'env': env,
            },
        )
        if resp.status_code not in (200, 409):
            pytest.fail(
                f'Failed to create profile {profile_id}: '
                f'{resp.status_code} {resp.text}'
            )
    finally:
        client.close()

    e2e_helpers.DEFAULT_PROFILE_ID = profile_id

    # Set admin user's default profile so UI-created tasks
    # (Playwright tests) also use the worker's provider
    # instead of falling back to server os.environ.
    try:
        client2 = httpx.Client(timeout=30)
        _login_client(client2)
        client2.patch(
            f'{BASE_URL}/api/profiles/{profile_id}/set-default',
        )
        client2.close()
    except Exception:
        logging.getLogger('e2e').warning(
            'Failed to set default profile %s', profile_id, exc_info=True
        )


@pytest.fixture(scope='module')
def api_client():
    """Authenticated httpx client for API-level e2e tests.

    The SSE auto-answer is handled by the session-scoped
    _sse_auto_answer fixture (autouse).
    """
    client = httpx.Client(timeout=30)
    _login_client(client)
    yield client
    client.close()
