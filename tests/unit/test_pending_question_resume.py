"""Unit tests for Bug 2B — park WAITING when agent exits mid-question.

Scenario: the agent called `AskUserQuestion` but the subprocess
exited before the operator responded. Instead of marking the task
FAILED, we persist the question into ``wait_condition.pending_question``,
flip the task to WAITING, and wait for the operator to answer via
``POST /api/tasks/{id}/questions/answer``. When they do, the task
re-queues and the resumed agent receives the answers as the next
user turn (``claude --resume <session_id>``).
"""

import json

import pytest

from app.task_runner import (
    PromptBundle,
    format_answered_questions_prefix,
    maybe_inject_pending_answers,
    park_waiting_for_pending_question,
)
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


# ────────────────────────────────────────────────────────────
# Prefix rendering
# ────────────────────────────────────────────────────────────


class TestAnsweredQuestionsPrefix:
    def test_renders_question_answer_pairs(self):
        prefix = format_answered_questions_prefix(
            [
                {'question': 'Which site?'},
                {'question': 'Include suppressed listings?'},
            ],
            {
                'Which site?': 'SA + AE',
                'Include suppressed listings?': 'Yes',
            },
        )
        assert 'Which site? → SA + AE' in prefix
        assert 'Include suppressed listings? → Yes' in prefix
        assert 'Please continue the task' in prefix

    def test_falls_back_to_json_when_shape_wrong(self):
        # Agent SDK variant where questions list isn't dicts
        prefix = format_answered_questions_prefix(
            ['not a dict'], {'foo': 'bar'}
        )
        # Either the happy path (stringifies) or the json fallback,
        # but never raises.
        assert 'foo' in prefix or 'Please continue' in prefix


# ────────────────────────────────────────────────────────────
# park_waiting_for_pending_question
# ────────────────────────────────────────────────────────────


class _StubTask:
    """Minimal stand-in for a SQLAlchemy Task row."""

    def __init__(self, status=TaskStatus.RUNNING):
        self.status = status
        self.wait_condition = None
        self.updated_at = None


class _StubDB:
    """Collects commits; emit_bus is separate."""

    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


async def test_park_waiting_persists_pending_question(monkeypatch):
    task = _StubTask(status=TaskStatus.RUNNING)
    db = _StubDB()
    emitted = []

    async def fake_emit(event, payload):
        emitted.append((event, payload))

    monkeypatch.setattr(
        'app.task_runner.event_bus.emit',
        fake_emit,
    )

    pending = {
        'req-123': {
            'request_id': 'req-123',
            'questions': [
                {'question': 'Which site?', 'options': []},
            ],
        }
    }
    await park_waiting_for_pending_question(task, db, 'task-xyz', pending)

    assert task.status == TaskStatus.WAITING
    cond = json.loads(task.wait_condition)
    assert cond['check_strategy'] == 'manual'
    assert cond['reason'].startswith('Agent asked a question')
    assert cond['pending_question']['request_id'] == 'req-123'
    qs = cond['pending_question']['questions']
    assert qs[0]['question'] == 'Which site?'
    assert db.commits == 1
    assert emitted == [
        ('task_update', {'task_id': 'task-xyz', 'status': TaskStatus.WAITING})
    ]


# ────────────────────────────────────────────────────────────
# maybe_inject_pending_answers
# ────────────────────────────────────────────────────────────


class _DBSession:
    """Minimal async-context-manager stand-in for async_session()."""

    def __init__(self, task):
        self._task = task
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, pk):
        return self._task

    async def commit(self):
        self.commits += 1


async def test_inject_answers_prepends_prefix_and_clears_wait_condition(
    monkeypatch,
):
    task = _StubTask(status=TaskStatus.QUEUED)
    task.wait_condition = json.dumps({
        'reason': 'Agent asked a question; awaiting operator input',
        'check_strategy': 'manual',
        'waiting_since': '2026-04-20T01:05:00+00:00',
        'pending_question': {
            'request_id': 'req-123',
            'questions': [{'question': 'Which site?'}],
        },
        'answers': {'Which site?': 'SA + AE'},
        'answered_at': '2026-04-20T01:30:00+00:00',
    })
    db = _DBSession(task)

    def fake_session():
        return db

    monkeypatch.setattr('app.task_runner.async_session', fake_session)

    bundle = PromptBundle(
        prompt='Original user turn',
        system_extra='sys',
        mode='auto',
    )
    new_bundle = await maybe_inject_pending_answers('task-xyz', bundle)

    assert new_bundle is not bundle
    assert 'Which site? → SA + AE' in new_bundle.prompt
    assert new_bundle.prompt.endswith('Original user turn')
    assert new_bundle.system_extra == 'sys'
    assert new_bundle.mode == 'auto'
    # wait_condition cleared — next re-queue won't re-inject
    assert task.wait_condition is None
    assert db.commits == 1


async def test_inject_is_noop_without_answers(monkeypatch):
    task = _StubTask(status=TaskStatus.QUEUED)
    task.wait_condition = json.dumps({
        'reason': 'something else',
        'check_strategy': 'manual',
    })
    db = _DBSession(task)
    monkeypatch.setattr('app.task_runner.async_session', lambda: db)

    bundle = PromptBundle(prompt='p', system_extra='s', mode='auto')
    same = await maybe_inject_pending_answers('task-xyz', bundle)

    assert same is bundle
    # wait_condition untouched — someone else owns it
    assert task.wait_condition is not None
    assert db.commits == 0


async def test_inject_is_noop_without_wait_condition(monkeypatch):
    task = _StubTask(status=TaskStatus.QUEUED)
    task.wait_condition = None
    db = _DBSession(task)
    monkeypatch.setattr('app.task_runner.async_session', lambda: db)

    bundle = PromptBundle(prompt='p', system_extra='s', mode='auto')
    same = await maybe_inject_pending_answers('task-xyz', bundle)
    assert same is bundle
    assert db.commits == 0
