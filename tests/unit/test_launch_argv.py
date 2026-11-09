"""The launch argv must name real files the ONE thread hop wrote.

Brief #14d: a scripted edit dropped ``build_launch_artifacts`` from an
``asyncio.to_thread(...)`` call and the workspace assistant's launch
died with ``TypeError: 'str' object is not callable`` — in CI, because
no test ran ``WorkspaceAssistantSession.start()`` far enough to build
its command. Both session launches are run here to the spawn boundary
(the subprocess spawn is faked) and their ``--append-system-prompt-file``
/ ``--settings`` flags must name files that exist and
carry the right bytes. Those files are written by the one
``asyncio.to_thread`` hop, so a lost function argument fails HERE
instead of in production.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.ai import launch_artifacts
from app.ai.claude_backend import AgentSession, ProfileManager
from app.ai.workspace_assistant import WorkspaceAgentSession
from app.workspace.manager import workspace_manager

pytestmark = pytest.mark.unit


class _Spawned(Exception):
    """Raised by the fake spawn: start() has built its full argv."""


async def _async_noop(*_a, **_kw):
    return None


def _flag(cmd: list[str], name: str) -> Path:
    assert name in cmd, f'{name} missing from argv: {cmd}'
    return Path(cmd[cmd.index(name) + 1])


def _prepare(monkeypatch, tmp_path):
    """Everything OUTSIDE the hop is faked; the hop itself runs real."""
    captured: list[tuple] = []

    async def fake_exec(*cmd, **kw):
        captured.append((list(cmd), kw))
        raise _Spawned

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setenv('MOCK_CLI', '')
    # Launch artifacts land under the patched home, never the real one.
    monkeypatch.setattr('app.ai.claude_backend_utils.VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(
        workspace_manager, 'ensure_init', _async_noop, raising=False
    )
    monkeypatch.setattr(
        ProfileManager, 'get_env_for_profile', staticmethod(lambda _p: {})
    )
    # The venv/guard wiring reads the real home; its own tests pin it.
    seen: list = []
    monkeypatch.setattr(
        launch_artifacts,
        'apply_agent_venv_path',
        lambda env, slug: seen.append(slug),
    )
    return captured


def _assert_common_argv(cmd: list[str], system_prompt: str) -> None:
    prompt_file = _flag(cmd, '--append-system-prompt-file')
    assert prompt_file.is_file()
    assert prompt_file.read_text(encoding='utf-8') == system_prompt
    settings_file = _flag(cmd, '--settings')
    settings = json.loads(settings_file.read_text(encoding='utf-8'))
    assert settings['disableAutoMode'] == 'disable'
    assert settings['useAutoModeDuringPlan'] is False


def test_the_task_launch_argv_names_the_hops_files(tmp_path, monkeypatch):
    captured = _prepare(monkeypatch, tmp_path)
    task_dir = tmp_path / 'task'
    task_dir.mkdir()
    session = AgentSession(
        task_id='launch-argv',
        prompt='do the thing',
        system_prompt_extra='SYS PROMPT',
        mode='auto',
        task_dir=task_dir,
    )
    session._register_vibe_seller_mcp = _async_noop

    with pytest.raises(_Spawned):
        asyncio.run(session.start())

    ((cmd, _kw),) = captured
    _assert_common_argv(cmd, 'SYS PROMPT')
    # No product-defined reviewer agent types are registered any more.
    assert '--agents' not in cmd


def test_the_workspace_launch_argv_names_the_hops_files(tmp_path, monkeypatch):
    captured = _prepare(monkeypatch, tmp_path)
    session = WorkspaceAgentSession(
        session_key='ws-launch-argv',
        prompt='hello',
        system_prompt_extra='WS SYS PROMPT',
    )

    with pytest.raises(_Spawned):
        asyncio.run(session.start())

    ((cmd, _kw),) = captured
    _assert_common_argv(cmd, 'WS SYS PROMPT')
    assert '--agents' not in cmd
