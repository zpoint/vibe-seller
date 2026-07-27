"""A refused submission survives its refusal.

Reviewer gates refuse by raising, and the raise used to happen BEFORE
``task.result`` was assigned — so a refusal destroyed the submission.
Two live runs submitted 41 and 14 times, every one refused, and both
ended holding nothing; the end-of-turn fallback then filed the agent's
own chat narration as the deliverable and the task shipped FAILED over
a transcript.

Submission and verdict are separate facts. What the agent submitted is
recorded unconditionally; the refusal only annotates it. See
``app/task_outcome.py`` for the precedence that consumes them.
"""

import asyncio

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

# Trips ``markdown_format`` — a body row with the wrong column count.
# SOFT_GATE_MAX_DENIALS is 1, so the FIRST submit is refused.
MALFORMED = (
    'Audit report, 95% complete.\n\n'
    '| Campaign | Spend | ROAS |\n'
    '|---|---|---|\n'
    '| widget-006 | 10 |\n'
)
CLEAN = (
    'Audit report, complete.\n\n'
    '| Campaign | Spend | ROAS |\n'
    '|---|---|---|\n'
    '| widget-006 | 10 | 4.2 |\n'
)


async def _running_task(admin_client, install_fake_agent, name):
    """A task whose fake agent stays alive, so MCP endpoints see RUNNING."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='streamed narration from the agent',
        gate=gate,
    )
    r = await admin_client.post('/api/stores', json={'name': name})
    store_id = r.json()['id']
    r = await admin_client.post(
        '/api/tasks', json={'title': 'retention', 'store_id': store_id}
    )
    task_id = r.json()['id']
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    assert data['status'] == 'running'
    return task_id, gate


class TestRefusalKeepsTheSubmission:
    async def test_refused_submission_is_retained(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Retain Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': MALFORMED}
        )
        assert r.status_code == 400

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        # The refusal stands — no accepted deliverable…
        assert not data.get('result')
        # …but what the agent submitted is still here.
        assert data['submitted_result'] == MALFORMED
        assert data['submission_count'] == 1
        assert data['review_gaps']

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_run_ending_on_refusals_ships_the_report_not_narration(
        self, admin_client, install_fake_agent
    ):
        """THE REGRESSION, end to end.

        Submission refused, agent reaches for the error channel as its
        exit, turn ends. The run must complete carrying the retained
        report plus an honest gap list — not FAILED showing the agent's
        chat narration.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Regression Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': MALFORMED}
        )
        assert r.status_code == 400

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            json={'error': 'reviewer kept refusing the format'},
        )
        assert r2.status_code == 200

        gate.set()
        final = await wait_for_task(admin_client, task_id)

        assert final['status'] == 'completed'
        assert not final.get('error')
        assert '95% complete' in final['result']
        # The narration must NOT be the deliverable.
        assert 'streamed narration' not in final['result']
        # Both the gate's gap and the agent's own account ride along.
        assert 'reviewer kept refusing the format' in final['result']

    async def test_accepted_submission_clears_the_gaps(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Accept Store'
        )
        assert (
            await admin_client.post(
                f'/api/tasks/{task_id}/result', json={'result': MALFORMED}
            )
        ).status_code == 400

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': CLEAN}
        )
        assert r.status_code == 200

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == CLEAN
        assert data['submitted_result'] == CLEAN
        assert data['submission_count'] == 2
        assert not data['review_gaps']

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert final['status'] == 'completed'
        assert final['result'] == CLEAN


class TestIncompleteExit:
    async def test_declared_incomplete_rides_along_as_caveats(
        self, admin_client, install_fake_agent
    ):
        """The honest exit: a deliverable plus what's missing.

        Reaching for ``set_task_error`` to explain caveats over real
        output is what the error channel was being abused for; this is
        the door that makes that unnecessary.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Incomplete Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={
                'result': CLEAN,
                'incomplete': ['noon AE search-term layer not collected'],
            },
        )
        assert r.status_code == 200

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        # Declaring incompleteness is NOT a failure.
        assert final['status'] == 'completed'
        assert not final.get('error')
        assert 'noon AE search-term layer' in final['result']

    async def test_incomplete_does_not_bypass_a_gate(
        self, admin_client, install_fake_agent
    ):
        """It is an honest exit, not a skeleton key."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'No Bypass Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': MALFORMED, 'incomplete': ['let me out']},
        )
        assert r.status_code == 400

        gate.set()
        await wait_for_task(admin_client, task_id)
