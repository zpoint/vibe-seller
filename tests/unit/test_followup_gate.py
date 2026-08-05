"""Unit tests for follow-up gate consistency:

- ``reset_task_runtime_state`` wipes on-disk RUN RESIDUE so a retry is a
  true fresh start (workspace + Claude Code's per-task Task/todo store +
  project transcripts) — the fix for stale todos/refs leaking across a
  retried task via the stable CLAUDE_CODE_TASK_LIST_ID — while keeping
  ``uploads/``, which is the user's own input rather than residue.
- ``interrupt_pending_question`` retires a pending AskUserQuestion with a
  NOTE (not the user's message, not recorded as answers) + emits
  ``task_question_interrupted`` — so a composer follow-up interrupts the
  card instead of being force-submitted as its answer.
- ``session_has_orphaned_bg_task`` is the RUNAWAY backstop: the turn now
  waits for a background shell to finish, but one that never completes is
  force-closed + orphaned; the follow-up then spawns a FRESH session
  instead of ``--resume`` (a resume would inject a reconciliation
  notification that collides with the follow-up and aborts the turn).
"""

import asyncio
import json

import pytest

from app.ai.claude_backend_manager import agent_manager
from app.events.bus import event_bus
from app.workspace import manager as mgr

pytestmark = pytest.mark.unit


def test_reset_task_runtime_state_wipes_residue_keeps_uploads(
    tmp_path, monkeypatch
):
    """Residue goes; the user's attachments stay.

    This used to rmtree the whole workspace. ``uploads/`` is not run
    residue — it holds files the USER attached, the retry keeps their
    ``task_attachments`` rows, nothing re-uploads them, and every run's
    prompt is built by reading them off disk. Wiping them turned a retry
    of an image task back into "where is the image?" with the surviving
    DB rows pointing at deleted files.
    """
    vibe = tmp_path / 'vibe'
    claude = tmp_path / 'claude'
    monkeypatch.setattr(mgr, 'VIBE_SELLER_DIR', vibe)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude))

    tid = 'abcd1234-0000-0000-0000-000000000000'
    ws = vibe / 'tasks' / tid
    (ws / 'uploads').mkdir(parents=True)
    (ws / 'uploads' / 'ref.jpg').write_text('img')
    (ws / 'reviews').mkdir(parents=True)
    (ws / 'reviews' / 'stale.json').write_text('{}')
    (ws / 'notes.md').write_text('prior run scratch')
    store = claude / 'tasks' / f'vibe-{tid[:8]}'
    store.mkdir(parents=True)
    (store / '1.json').write_text('{"subject": "为产品图1生成白底主图"}')
    cwd_key = str(ws).replace('/', '-').replace('.', '-')
    proj = claude / 'projects' / cwd_key
    proj.mkdir(parents=True)
    (proj / 'session.jsonl').write_text('stale transcript')

    mgr.reset_task_runtime_state(tid)

    assert not (ws / 'reviews').exists(), 'run residue must be wiped'
    assert not (ws / 'notes.md').exists(), 'run residue must be wiped'
    assert (ws / 'uploads' / 'ref.jpg').read_text() == 'img', (
        'user-supplied attachments must survive a retry'
    )
    assert not store.exists(), 'Claude Code Task/todo store must be wiped'
    assert not proj.exists(), 'Claude Code project transcripts must be wiped'


def _write_transcript(
    tmp_path, monkeypatch, task_id: str, session_id: str, body: str
) -> None:
    """Write a fake Claude Code transcript where the detector reads it."""
    vibe = tmp_path / 'vibe'
    claude = tmp_path / 'claude'
    monkeypatch.setattr(mgr, 'VIBE_SELLER_DIR', vibe)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude))
    ws = vibe / 'tasks' / task_id
    cwd_key = str(ws).replace('/', '-').replace('.', '-')
    proj = claude / 'projects' / cwd_key
    proj.mkdir(parents=True)
    (proj / f'{session_id}.jsonl').write_text(body, encoding='utf-8')


_BG_START = (
    '{"type":"user","message":{"content":[{"type":"tool_result",'
    '"content":"Command running in background with ID: bgtask-x1"}]},'
    '"tool_use_result":{"backgroundTaskId":"bgtask-x1"}}\n'
)
_BG_DONE = (
    '{"type":"user","message":{"content":[{"type":"text","text":'
    '"<task-notification>\\n<task-id>bgtask-x1</task-id>\\n'
    '<status>completed</status>\\n</task-notification>"}]}}\n'
)


def test_orphan_bg_task_no_session_is_false():
    assert mgr.session_has_orphaned_bg_task('t', None) is False


def test_orphan_bg_task_missing_transcript_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, 'VIBE_SELLER_DIR', tmp_path / 'vibe')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    assert mgr.session_has_orphaned_bg_task('t', 'no-such-session') is False


def test_orphan_bg_task_started_without_completion_is_true(
    tmp_path, monkeypatch
):
    _write_transcript(tmp_path, monkeypatch, 't', 's1', _BG_START)
    assert mgr.session_has_orphaned_bg_task('t', 's1') is True


def test_orphan_bg_task_completed_is_false(tmp_path, monkeypatch):
    _write_transcript(tmp_path, monkeypatch, 't', 's1', _BG_START + _BG_DONE)
    assert mgr.session_has_orphaned_bg_task('t', 's1') is False


def test_orphan_bg_task_none_started_is_false(tmp_path, monkeypatch):
    body = '{"type":"assistant","message":{"content":"hi"}}\n'
    _write_transcript(tmp_path, monkeypatch, 't', 's1', body)
    assert mgr.session_has_orphaned_bg_task('t', 's1') is False


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
