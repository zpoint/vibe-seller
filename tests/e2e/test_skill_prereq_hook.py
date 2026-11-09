"""E2E: a skill's prerequisites reach the agent without a refusal.

``noon-ads`` declares ``requires: [noon-shared]``; the noon login URL
lives only in ``noon-shared``. Loading ``noon-ads`` alone is allowed —
the hook attaches a hint naming the prerequisite instead of denying the
call — and the agent then loads ``noon-shared`` and answers from it.
Inventing a noon URL after skipping the prerequisite was the production
failure this protects against.

Requires: real ``claude`` CLI + LLM API key + running server.
"""

import time

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    get_messages,
    login,
    poll_task_status,
)

pytestmark = [pytest.mark.e2e]

# Appears only in noon-shared/SKILL.md among the loaded skills.
NOON_SHARED_MARKER = 'https://login.noon.partners/en/'


@pytest.fixture(scope='module')
def api_client():
    client = httpx.Client(timeout=30)
    login(client)
    yield client
    client.close()


def test_prerequisite_is_loaded_and_used(api_client: httpx.Client):
    store = create_store(
        api_client, f'prereq-hint-{int(time.time())}', browser_backend='chrome'
    )
    task = create_task(
        api_client,
        title='Where do I sign in to run our noon ads?',
        store_id=store['id'],
        description=(
            'Just tell me the sign-in page address for running our noon '
            'ad campaigns. No need to open anything.'
        ),
    )
    result = poll_task_status(
        api_client,
        task['id'],
        target_statuses={'completed', 'failed'},
        timeout=PIPELINE_TIMEOUT,
    )
    messages = get_messages(api_client, task['id'])
    transcript = ' '.join(m.get('content', '') for m in messages)
    assert 'noon-shared' in transcript, (
        'the agent never loaded noon-shared; transcript starts: '
        + transcript[:800]
    )
    answer = transcript + ' ' + (result.get('result') or '')
    assert NOON_SHARED_MARKER in answer, answer[:800]
