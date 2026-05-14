"""Workflow tests for conversation stream improvements.

Covers: tool_use and thinking message persistence, delta
filtering, replan flow, and plan-skip.
"""

import pytest

from app.models.task_message import TaskMessage
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class TestToolCallMessages:
    async def test_tool_calls_appear_in_messages(
        self, admin_client, install_fake_agent
    ):
        """Tool calls persisted by agent appear in /messages."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            tool_calls=[
                {'tool': 'Read', 'input': {'file_path': 'app/models.py'}},
                {'tool': 'Grep', 'input': {'pattern': 'class Task'}},
            ],
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'Tool call test'}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        tool_msgs = [m for m in msgs if m['role'] == 'tool_use']
        assert len(tool_msgs) == 2
        assert 'Read' in tool_msgs[0]['content']
        assert 'Grep' in tool_msgs[1]['content']

    async def test_thinking_appears_in_messages(
        self, admin_client, install_fake_agent
    ):
        """Thinking blocks persisted by agent appear in /messages."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            thinking_text='Analyzing the codebase...',
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'Thinking test'}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        think_msgs = [m for m in msgs if m['role'] == 'thinking']
        assert len(think_msgs) == 1
        assert 'Analyzing' in think_msgs[0]['content']


class TestDeltaFiltering:
    async def test_delta_excluded_from_messages_api(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
    ):
        """Delta rows inserted in DB are excluded from /messages."""
        r = await admin_client.post(
            '/api/tasks', json={'title': 'Delta filter test'}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Insert delta rows directly (simulates old behavior where
        # _emit_message persisted every streaming chunk)
        async with override_async_session() as db:
            for seq_offset, role in enumerate(('delta', 'thinking_delta')):
                db.add(
                    TaskMessage(
                        task_id=task_id,
                        role=role,
                        content='ephemeral chunk',
                        seq=9000 + seq_offset,
                    )
                )
            await db.commit()

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        delta_msgs = [
            m for m in msgs if m['role'] in ('delta', 'thinking_delta')
        ]
        assert len(delta_msgs) == 0


class TestReplanFlow:
    async def test_replan_with_tool_calls_completes(
        self, admin_client, install_fake_agent
    ):
        """Full replan cycle with tool calls doesn't hang."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step one',
            result='Done after replan',
            tool_calls=[
                {'tool': 'Read', 'input': {'file_path': 'x.py'}},
            ],
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Replan flow', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for plan
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'

        # Send feedback
        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Add step 3'},
        )

        # Wait for revised plan
        data2 = await wait_for_task(admin_client, task_id, target='planned')
        assert 'revised' in data2['plan']

        # Approve
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')

        # Wait for completion
        data3 = await wait_for_task(admin_client, task_id)
        assert data3['status'] == 'completed'

        # Verify tool calls in messages
        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        tool_msgs = [m for m in msgs if m['role'] == 'tool_use']
        assert len(tool_msgs) >= 1


class TestPlanSkip:
    async def test_plan_skip_completes_directly(
        self, admin_client, install_fake_agent
    ):
        """Agent skips plan → task completes without plan."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            skip_plan=True,
            result='Quick done',
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'Skip plan test'}
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] is None
        assert data['result'] == 'Quick done'


class TestMessageOrdering:
    async def test_messages_ordered_with_tool_calls(
        self, admin_client, install_fake_agent
    ):
        """Messages are chronologically ordered including tool calls."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            thinking_text='Thinking...',
            tool_calls=[
                {'tool': 'Read', 'input': {'file_path': 'a.py'}},
            ],
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Order test', 'plan_mode': True},
        )
        task_id = r.json()['id']
        # plan_mode=True → pauses at PLANNED (assertions only
        # need messages up through the plan, not execution).
        await wait_for_task(admin_client, task_id, target='planned')

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        roles = [m['role'] for m in msgs]
        # thinking and tool_use should come before plan/result
        think_idx = roles.index('thinking')
        tool_idx = roles.index('tool_use')
        plan_idx = roles.index('plan')
        assert think_idx < plan_idx
        assert tool_idx < plan_idx
        # created_at should be non-decreasing
        times = [m['created_at'] for m in msgs]
        assert times == sorted(times)


class TestMultipleResults:
    """Multi-turn tasks should only produce one result card.

    Inspired by real-world scenario: agent scrapes docs, launches
    background tasks; each completion notification triggers a new
    turn whose result is a short acknowledgment.  Only the first
    substantive result should appear as role='result'.
    """

    @staticmethod
    async def _create_store(client):
        r = await client.post('/api/stores', json={'name': 'Dedup Test Store'})
        return r.json()['id']

    async def test_extra_results_stored_as_assistant(
        self, admin_client, install_fake_agent
    ):
        """Extra results after the first are persisted as assistant."""
        store_id = await self._create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            result=(
                'Task Complete\nScraped 32 markdown files from SA help docs.'
            ),
            extra_results=[
                'Background task done. No action needed.',
                'Already complete with all files.',
            ],
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Multi-result scraper',
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        result_msgs = [m for m in msgs if m['role'] == 'result']
        asst_msgs = [
            m
            for m in msgs
            if m['role'] == 'assistant'
            and (
                'Background task' in m['content']
                or 'Already complete' in m['content']
            )
        ]
        assert len(result_msgs) == 1
        assert 'Scraped 32' in result_msgs[0]['content']
        assert len(asst_msgs) == 2

    async def test_single_result_unchanged(
        self, admin_client, install_fake_agent
    ):
        """Tasks with one result still work normally."""
        store_id = await self._create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Done.',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Single result',
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        result_msgs = [m for m in msgs if m['role'] == 'result']
        assert len(result_msgs) == 1
        assert result_msgs[0]['content'] == 'Done.'

    async def test_plan_mode_extra_results(
        self, admin_client, install_fake_agent
    ):
        """Plan-mode task with extra results: one result card."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Scrape docs',
            result='Scraping completed successfully.',
            extra_results=['Confirmed. All files saved.'],
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Plan + multi-result', 'plan_mode': True},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='planned')

        # Approve and execute
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r2.json()
        result_msgs = [m for m in msgs if m['role'] == 'result']
        assert len(result_msgs) == 1
        assert 'Scraping completed' in result_msgs[0]['content']
