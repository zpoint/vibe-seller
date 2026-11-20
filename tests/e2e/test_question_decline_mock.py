"""Scheduled-run question e2e against the mock CLI (C).

A scheduled run nobody is watching must never wait on a question. The
schedule is created and its plan approved through the real path, then
fired with the UI's "Run Now"; the mock asks in its execute turn. The
server's answer timeout is a DAY and nobody answers — so only the
decline can let the run finish, and it must finish at once, with the
mock's own choice and its open question in the result.
"""

import os

import httpx
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import create_store, login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack
from tests.e2e.question_park_flow import run_decline_flow

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the scheduled-question mock e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope='module')
def decline_server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'question-decline-mock',
        env={
            'MOCK_CLI': MOCK_CLI,
            'MAX_AGENT_CONCURRENCY': '1',
            'VIBE_QUESTION_ANSWER_TIMEOUT_S': '86400',
            'AGENT_DEBUG': '1',
        },
        auto_answer=False,
    ) as server:
        yield server


@pytest.fixture
def api(decline_server):
    client = httpx.Client(timeout=30)
    login_client(client, decline_server.base_url)
    yield client
    client.close()


def test_scheduled_run_does_not_wait_on_question(
    decline_server, api, authenticated_page
):
    assert e2e_helpers.BASE_URL == decline_server.base_url
    ev = Evidence('e2e-mock-c-scheduled-decline', home=decline_server.home)
    tag = run_tag()
    store = create_store(api, f'qdecline-{tag}')

    def check(result: str) -> None:
        assert 'Question declined (Nobody is watching this run' in result
        assert 'Chose Standard myself' in result
        assert 'Open questions' in result

    out = run_decline_flow(
        page=authenticated_page,
        api=api,
        base_url=decline_server.base_url,
        log_path=decline_server.log,
        ev=ev,
        store_name=store['name'],
        store_id=store['id'],
        title=f'Nightly shipping check {tag}',
        # The execute turn's prompt is the schedule's description.
        prompt=(
            '[[MOCK_ASK_ON_EXECUTE]] Ship ORD-1001. If more than one '
            'option, ask me which one.'
        ),
        plan_budget_s=60,
        run_budget_s=60,
        check_result=check,
    )
    child = out['ids']['child']
    log = decline_server.log.read_text(errors='replace')
    assert f'Task {child[:8]}: AskUserQuestion declined — scheduled run' in log
