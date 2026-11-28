"""Unit tests: agent launches keep the user's own Claude Code config out.

The CLI loads the machine user's ``~/.claude`` setup unless told not
to: their private ``CLAUDE.md``, their settings ``env`` (where
cc-switch writes ``ANTHROPIC_*``, outranking the profile we spawn
with), skills, plugins and user MCP servers. ``app.ai.claude_isolation``
owns the contract; these tests pin how every launch composes it:

- non-default profile → ``--setting-sources project,local``;
- default profile → no ``--setting-sources`` (cc-switch / login work);
- every launch → ``claudeMdExcludes`` + auto memory locked off in the
  ONE ``--settings`` payload;
- "load global MCP" with the user source off → the user-scope servers
  from ``$CLAUDE_CONFIG_DIR/.claude.json`` ride along via
  ``--mcp-config`` (never with ``--strict-mcp-config``).
"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.ai.agent_proxy as agent_proxy
import app.ai.claude_backend as cb_mod
from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_mcp as mcp_mod
from app.ai.claude_backend_utils import mcp_launch_flags
from app.ai.claude_isolation import (
    claude_global_config_path,
    claude_md_excludes,
    isolation_settings,
    read_user_mcp_servers,
    setting_sources_flags,
    uses_user_settings,
)
import app.ai.workspace_assistant as wa_mod

pytestmark = pytest.mark.unit

USER_SERVER = {'command': 'python3', 'args': ['-m', 'marker_server']}


# ── pure composition ────────────────────────────────────────────────


class TestSettingSources:
    def test_default_keeps_the_user_source(self):
        assert uses_user_settings('default')
        assert uses_user_settings(None)
        assert setting_sources_flags('default') == []

    def test_non_default_drops_the_user_source(self):
        assert not uses_user_settings('deepseek')
        assert setting_sources_flags('deepseek') == [
            '--setting-sources',
            'project,local',
        ]


class TestClaudeMdExcludes:
    def test_user_memory_and_every_ancestor_above_workspace(self, tmp_path):
        home = tmp_path / 'home'
        ws = home / '.vibe-seller'
        env = {'HOME': str(home)}
        patterns = claude_md_excludes(ws, env)
        # the user memory file + rules (type User)
        assert (home / '.claude' / 'CLAUDE.md').as_posix() in patterns
        assert (home / '.claude' / 'rules').as_posix() + '/**' in patterns
        # the upward walk reaches HOME as *project* memory
        assert (home / 'CLAUDE.md').as_posix() in patterns
        assert (home / 'CLAUDE.local.md').as_posix() in patterns
        # the workspace's own memory is never excluded
        assert (ws / 'CLAUDE.md').as_posix() not in patterns
        assert not any(
            p.startswith(ws.as_posix() + '/') or p == ws.as_posix()
            for p in patterns
        )

    def test_claude_config_dir_is_respected(self, tmp_path):
        env = {
            'HOME': str(tmp_path / 'home'),
            'CLAUDE_CONFIG_DIR': str(tmp_path / 'cfg'),
        }
        patterns = claude_md_excludes(tmp_path / 'home' / '.vibe-seller', env)
        assert (tmp_path / 'cfg' / 'CLAUDE.md').as_posix() in patterns
        assert (tmp_path / 'cfg' / 'rules').as_posix() + '/**' in patterns
        # ~/.claude is still an ancestor-walk target
        assert (
            tmp_path / 'home' / '.claude' / 'CLAUDE.md'
        ).as_posix() in patterns

    def test_settings_lock_auto_memory_off_and_are_fresh(self, tmp_path):
        one = isolation_settings(tmp_path, {'HOME': str(tmp_path)})
        two = isolation_settings(tmp_path, {'HOME': str(tmp_path)})
        assert one['autoMemoryEnabled'] is False
        assert one['env'] == {'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1'}
        one['env']['X'] = '1'
        assert 'X' not in two['env']


class TestUserMcpServers:
    def test_reads_claude_config_dir_dot_claude_json(self, tmp_path):
        cfg = tmp_path / 'cfg'
        cfg.mkdir()
        (cfg / '.claude.json').write_text(
            json.dumps({'mcpServers': {'marker': USER_SERVER}})
        )
        env = {'CLAUDE_CONFIG_DIR': str(cfg), 'HOME': str(tmp_path / 'h')}
        assert claude_global_config_path(env) == cfg / '.claude.json'
        assert read_user_mcp_servers(env) == {'marker': USER_SERVER}

    def test_without_config_dir_reads_home_dot_claude_json(self, tmp_path):
        # NOT ~/.claude/.claude.json — the CLI keeps it in HOME.
        (tmp_path / '.claude.json').write_text(
            json.dumps({'mcpServers': {'marker': USER_SERVER}})
        )
        env = {'HOME': str(tmp_path)}
        assert claude_global_config_path(env) == tmp_path / '.claude.json'
        assert read_user_mcp_servers(env) == {'marker': USER_SERVER}

    def test_legacy_config_json_wins(self, tmp_path):
        cfg = tmp_path / 'cfg'
        cfg.mkdir()
        (cfg / '.config.json').write_text('{}')
        env = {'CLAUDE_CONFIG_DIR': str(cfg)}
        assert claude_global_config_path(env) == cfg / '.config.json'

    @pytest.mark.parametrize(
        'body',
        [
            None,  # missing file
            '{not json',
            '[]',
            '{"mcpServers": []}',
            '{"mcpServers": {"bad": "string"}}',
        ],
    )
    def test_missing_or_malformed_is_empty(self, tmp_path, body):
        if body is not None:
            (tmp_path / '.claude.json').write_text(body)
        assert read_user_mcp_servers({'HOME': str(tmp_path)}) == {}


class TestMcpFlags:
    def test_user_config_rides_along_without_strict(self, tmp_path):
        user = tmp_path / 'u.json'
        assert mcp_launch_flags(
            load_global=True,
            session_config=None,
            project_config=tmp_path / '.mcp.json',
            user_config=user,
        ) == ['--mcp-config', str(user)]

    def test_session_and_user_config_together(self, tmp_path):
        s, u = tmp_path / 's.json', tmp_path / 'u.json'
        assert mcp_launch_flags(
            load_global=True, session_config=s, user_config=u
        ) == ['--mcp-config', str(s), str(u)]

    def test_strict_launch_never_sees_user_config(self, tmp_path):
        p, u = tmp_path / '.mcp.json', tmp_path / 'u.json'
        assert mcp_launch_flags(
            load_global=False,
            session_config=None,
            project_config=p,
            user_config=u,
        ) == ['--mcp-config', str(p), '--strict-mcp-config']


# ── the task agent launch ───────────────────────────────────────────


def _fake_db_session():
    fake_db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = MagicMock(
        id='bot', role='ai_bot'
    )
    fake_db.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _ctx():
        yield fake_db

    return _ctx


def _fake_exec(captured: list):
    async def fake_exec(*args, **kwargs):
        captured.extend(args)
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout.readline = AsyncMock(return_value=b'')
        proc.stderr.readline = AsyncMock(return_value=b'')
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        proc.pid = 12345
        return proc

    return fake_exec


@pytest.fixture
def claude_home(tmp_path):
    """A fake user config: cc-switch env, marker CLAUDE.md, user MCP."""
    cfg = tmp_path / 'claude-home'
    cfg.mkdir()
    (cfg / 'settings.json').write_text(
        json.dumps({
            'env': {
                'ANTHROPIC_BASE_URL': 'https://cc-switch.example',
                'ANTHROPIC_AUTH_TOKEN': 'sk-fake',
            }
        })
    )
    (cfg / 'CLAUDE.md').write_text('private user instructions\n')
    (cfg / '.claude.json').write_text(
        json.dumps({'mcpServers': {'marker': USER_SERVER}})
    )
    return cfg


def _patch_launch(monkeypatch, tmp_path, claude_home, load_global):
    captured: list[str] = []
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', _fake_exec(captured))
    monkeypatch.setattr(
        cb_mod, 'workspace_manager', MagicMock(ensure_init=AsyncMock())
    )
    env = {
        'PATH': '/usr/bin:/bin',
        'HOME': str(tmp_path / 'home'),
        'CLAUDE_CONFIG_DIR': str(claude_home),
    }
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_env_for_profile',
        staticmethod(lambda pid: dict(env)),
    )
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_load_global_mcp',
        staticmethod(lambda pid: load_global),
    )
    monkeypatch.setattr(mcp_mod, 'read_mcp_config', lambda: {'mcpServers': {}})
    monkeypatch.setattr(cb_mod, 'async_session', _fake_db_session())
    monkeypatch.setattr(
        mcp_mod,
        'atomic_write_json',
        lambda path, data: path.write_text(json.dumps(data)),
    )
    monkeypatch.setattr(mcp_mod, 'create_token', lambda *a: 'fake-token')
    monkeypatch.setattr(
        agent_proxy, 'proxy_refuses', AsyncMock(return_value=True)
    )
    return captured


def _flag_value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def _mcp_configs(argv: list[str]) -> list[str]:
    if '--mcp-config' not in argv:
        return []
    out = []
    for arg in argv[argv.index('--mcp-config') + 1 :]:
        if arg.startswith('--'):
            break
        out.append(arg)
    return out


async def _start(profile_id, tmp_path):
    task_dir = tmp_path / 'task'
    task_dir.mkdir(exist_ok=True)
    session = AgentSession(
        task_id='isolation-test',
        prompt='hi',
        mode='execute',
        task_dir=task_dir,
        profile_id=profile_id,
    )
    await session.start()


class TestTaskLaunch:
    async def test_non_default_profile_is_isolated(
        self, monkeypatch, tmp_path, claude_home
    ):
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, False)
        await _start('deepseek', tmp_path)

        assert _flag_value(argv, '--setting-sources') == 'project,local'
        payload = json.loads(Path(_flag_value(argv, '--settings')).read_text())
        assert (claude_home / 'CLAUDE.md').as_posix() in payload[
            'claudeMdExcludes'
        ]
        assert payload['autoMemoryEnabled'] is False
        assert payload['env']['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
        # backend-owned settings still ride in the SAME payload
        assert payload['disableAutoMode'] == 'disable'
        assert argv.count('--settings') == 1

    async def test_default_profile_keeps_user_source(
        self, monkeypatch, tmp_path, claude_home
    ):
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, False)
        await _start('default', tmp_path)

        assert '--setting-sources' not in argv
        payload = json.loads(Path(_flag_value(argv, '--settings')).read_text())
        assert (claude_home / 'CLAUDE.md').as_posix() in payload[
            'claudeMdExcludes'
        ]
        assert payload['autoMemoryEnabled'] is False

    async def test_global_mcp_on_passes_user_servers(
        self, monkeypatch, tmp_path, claude_home
    ):
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, True)
        await _start('deepseek', tmp_path)

        assert '--strict-mcp-config' not in argv
        configs = _mcp_configs(argv)
        assert len(configs) == 1, argv
        body = json.loads(Path(configs[0]).read_text())
        assert body == {'mcpServers': {'marker': USER_SERVER}}

    async def test_global_mcp_off_never_passes_user_servers(
        self, monkeypatch, tmp_path, claude_home
    ):
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, False)
        await _start('deepseek', tmp_path)

        for cfg in _mcp_configs(argv):
            assert 'marker' not in Path(cfg).read_text()

    async def test_default_with_global_mcp_lets_the_cli_load_them(
        self, monkeypatch, tmp_path, claude_home
    ):
        # The user source is on: the CLI loads its own user servers,
        # so a second copy by file would only duplicate them.
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, True)
        await _start('default', tmp_path)
        assert _mcp_configs(argv) == []

    async def test_malformed_user_config_still_launches(
        self, monkeypatch, tmp_path, claude_home
    ):
        (claude_home / '.claude.json').write_text('{oops')
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, True)
        await _start('deepseek', tmp_path)
        assert argv, 'the agent must still spawn'
        assert _mcp_configs(argv) == []

    async def test_user_settings_proxy_is_ignored_when_source_is_off(
        self, monkeypatch, tmp_path, claude_home
    ):
        # A dead proxy that only lives in the user settings file can
        # never reach an isolated launch — so it is not "dropped" (no
        # notice, no blanks), while the default profile still drops it.
        data = json.loads((claude_home / 'settings.json').read_text())
        data['env']['HTTPS_PROXY'] = 'http://127.0.0.1:1'
        (claude_home / 'settings.json').write_text(json.dumps(data))

        argv = _patch_launch(monkeypatch, tmp_path, claude_home, False)
        await _start('deepseek', tmp_path)
        payload = json.loads(Path(_flag_value(argv, '--settings')).read_text())
        assert 'HTTPS_PROXY' not in payload['env']

        argv = _patch_launch(monkeypatch, tmp_path, claude_home, False)
        await _start('default', tmp_path)
        payload = json.loads(Path(_flag_value(argv, '--settings')).read_text())
        assert payload['env']['HTTPS_PROXY'] == ''


# ── the workspace assistant launch ──────────────────────────────────


class TestAssistantLaunch:
    @pytest.mark.parametrize('profile_id', ['default', 'deepseek'])
    async def test_assistant_follows_the_same_contract(
        self, monkeypatch, tmp_path, claude_home, profile_id
    ):
        argv = _patch_launch(monkeypatch, tmp_path, claude_home, True)
        monkeypatch.setattr(
            wa_mod, 'workspace_manager', MagicMock(ensure_init=AsyncMock())
        )
        monkeypatch.setattr(wa_mod, 'create_token', lambda *a: 'fake-token')
        session = wa_mod.WorkspaceAgentSession(
            session_key=f'assistant-isolation-{profile_id}',
            prompt='hi',
            profile_id=profile_id,
        )
        await session.start()

        payload = json.loads(Path(_flag_value(argv, '--settings')).read_text())
        assert payload['autoMemoryEnabled'] is False
        assert (claude_home / 'CLAUDE.md').as_posix() in payload[
            'claudeMdExcludes'
        ]
        bodies = [Path(c).read_text() for c in _mcp_configs(argv)]
        if profile_id == 'default':
            assert '--setting-sources' not in argv
            assert not any('marker' in b for b in bodies)
        else:
            assert _flag_value(argv, '--setting-sources') == 'project,local'
            assert any('marker' in b for b in bodies), argv
        assert '--strict-mcp-config' not in argv
