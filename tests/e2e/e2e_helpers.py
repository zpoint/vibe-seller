"""
Shared helpers for E2E tests.

Consolidates duplicated login, polling, task creation, and
question-answering code that was copy-pasted across 5+ files.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

import httpx


# ── The shared e2e server's address: ONE definition ────────────
#
# Everything derived from "which server are the tests talking to" —
# its port, its log files, the port own_server must never touch —
# computes from BASE_URL here. A second fallback default anywhere
# sends one test to another server's log (observed: a reader looking
# for backend_7811.log while the server wrote backend_7777.log).
# E2E_PORT, where it still appears in run commands, feeds BASE_URL
# rather than living as a side channel.
def _base_url() -> str:
    explicit = os.environ.get('E2E_BASE_URL')
    if explicit:
        return explicit
    port = os.environ.get('E2E_PORT')
    return f'http://127.0.0.1:{port}' if port else 'http://127.0.0.1:7777'


def _port_of(url: str) -> int:
    parsed = urlparse(url)
    return parsed.port or (443 if parsed.scheme == 'https' else 80)


BASE_URL = _base_url()
#: The shared server's port — same value tests/conftest.py feeds to
#: ``BACKEND_PORT`` (a URL without an explicit port means the scheme's
#: default; the app's own default is 7777 when no URL is set at all).
SHARED_PORT = _port_of(BASE_URL)


def start_sh_log_path(port: int) -> Path:
    """Where ``start.sh`` writes the app log for *port*.

    DETERMINISTIC: ``start.sh`` forces ``LOG_DIR`` to the repo's
    ``logs/`` before launching uvicorn, and the app writes
    ``backend_<port>.log`` there. Derived from THIS file's location,
    not the process cwd. The owner of a server (OwnServer, which runs
    ``start.sh`` itself) knows this path from the moment it picks the
    port; only a non-owner searches (see ``server_log_candidates``).
    """
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / 'logs' / f'backend_{port}.log'


def server_log_candidates(base_url: str | None = None) -> tuple[Path, ...]:
    """Where the app log of the server we talk to can be — one port.

    The app writes ``backend_<port>.log`` under the ``LOG_DIR`` its own
    process runs with: ``vibe-seller start`` puts that in the runtime
    home's ``logs/`` (CI), ``start.sh`` exports the checkout's
    ``logs/``. The test process cannot see the server's env, so all
    the real locations for THAT ONE port are candidates (LOG_DIR as
    the test process resolves it, the runtime home, the checkout).
    Every name is PER-PORT: the server this port names owns its log,
    and no reader may assume a shared name.
    """
    from app.config import LOG_DIR, VIBE_SELLER_DIR

    port = _port_of(base_url or BASE_URL)
    name = f'backend_{port}.log'
    candidates = [
        LOG_DIR / name,  # as the test process resolves it
        VIBE_SELLER_DIR / 'logs' / name,  # `vibe-seller start` (CI)
        start_sh_log_path(port),  # start.sh's own logs dir
    ]
    seen: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return tuple(seen)


def server_log_path(base_url: str | None = None) -> Path:
    """The app log of the server we talk to (first candidate that
    exists)."""
    candidates = server_log_candidates(base_url)
    for path in candidates:
        if path.is_file():
            return path
    raise AssertionError(
        f'no server log found at any of {[str(p) for p in candidates]}'
    )


POLL_INTERVAL = 3  # seconds between status polls
PIPELINE_TIMEOUT = 600  # max seconds for entire pipeline (Kimi can take 8+ min)

_logger = logging.getLogger('e2e')

# Set by conftest per xdist worker to spread tasks across providers.
DEFAULT_PROFILE_ID: str | None = None


def admin_credentials() -> dict[str, str]:
    """The account every e2e logs in as.

    Defaults are the seeded admin, so CI (fresh DB) is unchanged. They
    are overridable because a developer's own instance has long since
    changed the admin password — with the values hardcoded, running any
    e2e locally meant either editing this file or resetting the real
    user's credentials.
    """
    return {
        'identifier': os.environ.get(
            'E2E_ADMIN_IDENTIFIER', 'admin@vibe-seller.local'
        ),
        'password': os.environ.get('E2E_ADMIN_PASSWORD', 'admin'),
    }


def _task_log(task_id: str) -> logging.LoggerAdapter:
    """Logger adapter that annotates log records with task_id."""
    return logging.LoggerAdapter(_logger, {'task_id': task_id[:8]})


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------


def get_secret(*keys: str) -> str:
    """Return first non-empty env var value."""
    for key in keys:
        val = (os.environ.get(key) or '').strip()
        if val:
            return val
    return ''


def resolve_provider(name: str) -> tuple[str, str, str]:
    """Return (api_key, base_url, model) for a named provider.

    Works with any provider whose secrets follow the
    ``{UPPER}_API_KEY / {UPPER}_BASE_URL / {UPPER}_MODEL``
    convention.
    """
    upper = name.upper()
    api_key = get_secret(f'{upper}_API_KEY')
    base_url = get_secret(f'{upper}_BASE_URL')
    model = get_secret(f'{upper}_MODEL')
    return api_key, base_url, model


def fetch_presets(client: httpx.Client, base_url: str | None = None) -> dict:
    """Fetch provider presets from the server."""
    resp = client.get(f'{base_url or BASE_URL}/api/profiles/presets')
    resp.raise_for_status()
    return resp.json().get('presets', {})


def build_profile_env(
    provider: str,
    presets: dict,
) -> dict:
    """Build profile env from a server preset + env secrets.

    Mirrors how the web UI creates profiles: pick a preset,
    fill in the auth token.  Env secrets (``{PROVIDER}_API_KEY``,
    ``{PROVIDER}_BASE_URL``, ``{PROVIDER}_MODEL``) override preset
    defaults when set.
    """
    api_key, base_url, model = resolve_provider(provider)
    if not api_key:
        raise ValueError(f'Missing API key for provider {provider!r}')
    preset = presets.get(provider)
    if not preset:
        raise ValueError(f'No preset for provider {provider!r}')
    env = dict(preset['env'])
    env['ANTHROPIC_AUTH_TOKEN'] = api_key
    if base_url:
        env['ANTHROPIC_BASE_URL'] = base_url
    if model:
        env['ANTHROPIC_MODEL'] = model
    return env


def get_distinct_providers() -> tuple[str, str]:
    """Return two distinct provider names from E2E_PROVIDER_MAP.

    E.g. ``deepseek,deepseek,minimax`` → ``('deepseek', 'minimax')``.
    Raises ``ValueError`` when the map is unset or contains fewer
    than 2 distinct providers.
    """
    raw = os.environ.get('E2E_PROVIDER_MAP', '')
    seen: list[str] = []
    for p in raw.split(','):
        p = p.strip()
        if p and p not in seen:
            seen.append(p)
    if len(seen) >= 2:
        return seen[0], seen[1]
    raise ValueError(
        'E2E_PROVIDER_MAP must contain at least 2 distinct '
        f'providers, got: {raw!r}'
    )


def login(client: httpx.Client) -> dict:
    """Login and return user info. Sets auth cookie on client."""
    resp = client.post(f'{BASE_URL}/api/auth/login', json=admin_credentials())
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------
# Task CRUD
# ------------------------------------------------------------------


def get_task(client: httpx.Client, task_id: str) -> dict:
    """Fetch current task state."""
    resp = client.get(f'{BASE_URL}/api/tasks/{task_id}')
    resp.raise_for_status()
    return resp.json()


def get_messages(client: httpx.Client, task_id: str) -> list[dict]:
    """Fetch all messages for a task."""
    resp = client.get(f'{BASE_URL}/api/tasks/{task_id}/messages')
    resp.raise_for_status()
    return resp.json()


def create_store(
    client: httpx.Client,
    name: str,
    timeout: float = 120.0,
    **kwargs,
) -> dict:
    """Create a store via API.

    ``timeout`` is generous on purpose. Creating a store initialises a
    git workspace, snapshots skills, and writes the browser wrapper; on
    a box that is also running an agent, that overruns the client's
    30s default. The request still SUCCEEDS server-side, so a client
    timeout leaves an orphan store behind and reads as a mysterious
    ReadTimeout at the top of an unrelated test.
    """
    payload = {'name': name, **kwargs}
    resp = client.post(f'{BASE_URL}/api/stores', json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def create_task(
    client: httpx.Client,
    title: str,
    *,
    store_id: str | None = None,
    description: str | None = None,
    profile_id: str | None = None,
    plan_mode: bool | None = None,
    schedule_id: str | None = None,
) -> dict:
    """Create a task via API. Auto-triggers the agent pipeline."""
    payload: dict = {'title': title}
    if store_id is not None:
        payload['store_id'] = store_id
    if description is not None:
        payload['description'] = description
    effective_profile = profile_id or DEFAULT_PROFILE_ID
    if effective_profile is not None:
        payload['profile_id'] = effective_profile
    if plan_mode is not None:
        payload['plan_mode'] = plan_mode
    if schedule_id is not None:
        payload['schedule_id'] = schedule_id
    resp = client.post(f'{BASE_URL}/api/tasks', json=payload)
    resp.raise_for_status()
    data = resp.json()
    log = _task_log(data['id'])
    log.info('created task %s: %s', data['id'][:8], title)
    return data


# ------------------------------------------------------------------
# Polling
# ------------------------------------------------------------------


def poll_task_status(
    client: httpx.Client,
    task_id: str,
    target_statuses: set[str],
    *,
    fail_statuses: set[str] | None = None,
    timeout: int = PIPELINE_TIMEOUT,
) -> dict:
    """Poll task status until target reached.

    Returns:
        Task dict when a target or fail status is reached.

    Raises:
        TimeoutError: if timeout expires without reaching target. The
        task's agent is stopped first (the same endpoint the UI's stop
        button uses) — a poll that gives up must relinquish the task,
        or the agent keeps running against fixtures the failing test is
        tearing down.

    On fail_status: returns the task data (caller decides what to do).
    This matches the majority of existing callers.
    """
    log = _task_log(task_id)
    start = time.time()
    last_status = ''
    data: dict = {}

    while time.time() - start < timeout:
        data = get_task(client, task_id)
        status = data.get('status', '')

        if status != last_status:
            elapsed = int(time.time() - start)
            log.info('status=%s (after %ds)', status, elapsed)
            last_status = status

        if status in target_statuses:
            return data

        if fail_statuses and status in fail_statuses:
            return data

        time.sleep(POLL_INTERVAL)

    # Best-effort: a stop failure must not mask the TimeoutError.
    # Observed in CI (PR #181 e2e run 41162900220): the timed-out
    # test's mock-console fixture was torn down while the agent kept
    # running, and the agent spent its last minutes trying to restart
    # the dead console instead of finishing.
    try:
        r = client.post(f'{BASE_URL}/api/tasks/{task_id}/agent/stop')
        log.info(
            'timeout: stopped agent (HTTP %s, status=%s)',
            r.status_code,
            last_status,
        )
    except Exception as exc:  # noqa: BLE001 - never mask the timeout
        log.warning('timeout: could not stop agent: %s', exc)

    raise TimeoutError(
        f'Task {task_id[:8]} did not reach {target_statuses} '
        f'within {timeout}s (last status={last_status})'
    )


# ------------------------------------------------------------------
# Question answering
# ------------------------------------------------------------------


def build_smart_answers(questions: list[dict]) -> dict:
    """Build answers by selecting the first option for each question.

    Falls back to a generic text answer if no structured questions.
    """
    if not questions:
        return {'answer': 'Please proceed with default settings.'}

    answers = {}
    for q in questions:
        options = q.get('options', [])
        key = q.get('question', q.get('header', ''))
        if options:
            if q.get('multiSelect'):
                answers[key] = [options[0].get('label', '')]
            else:
                answers[key] = options[0].get('label', '')
        else:
            answers[key] = 'Use default settings.'
    return answers


def answer_question(
    client: httpx.Client,
    task_id: str,
    request_id: str,
    answers: dict,
    base_url: str | None = None,
) -> dict:
    """Submit an answer for a pending agent question."""
    resp = client.post(
        f'{base_url or BASE_URL}/api/tasks/{task_id}/questions/answer',
        json={'request_id': request_id, 'answers': answers},
    )
    resp.raise_for_status()
    return resp.json()


# ── A module that OWNS a server: point the helpers at it ─────────
#
# Everything here talks to ONE module-global target, ``BASE_URL``, and
# reads it at CALL time. A test module that owns its server
# (``tests/e2e/own_server.py``) therefore only has to aim these two
# globals at it for the duration of the module — every helper call,
# the profile and the SSE auto-answer come along — and hand the shared
# server's values back on teardown, because the SAME xdist worker runs
# other modules' tests after this one finishes.
#
# Questions whose text carries this tag are owned by a UI test that
# answers them itself (issue #211 free-text click-through); the
# auto-answer must NOT race it. Kept in sync with the mock CLI's
# ASK_QUESTION (tests/e2e/mock_cli.py).
MANUAL_ANSWER_TAG = '(free-text-e2e)'


def provider_list() -> list[str]:
    """The per-worker providers from ``E2E_PROVIDER_MAP``."""
    raw = os.environ.get('E2E_PROVIDER_MAP', '')
    return [p.strip() for p in raw.split(',') if p.strip()]


def worker_name() -> str:
    """This xdist worker ('gw3'), or 'gw0' for a single-process run."""
    return os.environ.get('PYTEST_XDIST_WORKER', '') or 'gw0'


#: How many servers THIS worker has owned so far. A run can hold more
#: owned servers than the provider map has entries (6 modules, 4
#: workers), and one worker may own two at different times; the map is
#: the pool of distinct provider KEYS, so owned servers take it in
#: round-robin — the first ``len(map)`` owned servers in a run get a
#: key nobody else is hammering, and only an over-subscribed run shares.
_owned_slots = 0


def owned_server_provider() -> tuple[str, str]:
    """(provider, worker tag) for the next server this worker owns.

    '' when the run carries no ``E2E_PROVIDER_MAP`` — then nothing is
    configured on the owned server either, exactly as on the shared one.
    """
    global _owned_slots
    providers = provider_list()
    slot = _owned_slots
    _owned_slots += 1
    if not providers:
        return '', f'{worker_name()}-own{slot}'
    return providers[slot % len(providers)], f'{worker_name()}-own{slot}'


def login_client(client: httpx.Client, base_url: str | None = None) -> None:
    """Login on an httpx client (sets the auth cookie), with retries.

    Retries on transient errors (502, connection refused): the server
    may still be starting. Credentials come from
    :func:`admin_credentials`.
    """
    url = f'{base_url or BASE_URL}/api/auth/login'
    for attempt in range(5):
        try:
            client.post(url, json=admin_credentials()).raise_for_status()
            return
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            if attempt < 4:
                _logger.warning('Login attempt %d failed: %s', attempt + 1, exc)
                time.sleep(2)
            else:
                raise


def setup_worker_profile(base_url: str, provider: str, worker: str) -> str:
    """Put THIS worker's provider profile on the server at *base_url*.

    One implementation for both kinds of server — the shared one
    (``tests/e2e/conftest.py::_setup_worker_profile``) and every server
    a module owns — because an owned server needs the SAME thing: CI
    provisions no Claude credentials, so any task that falls through to
    the 'default' (Claude) profile dies mid-run with "Not logged in".

    Returns the profile id. It routes EVERY agent task on that server
    through the worker's provider:

      (a) the admin default → UI-created tasks + admin-owned schedules.
      (b) system schedules (e.g. catalog sync) are owned by the ai_bot
          user, whose default is never set, so (a) does not reach them —
          pin the provider directly on every schedule still resolving
          to 'default' (a pin has top precedence in resolution).

    FAILS LOUD on any error: a swallowed failure here is exactly what
    once let catalog sync silently fall back to Claude.
    """
    profile_id = f'e2e-{provider}-{worker}'
    preset_client = httpx.Client(timeout=30)
    login_client(preset_client, base_url)
    try:
        presets = fetch_presets(preset_client, base_url)
    finally:
        preset_client.close()

    env = build_profile_env(provider, presets)

    client = httpx.Client(timeout=30)
    login_client(client, base_url)
    try:
        resp = client.post(
            f'{base_url}/api/profiles',
            json={
                'id': profile_id,
                'name': f'E2E {provider} ({worker})',
                'env': env,
            },
        )
        if resp.status_code not in (200, 409):
            raise AssertionError(
                f'Failed to create profile {profile_id}: '
                f'{resp.status_code} {resp.text}'
            )
    finally:
        client.close()

    client2 = httpx.Client(timeout=30)
    login_client(client2, base_url)
    try:
        resp = client2.patch(
            f'{base_url}/api/profiles/{profile_id}/set-default'
        )
        if resp.status_code != 200:
            raise AssertionError(
                f'set-default {profile_id} failed: '
                f'{resp.status_code} {resp.text}'
            )

        resp = client2.get(f'{base_url}/api/schedules')
        resp.raise_for_status()
        for sched in resp.json():
            if sched.get('ai_profile_id') in (None, '', 'default'):
                pinned = client2.put(
                    f'{base_url}/api/schedules/{sched["id"]}',
                    json={'ai_profile_id': profile_id},
                )
                if pinned.status_code != 200:
                    raise AssertionError(
                        f'pin schedule {sched["id"]} to {profile_id} '
                        f'failed: {pinned.status_code} {pinned.text}'
                    )
    finally:
        client2.close()
    return profile_id


def _auto_answer_listener(base_url: str, stop: threading.Event) -> None:
    """Answer every pending agent question on the server at *base_url*.

    Thread body for :func:`start_auto_answer`; exits when *stop* is set.
    """
    sse_client = httpx.Client(timeout=None)
    answer_client = httpx.Client(timeout=30)
    try:
        login_client(sse_client, base_url)
        login_client(answer_client, base_url)
    except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
        # No default admin / server not up (e.g. a customized local box).
        # The auto-answer only matters for LLM tests, which need auth
        # anyway — let those tests hang/skip on their own terms rather
        # than erroring the whole session.
        _logger.warning('SSE auto-answer disabled (login failed): %s', exc)
        sse_client.close()
        answer_client.close()
        return

    while not stop.is_set():
        try:
            with sse_client.stream(
                'GET', f'{base_url}/api/sse', timeout=PIPELINE_TIMEOUT
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
                    if not (task_id and request_id):
                        continue
                    questions = event.get('questions', [])
                    # Defer questions a UI test owns (issue #211
                    # free-text click-through) — answering them here
                    # would beat the operator to it.
                    if any(
                        isinstance(q, dict)
                        and MANUAL_ANSWER_TAG in str(q.get('question') or '')
                        for q in questions
                    ):
                        continue
                    _logger.info(
                        '[%s] SSE auto-answering question %s',
                        task_id[:8],
                        request_id[:8],
                    )
                    answer_question(
                        answer_client,
                        task_id,
                        request_id,
                        build_smart_answers(questions),
                        base_url,
                    )
        except (httpx.ReadError, httpx.ReadTimeout):
            if stop.is_set():
                break
            time.sleep(1)
        except Exception:
            if stop.is_set():
                break
            _logger.exception('SSE listener error')
            time.sleep(2)

    sse_client.close()


def start_auto_answer(base_url: str) -> Callable[[], None]:
    """Auto-answer agent questions on *base_url* until the returned
    callable is called.

    A server an e2e module OWNS has nobody else listening to it: the
    session-wide listener in ``tests/e2e/conftest.py`` follows the
    SHARED server, so without this a model that asks a clarifying
    question on an owned server parks its task until the poll times
    out. Same thread, same loop, one per server.
    """
    stop = threading.Event()
    thread = threading.Thread(
        target=_auto_answer_listener, args=(base_url, stop), daemon=True
    )
    thread.start()

    def _shutdown() -> None:
        stop.set()
        thread.join(timeout=5)

    return _shutdown


@contextmanager
def point_at_server(base_url: str) -> Generator[str, None, None]:
    """Aim the helpers at a server THIS module owns, then hand back.

    Refuses the shared server: this exists to move a module OFF it, and
    pointing the target at the shared server would look like ownership
    while racing every other worker.
    """
    global BASE_URL, DEFAULT_PROFILE_ID
    if _port_of(base_url) == SHARED_PORT:
        raise ValueError(
            f'{base_url} is the SHARED e2e server — point_at_server is '
            'for a server the module owns (tests/e2e/own_server.py)'
        )
    saved_url, saved_profile = BASE_URL, DEFAULT_PROFILE_ID
    shutdown: Callable[[], None] | None = None
    try:
        BASE_URL = base_url
        provider, worker = owned_server_provider()
        if provider:
            DEFAULT_PROFILE_ID = setup_worker_profile(
                base_url, provider, worker
            )
        shutdown = start_auto_answer(base_url)
        yield base_url
    finally:
        if shutdown is not None:
            shutdown()
        BASE_URL, DEFAULT_PROFILE_ID = saved_url, saved_profile


def assert_not_the_shared_server(base_url: str, what: str) -> None:
    """Refuse to drive server-global state on the server every worker sees.

    Keeps the meaning the cross-worker catalog lock had — never drive a
    sync another worker can see — as an ownership rule instead of a
    lock: the global state lives on the shared server, so a test that
    touches it runs against a server of its own.
    """
    if _port_of(base_url) == SHARED_PORT:
        raise AssertionError(
            f'{what} is SERVER-GLOBAL: driven on the shared server at '
            f'{base_url} another xdist worker can change or observe it '
            'mid-run — run this test against a server it owns '
            '(tests/e2e/own_server.py)'
        )
