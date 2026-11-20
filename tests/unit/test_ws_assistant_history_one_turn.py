"""A restarted workspace assistant gets its history as ONE turn.

Observed (e2e, intermittent on main): the manager keeps every message a
user sent the assistant, and a new session wrote each of them into the
fresh CLI as its own stream-json ``user`` line before the prompt. Every
such line is a turn: the CLI re-answered the old messages one by one,
and the send collected an old turn's reply as the answer to the new
message ("turn 2" echoed the previous prompt). The history is folded
into the first turn instead, the way a task's restart does it.

``start()`` runs for real up to a fake process that records stdin.
"""

import asyncio
import json

import pytest

from app.ai import launch_artifacts
from app.ai.claude_backend import ProfileManager
from app.ai.workspace_assistant import WorkspaceAgentSession
from app.workspace.manager import workspace_manager

pytestmark = pytest.mark.unit


class _Stdin:
    def __init__(self):
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        for line in data.decode().splitlines():
            if line.strip():
                self.lines.append(json.loads(line))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


class _EOF:
    async def readline(self) -> bytes:
        await asyncio.sleep(3600)
        return b''

    async def read(self, _n: int = -1) -> bytes:
        await asyncio.sleep(3600)
        return b''


class _Proc:
    def __init__(self):
        self.stdin = _Stdin()
        self.stdout = _EOF()
        self.stderr = _EOF()
        self.returncode = None
        self.pid = 4242

    async def wait(self) -> int:
        await asyncio.sleep(3600)
        return 0


async def _noop(*_a, **_kw):
    return None


@pytest.fixture
def spawned(monkeypatch, tmp_path):
    procs: list[_Proc] = []

    async def fake_exec(*_cmd, **_kw):
        procs.append(_Proc())
        return procs[-1]

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setenv('MOCK_CLI', '')
    monkeypatch.setattr('app.ai.claude_backend_utils.VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr('app.ai.compaction.HISTORY_DIR', tmp_path / 'history')
    monkeypatch.setattr(workspace_manager, 'ensure_init', _noop, raising=False)
    monkeypatch.setattr(
        ProfileManager, 'get_env_for_profile', staticmethod(lambda _p: {})
    )
    monkeypatch.setattr(
        launch_artifacts, 'apply_agent_venv_path', lambda env, slug: None
    )
    return procs


def _user_lines(proc: _Proc) -> list[str]:
    return [
        m['message']['content']
        for m in proc.stdin.lines
        if m.get('type') == 'user'
    ]


async def _start(session: WorkspaceAgentSession) -> None:
    await session.start()
    for t in (session._task, getattr(session, '_stderr_task', None)):
        if t is not None:
            t.cancel()


async def test_history_is_one_turn_with_the_prompt(spawned):
    session = WorkspaceAgentSession(
        session_key='ws-history',
        prompt='And the store notes too.',
        message_history=[
            {'role': 'user', 'content': 'Tidy the knowledge notes.'},
            {'role': 'user', 'content': 'Also the catalog.'},
        ],
    )
    await _start(session)
    (proc,) = spawned
    lines = _user_lines(proc)
    assert len(lines) == 1, lines
    assert lines[0].endswith('And the store notes too.')
    assert 'Tidy the knowledge notes.' in lines[0]
    assert 'Also the catalog.' in lines[0]


async def test_no_history_sends_just_the_prompt(spawned):
    session = WorkspaceAgentSession(session_key='ws-fresh', prompt='hello')
    await _start(session)
    (proc,) = spawned
    assert _user_lines(proc) == ['hello']
