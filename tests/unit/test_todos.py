"""The todo list is checked where it enters, and read through one parser.

In CI a model sent ``TodoWrite`` with ``{"todos": {"item": {...}}}``.
The CLI rejected the call, but the server stored the dict, and finalize
iterated its keys and failed the task with ``'str' object has no
attribute 'get'``."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.task_runner import has_incomplete_todos
from app.todos import parse_stored, valid_todos

pytestmark = pytest.mark.unit

# The exact malformed input from CI.
CI_TODOS = {
    'item': {
        'active_content': 'Reflecting on task learnings',
        'status': 'in_progress',
    }
}

GOOD = [
    {'content': 'Read the page', 'status': 'completed'},
    {'content': 'Write the report', 'status': 'in_progress'},
]


@pytest.mark.parametrize(
    'value',
    [
        CI_TODOS,
        'not a list',
        ['a string item'],
        [{'status': 'pending'}],  # no content
        [{'content': 'x', 'status': 'done'}],  # unknown status
        None,
    ],
)
def test_malformed_lists_are_not_todo_lists(value):
    assert valid_todos(value) is None


def test_a_well_formed_list_passes():
    assert valid_todos(GOOD) == GOOD
    assert valid_todos([]) == []


def test_a_malformed_stored_row_does_not_crash_finalize():
    # A row written before the shape check: treated as incomplete (the
    # existing unparseable-row rule), never an AttributeError.
    task = SimpleNamespace(id='t1', todos=json.dumps(CI_TODOS))
    assert has_incomplete_todos(task) is True


def test_stored_lists_are_read_by_status():
    done = [{'content': 'x', 'status': 'completed'}]
    assert has_incomplete_todos(SimpleNamespace(id='t', todos='')) is False
    assert (
        has_incomplete_todos(SimpleNamespace(id='t', todos=json.dumps(done)))
        is False
    )
    assert (
        has_incomplete_todos(SimpleNamespace(id='t', todos=json.dumps(GOOD)))
        is True
    )
    assert parse_stored(json.dumps(GOOD)) == GOOD


def _todo_write(todos) -> dict:
    return {
        'type': 'assistant',
        'parent_tool_use_id': None,
        'message': {
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_todo',
                    'name': 'TodoWrite',
                    'input': {'todos': todos},
                }
            ]
        },
    }


@pytest.mark.parametrize('todos, stored', [(CI_TODOS, False), (GOOD, True)])
async def test_the_stream_stores_only_a_well_formed_list(todos, stored):
    session = AgentSession(task_id='test-task', prompt='t', mode='execute')
    persist = AsyncMock()
    with (
        patch.object(session, '_emit_message', AsyncMock()),
        patch.object(session, '_persist_todos', persist),
    ):
        await session._handle_event(_todo_write(todos))
    if stored:
        persist.assert_awaited_once_with(todos)
    else:
        persist.assert_not_awaited()
