"""A parent that creates a sub-task is told to end its turn.

The parent completes by itself with its sub-tasks' results once its
turn ends. A parent that instead polls and sleeps in its session holds
a run slot for as long as its sub-tasks take — observed in CI, where an
orchestrator looped on sleep + list_tasks for ten minutes.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_server import SUBTASK_NEXT, handle_tool_call

pytestmark = pytest.mark.unit


async def _create(args: dict, api_result: dict) -> dict:
    with patch(
        'app.mcp_server.call_api',
        new_callable=AsyncMock,
        return_value=api_result,
    ):
        return json.loads(
            await handle_tool_call('vibe_seller_create_task', args)
        )


async def test_a_created_subtask_says_end_your_turn():
    got = await _create(
        {'title': 'store-a work', 'store_id': 's-a', 'parent_task_id': 'p-1'},
        {'id': 'c-1', 'status': 'running'},
    )
    assert got['id'] == 'c-1'
    assert got['next'] == SUBTASK_NEXT
    assert 'end your turn' in SUBTASK_NEXT


async def test_a_top_level_task_has_no_subtask_note():
    got = await _create(
        {'title': 'store-a work', 'store_id': 's-a'},
        {'id': 't-1', 'status': 'running'},
    )
    assert 'next' not in got


async def test_a_refused_subtask_has_no_note():
    # The refusal is the whole answer — nothing was created.
    got = await _create(
        {'title': 'nested', 'store_id': 's-a', 'parent_task_id': 'c-1'},
        {'detail': 'a sub-task cannot create sub-tasks'},
    )
    assert 'next' not in got
