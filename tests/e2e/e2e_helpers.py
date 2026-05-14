"""
Shared helpers for E2E tests.

Consolidates duplicated login, polling, task creation, and
question-answering code that was copy-pasted across 5+ files.
"""

import logging
import os
import time

import httpx

# Single source of truth for E2E constants
BASE_URL = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7777')
POLL_INTERVAL = 3  # seconds between status polls
PIPELINE_TIMEOUT = 600  # max seconds for entire pipeline (Kimi can take 8+ min)

_logger = logging.getLogger('e2e')

# Set by conftest per xdist worker to spread tasks across providers.
DEFAULT_PROFILE_ID: str | None = None


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


def fetch_presets(client: httpx.Client) -> dict:
    """Fetch provider presets from the server."""
    resp = client.get(f'{BASE_URL}/api/profiles/presets')
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
    resp = client.post(
        f'{BASE_URL}/api/auth/login',
        json={
            'identifier': 'admin@vibe-seller.local',
            'password': 'admin',
        },
    )
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
    **kwargs,
) -> dict:
    """Create a store via API."""
    payload = {'name': name, **kwargs}
    resp = client.post(f'{BASE_URL}/api/stores', json=payload)
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
    skip_reflection: bool | None = None,
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
    if skip_reflection is not None:
        payload['skip_reflection'] = skip_reflection
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
        TimeoutError: if timeout expires without reaching target.

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
) -> dict:
    """Submit an answer for a pending agent question."""
    resp = client.post(
        f'{BASE_URL}/api/tasks/{task_id}/questions/answer',
        json={'request_id': request_id, 'answers': answers},
    )
    resp.raise_for_status()
    return resp.json()
