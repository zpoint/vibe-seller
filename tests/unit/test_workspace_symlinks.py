"""Unit tests for per-task workspace isolation via symlinks."""

import asyncio

import pytest

from app.workspace.manager import WorkspaceManager


@pytest.fixture
def ws(tmp_path):
    """Create a WorkspaceManager backed by a temp directory."""
    mgr = WorkspaceManager(root=tmp_path)
    return mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prepare_creates_symlinks(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-1')

    assert task_dir.is_dir()
    # .claude is a full copy (not a symlink) so Claude Code's
    # Glob can traverse it for skill discovery.
    assert (task_dir / '.claude').is_dir()
    assert not (task_dir / '.claude').is_symlink()
    assert (task_dir / '.claude' / 'skills').is_dir()
    assert not (task_dir / '.claude' / 'skills').is_symlink()
    assert (task_dir / 'knowledge').is_symlink()
    assert (task_dir / 'stores').is_symlink()
    assert (task_dir / 'CLAUDE.md').is_symlink()
    assert (task_dir / 'knowledge').resolve() == (
        ws.root / 'knowledge'
    ).resolve()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prepare_clean_recreates(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-2')
    (task_dir / 'junk.txt').write_text('data')
    assert (task_dir / 'junk.txt').exists()

    task_dir2 = await ws.prepare_task_workspace('tid-2', clean=True)
    assert task_dir2 == task_dir
    assert not (task_dir2 / 'junk.txt').exists()
    # Recreated after clean
    assert (task_dir2 / '.claude').is_dir()
    assert not (task_dir2 / '.claude').is_symlink()
    assert (task_dir2 / 'knowledge').is_symlink()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_through_symlink(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-3')
    (task_dir / 'knowledge' / 'test.md').write_text('hello')
    # Written through symlink → lands in real dir
    assert (ws.root / 'knowledge' / 'test.md').read_text() == 'hello'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_dir_gitignored(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-4')
    (task_dir / 'data.json').write_text('{}')

    proc = await asyncio.create_subprocess_exec(
        'git',
        'status',
        '--porcelain',
        cwd=str(ws.root),
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    assert 'data.json' not in stdout.decode()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idempotent_refresh(ws):
    await ws.ensure_init()
    await ws.prepare_task_workspace('tid-5')
    # Second call with clean=False is a no-op refresh
    task_dir = await ws.prepare_task_workspace('tid-5')
    assert (task_dir / '.claude').is_dir()
    assert not (task_dir / '.claude').is_symlink()
    assert (task_dir / 'knowledge').is_symlink()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tree_excludes_tasks(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-6')
    (task_dir / 'secret.txt').write_text('hidden')

    tree = await ws.list_tree()
    paths = [item['path'] for item in tree]
    assert not any('tasks' in p for p in paths)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_venv_not_copied_to_task(ws):
    """Stale per-skill .venv dirs must NOT be copied to task workspace."""
    await ws.ensure_init()

    # Create a skill with a stale .venv
    skill_dir = ws.root / '.claude' / 'skills' / 'test-skill'
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / 'SKILL.md').write_text('---\nname: test\n---\n')
    venv_dir = skill_dir / '.venv'
    venv_dir.mkdir()
    (venv_dir / 'bin').mkdir()
    (venv_dir / 'bin' / 'python').write_text('fake')

    # Also create a __pycache__ to verify it's excluded
    cache_dir = skill_dir / '__pycache__'
    cache_dir.mkdir()
    (cache_dir / 'module.pyc').write_text('bytecode')

    task_dir = await ws.prepare_task_workspace('tid-venv')

    # SKILL.md should be copied
    assert (
        task_dir / '.claude' / 'skills' / 'test-skill' / 'SKILL.md'
    ).exists()

    # .venv should NOT be copied
    assert not (
        task_dir / '.claude' / 'skills' / 'test-skill' / '.venv'
    ).exists()

    # __pycache__ should NOT be copied
    assert not (
        task_dir / '.claude' / 'skills' / 'test-skill' / '__pycache__'
    ).exists()
