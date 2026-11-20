"""Question-park e2e with a real model and a real CLI.

The same UI journey as ``test_question_park_mock`` — but the agent is
real: it opens a fake back-office page that offers two shipping
options, is told to ask which one, and nobody answers for the 30 s
timeout. The CLI is really stopped mid-question, the slot goes to the
task queued behind it, and answering in the UI resumes the SAME
session (``--resume``), which must still know where it was: it selects
the answered option on the page and reports that option's
confirmation code. The fake page's access log is the ground truth.
"""

import json
import os

import httpx
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import create_store, login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.fake_seller_shipping import FakeSellerShipping
from tests.e2e.own_server import owned_agent_stack
from tests.e2e.question_park_flow import run_park_flow

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'live question-park e2e needs the real CLI (MOCK_CLI is set)',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

TIMEOUT_S = 30
BUDGET_S = 900


@pytest.fixture(scope='module')
def shop():
    fake = FakeSellerShipping().start()
    yield fake
    fake.stop()


@pytest.fixture(scope='module')
def park_server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'question-park-live',
        env={
            'MAX_AGENT_CONCURRENCY': '1',
            'VIBE_QUESTION_ANSWER_TIMEOUT_S': str(TIMEOUT_S),
            'AGENT_DEBUG': '1',
        },
        auto_answer=False,
    ) as server:
        yield server


@pytest.fixture
def api(park_server):
    client = httpx.Client(timeout=60)
    login_client(client, park_server.base_url)
    yield client
    client.close()


def test_unanswered_question_parks_then_resumes_on_answer(
    park_server, api, shop, authenticated_page
):
    assert e2e_helpers.BASE_URL == park_server.base_url
    ev = Evidence('e2e-live-b-park-resume', home=park_server.home)
    tag = run_tag()
    store = create_store(api, f'qpark-live-{tag}')
    a_prompt = (
        f'Open {shop.base_url}/orders/ORD-1001/shipping in the browser. '
        'The page offers more than one shipping option. You MUST ask me '
        'which one with the AskUserQuestion tool (one question, one option '
        'per shipping option, labelled exactly as on the page) and wait '
        'for my answer — never choose yourself. After I answer, press the '
        'Select button of the option I chose and report the confirmation '
        'code the next page shows.'
    )
    b_prompt = (
        f'Open {shop.base_url}/status and report the exact status code '
        'text shown on the page. Do not ask me anything.'
    )
    selections_before_answer: list = []

    def check_a(result: str) -> None:
        assert shop.code_for('express') in result, result

    def check_b(result: str) -> None:
        assert shop.status_code in result, result
        # Nothing was selected while A waited for its answer.
        selections_before_answer.extend(shop.selections())
        assert selections_before_answer == [], selections_before_answer

    out = run_park_flow(
        page=authenticated_page,
        api=api,
        base_url=park_server.base_url,
        home=park_server.home,
        log_path=park_server.log,
        ev=ev,
        store_name=store['name'],
        store_id=store['id'],
        a_title=f'Ship order ORD-1001 {tag}',
        a_description=a_prompt,
        b_title=f'Read status {tag}',
        b_description=b_prompt,
        timeout_s=TIMEOUT_S,
        b_budget_s=BUDGET_S,
        a_budget_s=BUDGET_S,
        check_a_result=check_a,
        check_b_result=check_b,
    )
    ev.write_json('access_log.json', shop.access_log)
    picks = shop.selections()
    assert [p['body'] for p in picks] == ['option=express'], picks
    answered_at = next(
        json.loads(line)['at']
        for line in (ev.dir / 'timeline.jsonl').read_text().splitlines()
        if json.loads(line).get('event') == 'answered'
    )
    assert picks[0]['at'] > answered_at
    a_id = out['ids']['A']
    row = ev.task_row(a_id, 'A-final')
    parked_row = json.loads((ev.dir / 'task-A-03-parked.json').read_text())
    # Resumed, not restarted: the same CLI session carried on.
    assert row['session_id'] and row['session_id'] == parked_row['session_id']
    log = park_server.log.read_text(errors='replace')
    assert f'Task {a_id[:8]} parked in WAITING on question' in log
    assert f'Task {a_id[:8]} resuming after answer to' in log
