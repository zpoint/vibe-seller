"""Unit tests for app.ai.compaction — history file dump and prompt building."""

import json

import pytest

from app.ai.compaction import (
    RECENT_COUNT,
    build_history_prompt,
    dump_history_file,
)

pytestmark = [pytest.mark.unit]


# ── Fixtures ─────────────────────────────────────────


@pytest.fixture
def history_dir(tmp_path, monkeypatch):
    """Redirect HISTORY_DIR to a temp directory."""
    d = tmp_path / 'task_history'
    monkeypatch.setattr('app.ai.compaction.HISTORY_DIR', d)
    return d


def _make_messages(count: int) -> list[dict]:
    """Create N alternating user/assistant messages."""
    msgs = []
    for i in range(count):
        role = 'user' if i % 2 == 0 else 'assistant'
        msgs.append({
            'role': role,
            'content': f'Message {i}',
            'seq': i,
        })
    return msgs


# ── dump_history_file ────────────────────────────────


class TestDumpHistoryFile:
    def test_writes_valid_json(self, history_dir):
        msgs = _make_messages(3)
        path = dump_history_file('task-1', msgs)

        assert path is not None
        assert path.exists()

        data = json.loads(path.read_text(encoding='utf-8'))
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]['role'] == 'user'
        assert data[0]['content'] == 'Message 0'
        assert data[0]['seq'] == 0

    def test_empty_messages_returns_none(self, history_dir):
        assert dump_history_file('task-2', []) is None

    def test_creates_directory_if_missing(self, history_dir):
        assert not history_dir.exists()
        path = dump_history_file('task-3', _make_messages(1))
        assert path is not None
        assert history_dir.exists()

    def test_overwrites_existing_file(self, history_dir):
        dump_history_file('task-4', _make_messages(2))
        path = dump_history_file('task-4', _make_messages(5))

        data = json.loads(path.read_text(encoding='utf-8'))
        assert len(data) == 5

    def test_correct_file_path(self, history_dir):
        path = dump_history_file('abc-123', _make_messages(1))
        assert path == history_dir / 'abc-123.json'


# ── build_history_prompt ─────────────────────────────


class TestBuildHistoryPrompt:
    def test_empty_messages_returns_empty(self):
        assert build_history_prompt([], None) == ''

    def test_includes_mandatory_file_reference(self, tmp_path):
        msgs = _make_messages(3)
        f = tmp_path / 'history.json'
        f.write_text('[]')

        result = build_history_prompt(msgs, f)
        assert 'MUST read this file' in result
        assert str(f) in result
        assert '3 messages' in result

    def test_includes_only_last_n_messages(self, tmp_path):
        msgs = _make_messages(10)
        f = tmp_path / 'history.json'
        f.write_text('[]')

        result = build_history_prompt(msgs, f, recent_count=5)

        # Last 5 messages (seq 5-9) should be present
        assert '[user]: Message 8' in result
        assert '[assistant]: Message 9' in result
        # First messages should NOT be inline
        assert '[user]: Message 0' not in result
        assert '[assistant]: Message 1' not in result

    def test_fewer_than_recent_count_includes_all(self):
        msgs = _make_messages(3)
        result = build_history_prompt(msgs, None)

        assert '[user]: Message 0' in result
        assert '[assistant]: Message 1' in result
        assert '[user]: Message 2' in result

    def test_no_history_file_skips_reference(self):
        msgs = _make_messages(2)
        result = build_history_prompt(msgs, None)

        assert 'MUST read' not in result
        assert '[user]: Message 0' in result

    def test_shows_count_when_truncated(self, tmp_path):
        msgs = _make_messages(10)
        f = tmp_path / 'history.json'
        f.write_text('[]')

        result = build_history_prompt(msgs, f, recent_count=3)
        assert 'last 3 of 10' in result

    def test_default_recent_count(self):
        assert RECENT_COUNT == 5
