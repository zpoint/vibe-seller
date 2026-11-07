"""Unit tests: every agent launch carries ONE ``--settings`` payload
that turns Claude Code's auto-mode classifier off.

The backend's hooks (``claude_backend_hooks``) are the ONE permission
authority for agent sessions. The CLI's own auto-mode classifier is a
second authority: with any non-Anthropic provider it cannot produce a
verdict in plan mode and the CLI STOPS the session after a handful of
tool calls (``auto_mode_stop_no_check_in_session``), killing planning
dead. ``launch_settings()`` disables it; the launch writes exactly one
settings file and passes exactly one ``--settings`` — with the
dead-proxy blanks merged into that same payload, never a second flag.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.ai.agent_proxy as agent_proxy
import app.ai.claude_backend as cb_mod
from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit


def _make_fake_db_session():
    fake_user = MagicMock()
    fake_user.id = 'fake-bot-id'
    fake_user.role = 'ai_bot'
    fake_result = MagicMock()
    fake_result.scalars.return_value.first.return_value = fake_user
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)

    @asynccontextmanager
    async def _ctx():
        yield fake_db

    return _ctx


def _make_fake_subprocess():
    captured: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured.extend(args)
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.close = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(return_value=b'')
        proc.stderr = MagicMock()
        proc.stderr.readline = AsyncMock(return_value=b'')
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        proc.pid = 12345
        return proc

    return fake_exec, captured


def _patch(monkeypatch, tmp_path, env: dict | None = None):
    """Everything AgentSession.start() needs; returns the captured argv."""
    fake_exec, captured = _make_fake_subprocess()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setattr(
        cb_mod,
        'workspace_manager',
        MagicMock(ensure_init=AsyncMock()),
    )
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_env_for_profile',
        staticmethod(lambda pid: dict(env if env is not None else os.environ)),
    )
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_load_global_mcp',
        staticmethod(lambda pid: False),
    )
    monkeypatch.setattr(cb_mod, 'read_mcp_config', lambda: {'mcpServers': {}})
    monkeypatch.setattr(cb_mod, 'async_session', _make_fake_db_session())
    monkeypatch.setattr(
        cb_mod,
        'atomic_write_json',
        lambda path, data: path.write_text(json.dumps(data)),
    )
    monkeypatch.setattr(cb_mod, 'create_token', lambda *a: 'fake-token')
    return captured


def _settings_args(captured: list[str]) -> tuple[list[str], dict]:
    """The ONE --settings flag pair and the payload it points at."""
    flags = [i for i, arg in enumerate(captured) if arg == '--settings']
    assert len(flags) == 1, f'expected one --settings, got {captured}'
    path = captured[flags[0] + 1]
    return flags, json.loads(open(path, encoding='utf-8').read())


class TestLaunchSettings:
    @pytest.mark.parametrize('mode', ['plan_then_execute', 'execute'])
    async def test_one_settings_flag_disables_the_classifier(
        self, mode, monkeypatch, tmp_path
    ):
        captured = _patch(monkeypatch, tmp_path)
        session = AgentSession(
            task_id='launch-settings',
            prompt='test',
            mode=mode,
            task_dir=tmp_path,
        )
        await session.start()

        flags, payload = _settings_args(captured)
        # exactly one flag, and no leftover second override
        assert captured[flags[0] - 1] != '--settings'
        assert payload['disableAutoMode'] == 'disable'
        assert payload['useAutoModeDuringPlan'] is False
        # plan mode maps to the plan permission flag; bypass to the
        # other — the setting rides along on BOTH.
        assert '--permission-mode' in captured

    async def test_dead_proxy_blanks_merge_into_the_same_payload(
        self, monkeypatch, tmp_path
    ):
        env = {'HTTPS_PROXY': 'http://127.0.0.1:1', 'PATH': 'x'}
        monkeypatch.setattr(
            agent_proxy, 'proxy_refuses', AsyncMock(return_value=True)
        )
        captured = _patch(monkeypatch, tmp_path, env=env)

        session = AgentSession(
            task_id='launch-proxy',
            prompt='test',
            mode='execute',
            task_dir=tmp_path,
        )
        await session.start()

        _, payload = _settings_args(captured)
        assert payload['disableAutoMode'] == 'disable'
        assert payload['env']['HTTPS_PROXY'] == ''
        assert payload['env']['https_proxy'] == ''

    async def test_launch_settings_are_fresh_per_call(self):
        one = agent_proxy.launch_settings()
        two = agent_proxy.launch_settings()
        one['env'] = {}
        assert 'env' not in two, 'launch settings must never be shared'
