"""Question-park e2e against the mock CLI — deterministic, no credits.

B: an unanswered question parks its task in WAITING after the timeout
and frees the ONE agent slot for the task queued behind it; answering
in the UI resumes it to COMPLETED.

The server is owned by this module (dedicated port and home) because
it needs one agent slot, a 30 s question timeout and nobody answering
questions automatically. The mock CLI asks, waits with no deadline of
its own, and reports the answer it was resumed with.
"""

import logging
import os

import httpx
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import create_store, login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack
from tests.e2e.question_park_flow import run_park_flow

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the question-park mock e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

TIMEOUT_S = 30


@pytest.fixture(scope='module')
def park_server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'question-park-mock',
        env={
            'MOCK_CLI': MOCK_CLI,
            'MAX_AGENT_CONCURRENCY': '1',
            'VIBE_QUESTION_ANSWER_TIMEOUT_S': str(TIMEOUT_S),
            'AGENT_DEBUG': '1',
        },
        auto_answer=False,
    ) as server:
        yield server


@pytest.fixture
def api(park_server):
    client = httpx.Client(timeout=30)
    login_client(client, park_server.base_url)
    yield client
    client.close()


def test_unanswered_question_parks_then_resumes_on_answer(
    park_server, api, authenticated_page
):
    assert e2e_helpers.BASE_URL == park_server.base_url
    ev = Evidence('e2e-mock-b-park-resume', home=park_server.home)
    tag = run_tag()
    store = create_store(api, f'qpark-{tag}')
    result = run_park_flow(
        page=authenticated_page,
        api=api,
        base_url=park_server.base_url,
        home=park_server.home,
        log_path=park_server.log,
        ev=ev,
        store_name=store['name'],
        store_id=store['id'],
        a_title=f'[[MOCK_ASK_PARK]] ship ORD-1001 {tag}',
        a_description=None,
        b_title=f'Status check {tag}',
        b_description=None,
        timeout_s=TIMEOUT_S,
        b_budget_s=60,
        a_budget_s=60,
        check_a_result=lambda r: _assert_in('Chosen option: Express', r),
        check_b_result=lambda r: _assert_in('completed', r),
    )
    log = park_server.log.read_text(errors='replace')
    a_id = result['ids']['A']
    assert f'Task {a_id[:8]}: AskUserQuestion park-req-001 unanswered' in log
    assert f'Task {a_id[:8]} parked in WAITING on question park-req-001' in log
    assert f'Task {a_id[:8]} resuming after answer to park-req-001' in log


def _assert_in(needle: str, text: str) -> None:
    assert needle in text, f'{needle!r} not in {text!r}'
