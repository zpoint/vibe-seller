"""Scheduled-run question e2e with a real model (C).

The schedule's prompt tells the agent to ask which shipping option to
use; the schedule is created and planned through the real path and
fired with the UI's "Run Now". The answer timeout is a DAY and nobody
answers the fire — so the run can only finish because its question is
declined, and it must finish without ever waiting, naming what it did.

How the model handles the decline is its own business (pick an option
or skip the step and list the open question); the test asserts the
contract, not the habit: completed, never WAITING, never a pending
question, and the result says what happened.
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
from tests.e2e.question_park_flow import run_decline_flow

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'live scheduled-question e2e needs the real CLI (MOCK_CLI is set)',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope='module')
def shop():
    fake = FakeSellerShipping().start()
    yield fake
    fake.stop()


@pytest.fixture(scope='module')
def decline_server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'question-decline-live',
        env={
            'MAX_AGENT_CONCURRENCY': '1',
            'VIBE_QUESTION_ANSWER_TIMEOUT_S': '86400',
            'AGENT_DEBUG': '1',
        },
        auto_answer=False,
    ) as server:
        yield server


@pytest.fixture
def api(decline_server):
    client = httpx.Client(timeout=60)
    login_client(client, decline_server.base_url)
    yield client
    client.close()


def test_scheduled_run_does_not_wait_on_question(
    decline_server, api, shop, authenticated_page
):
    assert e2e_helpers.BASE_URL == decline_server.base_url
    ev = Evidence('e2e-live-c-scheduled-decline', home=decline_server.home)
    tag = run_tag()
    store = create_store(api, f'qdecline-live-{tag}')
    prompt = (
        f'Open {shop.base_url}/orders/ORD-1001/shipping in the browser. If '
        'the page offers more than one shipping option, ask me which one '
        'with the AskUserQuestion tool before selecting anything. Then press '
        'the Select button of the chosen option and report the confirmation '
        'code shown on the next page.'
    )

    def check(result: str) -> None:
        codes = [shop.code_for('standard'), shop.code_for('express')]
        assert any(c in result for c in codes) or (
            'open question' in result.lower()
        ), result

    out = run_decline_flow(
        page=authenticated_page,
        api=api,
        base_url=decline_server.base_url,
        log_path=decline_server.log,
        ev=ev,
        store_name=store['name'],
        store_id=store['id'],
        title=f'Nightly shipping check {tag}',
        prompt=prompt,
        plan_budget_s=900,
        run_budget_s=900,
        check_result=check,
    )
    ev.write_json('access_log.json', shop.access_log)
    assert len(shop.selections()) <= 1, shop.selections()
    child = out['ids']['child']
    msgs = json.loads((ev.dir / 'messages-child.json').read_text())
    declined = [
        m
        for m in msgs
        if m['role'] == 'agent_event' and 'question_declined' in m['content']
    ]
    ev.mark('declined_events', count=len(declined))
    if declined:
        log = decline_server.log.read_text(errors='replace')
        assert (
            f'Task {child[:8]}: AskUserQuestion declined — scheduled run' in log
        )
