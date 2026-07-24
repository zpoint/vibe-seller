"""Unit tests for follow-up gate consistency:

- ``reset_task_runtime_state`` wipes ALL on-disk state so a retry is a
  true fresh start (workspace + Claude Code's per-task Task/todo store +
  project transcripts) — the fix for stale todos/refs leaking across a
  retried task via the stable CLAUDE_CODE_TASK_LIST_ID.
- ``interrupt_pending_question`` retires a pending AskUserQuestion with a
  NOTE (not the user's message, not recorded as answers) + emits
  ``task_question_interrupted`` — so a composer follow-up interrupts the
  card instead of being force-submitted as its answer.
"""

import asyncio
import json

import pytest

from app.ai.claude_backend_manager import agent_manager
from app.events.bus import event_bus
from app.workspace import manager as mgr

pytestmark = pytest.mark.unit


def test_reset_task_runtime_state_wipes_everything(tmp_path, monkeypatch):
    vibe = tmp_path / 'vibe'
    claude = tmp_path / 'claude'
    monkeypatch.setattr(mgr, 'VIBE_SELLER_DIR', vibe)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude))

    tid = 'abcd1234-0000-0000-0000-000000000000'
    ws = vibe / 'tasks' / tid
    (ws / 'uploads').mkdir(parents=True)
    (ws / 'uploads' / 'ref.jpg').write_text('img')
    store = claude / 'tasks' / f'vibe-{tid[:8]}'
    store.mkdir(parents=True)
    (store / '1.json').write_text('{"subject": "为产品图1生成白底主图"}')
    cwd_key = str(ws).replace('/', '-').replace('.', '-')
    proj = claude / 'projects' / cwd_key
    proj.mkdir(parents=True)
    (proj / 'session.jsonl').write_text('stale transcript')

    mgr.reset_task_runtime_state(tid)

    assert not ws.exists(), 'workspace (uploads etc.) must be wiped'
    assert not store.exists(), 'Claude Code Task/todo store must be wiped'
    assert not proj.exists(), 'Claude Code project transcripts must be wiped'


async def test_interrupt_pending_question_no_session_is_noop():
    assert await agent_manager.interrupt_pending_question('missing') is None


async def test_interrupt_pending_question_notes_and_emits(monkeypatch):
    class _FakeSession:
        def __init__(self):
            self.running = True
            self._pending_questions = {
                'req-1': {'request_id': 'req-1', 'questions': []}
            }
            self.submitted = None

        async def submit_answer(self, request_id, answers):
            self.submitted = (request_id, answers)

    sess = _FakeSession()
    monkeypatch.setitem(agent_manager._sessions, 'task-q', sess)
    queue = event_bus.subscribe()
    try:
        rid = await agent_manager.interrupt_pending_question('task-q')
        assert rid == 'req-1'
        # Unblocked with a free-text NOTE (a sentinel telling the agent
        # the user messaged) — NOT the user's message, NOT real answers.
        assert sess.submitted[0] == 'req-1'
        assert '_free_text' in sess.submitted[1]

        raw = await asyncio.wait_for(queue.get(), 2)
        ev = json.loads(raw)
        assert ev['type'] == 'task_question_interrupted'
        assert ev['request_id'] == 'req-1'
    finally:
        event_bus.unsubscribe(queue)
        agent_manager._sessions.pop('task-q', None)
