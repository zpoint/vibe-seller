"""E2E: a skill's prerequisites reach the agent without a refusal.

``noon-ads`` declares ``requires: [noon-shared]``; the noon login URL
lives only in ``noon-shared``. Loading ``noon-ads`` alone is allowed —
the hook attaches a hint naming the prerequisite instead of denying the
call — and the agent then loads ``noon-shared`` and answers from it.
Inventing a noon URL after skipping the prerequisite was the production
failure this protects against.

The hint is verified from the SERVER log (the hook logs ``Skill prereq:
agent <id> loaded 'noon-ads' early; hint: ...`` when it attaches one),
not from the agent's narration: an agent that happens to load
``noon-shared`` on its own would otherwise pass with the hook deleted.

Requires: real ``claude`` CLI + LLM API key + running server.
"""

import re
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
    server_log_candidates,
)

pytestmark = [pytest.mark.e2e]

# Appears only in noon-shared/SKILL.md among the loaded skills.
NOON_SHARED_MARKER = 'https://login.noon.partners/en/'


def _skill_load_order(messages: list[dict]) -> list[str]:
    """Skill names in the order the agent loaded them with the Skill tool."""
    order: list[str] = []
    for m in messages:
        if m.get('role') != 'tool_use':
            continue
        hit = re.search(r'"skill":\s*"([^"]+)"', m.get('content', ''))
        if hit and '"Skill"' in m.get('content', ''):
            order.append(hit.group(1))
    return order


def hint_owed(messages: list[dict]) -> bool:
    """Whether the agent reached for noon-ads before noon-shared.

    The hook fires on a Skill-tool load of noon-ads that noon-shared did
    not precede. An agent that never loaded noon-ads with the Skill tool
    (observed: it read noon-shared's SKILL.md with sed and answered from
    it) gave the hook nothing to hint on — padding the missing names in
    a fixed order once read that as "noon-ads first".
    """
    order = _skill_load_order(messages)
    if 'noon-ads' not in order:
        return False
    return 'noon-shared' not in order or order.index('noon-ads') < (
        order.index('noon-shared')
    )


def _hint_logged(task_id_prefix: str) -> bool:
    """Whether the hook logged a prerequisite hint for this task."""
    pat = re.compile(
        rf'Skill prereq: agent {re.escape(task_id_prefix)} '
        r"loaded 'noon-ads' early; hint: .*noon-shared"
    )
    for path in server_log_candidates():
        if path.is_file() and pat.search(
            path.read_text(encoding='utf-8', errors='replace')
        ):
            return True
    return False


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
    # When the agent reached for noon-ads first — the production failure
    # — the hook must have hinted. Loading noon-shared first needs no
    # hint (the hook itself is pinned by tests/unit/test_skill_prereq_hint).
    if hint_owed(messages):
        assert _hint_logged(task['id'][:8]), (
            'noon-ads was loaded before noon-shared but no prerequisite '
            'hint was logged. Logs searched: '
            f'{[str(p) for p in server_log_candidates()]}'
        )
