"""Agents keep one memory: the workspace knowledge tree.

Claude Code's auto-memory is off in every spawn env, a profile cannot
turn it back on, and the folders earlier releases left are removed
without touching anything else under the Claude config dir.
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.profiles import ProfileManager
from app.models.base import Base
from app.workspace import claude_memory_cleanup as cleanup

pytestmark = pytest.mark.unit


def test_spawn_env_disables_auto_memory_for_unknown_profile():
    env = ProfileManager.get_env_for_profile('no-such-profile')
    assert env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'


def test_profile_cannot_turn_auto_memory_back_on(monkeypatch):
    monkeypatch.setattr(
        ProfileManager,
        'get_profile',
        staticmethod(
            lambda _id: {'env': {'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '0'}}
        ),
    )
    env = ProfileManager.get_env_for_profile('custom')
    assert env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'


def _project(config: Path, cwd: str, *, memory: bool = True) -> Path:
    proj = config / 'projects' / cleanup.sanitize_path(cwd)
    proj.mkdir(parents=True)
    (proj / 'session.jsonl').write_text('{}')
    if memory:
        (proj / 'memory').mkdir()
        (proj / 'memory' / 'MEMORY.md').write_text('- note')
    return proj


def test_purge_removes_only_our_memory_folders(tmp_path):
    home = tmp_path / 'acme' / '.vibe-seller'
    home.mkdir(parents=True)
    config = tmp_path / 'claude'
    root = _project(config, str(home))
    task = _project(config, str(home / 'tasks' / 'abc-123'))
    sibling = _project(config, str(home) + '-e2e')
    sibling_task = _project(config, str(home) + '-e2e/tasks/abc')
    unrelated = _project(config, str(tmp_path / 'code' / 'repo'))

    removed = cleanup.purge_agent_auto_memory(home, {config})

    assert set(removed) == {root / 'memory', task / 'memory'}
    for gone in (root, task):
        assert not (gone / 'memory').exists()
        assert (gone / 'session.jsonl').exists()  # transcripts stay
    for kept in (sibling, sibling_task, unrelated):
        assert (kept / 'memory' / 'MEMORY.md').exists()

    assert cleanup.purge_agent_auto_memory(home, {config}) == []


def test_purge_covers_profile_config_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'default'))
    dirs = cleanup.config_dirs([
        {'CLAUDE_CONFIG_DIR': str(tmp_path / 'kimi')},
        {},
    ])
    assert dirs == {tmp_path / 'default', tmp_path / 'kimi'}


def test_purge_tolerates_missing_projects_dir(tmp_path):
    assert cleanup.purge_agent_auto_memory(tmp_path, {tmp_path / 'x'}) == []


async def test_boot_purge_runs_once(tmp_path, monkeypatch):
    """Later boots leave alone memory a person wrote in the runtime home."""
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        cleanup,
        'async_session',
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
        raising=False,
    )
    home = tmp_path / '.vibe-seller'
    home.mkdir()
    config = tmp_path / 'claude'
    monkeypatch.setattr(cleanup, 'VIBE_SELLER_DIR', home)
    monkeypatch.setattr(cleanup, 'config_dirs', lambda _envs: {config})
    proj = _project(config, str(home))
    try:
        await cleanup.purge_on_boot()
        assert not (proj / 'memory').exists()

        (proj / 'memory').mkdir()
        (proj / 'memory' / 'MEMORY.md').write_text('- mine')
        await cleanup.purge_on_boot()
        assert (proj / 'memory' / 'MEMORY.md').exists()
    finally:
        await engine.dispose()
