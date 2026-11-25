"""Switching Auto/Review while the agent runs: the run follows the switch.

The mode a session was launched in is not the mode the user sees once
they switch it, and every switch must still leave the user a way on:
Auto runs the plan without a Confirm, Review shows a plan to confirm.
"""

import asyncio

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _start_held(
    client, fake, *, plan_mode: bool
) -> tuple[str, asyncio.Event]:
    gate = asyncio.Event()
    fake.default_scenario = FakeAgentScenario(
        plan='## Plan\n1. Do stuff', result='Done!', gate=gate
    )
    r = await client.post(
        '/api/tasks', json={'title': 'Switch me', 'plan_mode': plan_mode}
    )
    assert r.status_code == 200
    task_id = r.json()['id']
    await wait_for_task(client, task_id, target=('designing', 'running'))
    return task_id, gate


async def _switch(client, task_id: str, plan_mode: bool) -> None:
    r = await client.patch(
        f'/api/tasks/{task_id}/review-plan', json={'plan_mode': plan_mode}
    )
    assert r.status_code == 200


class TestSwitchWhileRunning:
    async def test_review_switched_to_auto_while_planning_runs_unconfirmed(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _start_held(
            admin_client, install_fake_agent, plan_mode=True
        )
        await _switch(admin_client, task_id, False)
        gate.set()

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Done!'
        assert data['plan'] is not None

    async def test_switched_back_to_review_waits_and_confirm_finishes(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _start_held(
            admin_client, install_fake_agent, plan_mode=True
        )
        await _switch(admin_client, task_id, False)
        await _switch(admin_client, task_id, True)
        gate.set()

        await wait_for_task(admin_client, task_id, target='planned')
        r = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        assert r.status_code == 200
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Done!'

    async def test_auto_switched_to_review_mid_run_still_finishes(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _start_held(
            admin_client, install_fake_agent, plan_mode=False
        )
        await _switch(admin_client, task_id, True)
        gate.set()

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Done!'
