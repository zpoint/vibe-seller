"""Unit tests: MCP isolation in AgentSession command construction.

Verifies that AgentSession.start() includes --mcp-config and
--strict-mcp-config flags so global MCP servers don't leak into
agent sessions.

Regression test for b571609 where ws_dir changed from
VIBE_SELLER_DIR to self.task_dir, breaking the .mcp.json check.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.ai.claude_backend as cb_mod
from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit


def _make_fake_db_session():
    """Return a fake async_session context manager."""
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
    """Return (fake_exec, captured_cmd) for capturing command args."""
    captured_cmd: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured_cmd.extend(args)
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.close = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        # stdout/stderr immediately EOF
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(return_value=b'')
        proc.stderr = MagicMock()
        proc.stderr.readline = AsyncMock(return_value=b'')
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        proc.pid = 12345
        return proc

    return fake_exec, captured_cmd


def _apply_common_mocks(monkeypatch, tmp_path, load_global_mcp=False):
    """Apply all mocks needed to run AgentSession.start()."""
    fake_exec, captured_cmd = _make_fake_subprocess()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setattr(
        cb_mod,
        'workspace_manager',
        MagicMock(ensure_init=AsyncMock()),
    )
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_env_for_profile',
        staticmethod(lambda pid: os.environ.copy()),
    )
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_load_global_mcp',
        staticmethod(lambda pid: load_global_mcp),
    )
    # Let _register_vibe_seller_mcp run with mocked internals
    monkeypatch.setattr(
        cb_mod,
        'read_mcp_config',
        lambda: {'mcpServers': {}},
    )
    monkeypatch.setattr(cb_mod, 'async_session', _make_fake_db_session())
    monkeypatch.setattr(
        cb_mod,
        'atomic_write_json',
        lambda path, data: path.write_text(json.dumps(data)),
    )
    monkeypatch.setattr(cb_mod, 'create_token', lambda *a: 'fake-token')

    return captured_cmd


class TestMcpCommandIsolation:
    """Verify --mcp-config and --strict-mcp-config in agent cmd."""

    @pytest.mark.asyncio
    async def test_strict_mcp_config_in_command(self, tmp_path, monkeypatch):
        """start() must include --mcp-config and
        --strict-mcp-config when task_dir has .mcp.json."""
        captured_cmd = _apply_common_mocks(monkeypatch, tmp_path)

        session = AgentSession(
            task_id='test-mcp-iso',
            prompt='test',
            mode='execute',
            task_dir=tmp_path,
        )
        await session.start()

        assert '--mcp-config' in captured_cmd, 'Expected --mcp-config in cmd'
        assert '--strict-mcp-config' in captured_cmd, (
            'Expected --strict-mcp-config in cmd'
        )
        idx = captured_cmd.index('--mcp-config')
        assert captured_cmd[idx + 1] == str(tmp_path / '.mcp.json')

    @pytest.mark.asyncio
    async def test_no_strict_when_load_global_mcp_true(
        self, tmp_path, monkeypatch
    ):
        """load_global_mcp=True → no --strict-mcp-config."""
        captured_cmd = _apply_common_mocks(
            monkeypatch, tmp_path, load_global_mcp=True
        )

        session = AgentSession(
            task_id='test-mcp-global',
            prompt='test',
            mode='execute',
            task_dir=tmp_path,
        )
        await session.start()

        assert '--strict-mcp-config' not in captured_cmd

    @pytest.mark.asyncio
    async def test_fallback_on_register_failure(self, tmp_path, monkeypatch):
        """DB failure in _register_vibe_seller_mcp → fallback
        empty .mcp.json → --strict-mcp-config still added."""
        captured_cmd = _apply_common_mocks(monkeypatch, tmp_path)
        # Override async_session to raise
        monkeypatch.setattr(
            cb_mod,
            'async_session',
            MagicMock(side_effect=RuntimeError('DB down')),
        )

        session = AgentSession(
            task_id='test-mcp-fallback',
            prompt='test',
            mode='execute',
            task_dir=tmp_path,
        )
        await session.start()

        assert '--strict-mcp-config' in captured_cmd, (
            'Fallback should still produce --strict-mcp-config'
        )
        # Verify fallback file was written
        mcp_json = tmp_path / '.mcp.json'
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text())
        assert data == {'mcpServers': {}}
