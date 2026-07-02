"""Unit tests for per-task workspace isolation.

Shared resources (knowledge, stores, store-data, CLAUDE.md) are linked
into each task dir. POSIX uses symlinks; Windows uses directory
*junctions* for dirs + a copy for CLAUDE.md, because ``os.symlink`` on
Windows needs ``SeCreateSymbolicLinkPrivilege`` — a privilege a normally
launched (non-elevated, Developer-Mode-off) process lacks, which raised
``WinError 1314`` on task creation.
"""

import asyncio
import os

import pytest

from app.workspace import task_links
from app.workspace.manager import WorkspaceManager

IS_WINDOWS = os.name == 'nt'


@pytest.fixture
def ws(tmp_path):
    """Create a WorkspaceManager backed by a temp directory."""
    mgr = WorkspaceManager(root=tmp_path)
    return mgr


def _assert_shared_dir_link(link_path, target):
    """A task→shared-dir link resolves to the shared dir via the
    platform's privilege-free mechanism (POSIX symlink / Windows
    junction) — never a raw os.symlink on Windows."""
    assert link_path.is_dir()  # resolves through the link
    assert link_path.resolve() == target.resolve()
    if IS_WINDOWS:
        assert not link_path.is_symlink(), 'must be a junction, not a symlink'
        assert task_links._is_junction(link_path)
    else:
        assert link_path.is_symlink()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prepare_creates_shared_links(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-1')

    assert task_dir.is_dir()
    # .claude is a full copy (not a link) so Claude Code's Glob can
    # traverse it for skill discovery.
    assert (task_dir / '.claude').is_dir()
    assert not (task_dir / '.claude').is_symlink()
    assert (task_dir / '.claude' / 'skills').is_dir()
    assert not (task_dir / '.claude' / 'skills').is_symlink()
    # Shared dirs are linked to the workspace root.
    _assert_shared_dir_link(task_dir / 'knowledge', ws.root / 'knowledge')
    _assert_shared_dir_link(task_dir / 'stores', ws.root / 'stores')
    # Per-store run data (reports/captures) lives outside stores/ so it
    # never surfaces as knowledge; tasks reach it via this link.
    _assert_shared_dir_link(task_dir / 'store-data', ws.root / 'store-data')
    # CLAUDE.md: symlink on POSIX, plain copy on Windows.
    claude_md = task_dir / 'CLAUDE.md'
    assert claude_md.exists()
    if IS_WINDOWS:
        assert claude_md.is_file() and not claude_md.is_symlink()
    else:
        assert claude_md.is_symlink()


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
    _assert_shared_dir_link(task_dir2 / 'knowledge', ws.root / 'knowledge')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_through_link(ws):
    await ws.ensure_init()
    task_dir = await ws.prepare_task_workspace('tid-3')
    (task_dir / 'knowledge' / 'test.md').write_text('hello')
    # Written through the link → lands in the real shared dir
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
    _assert_shared_dir_link(task_dir / 'knowledge', ws.root / 'knowledge')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clean_and_remove_preserve_shared_data(ws):
    """Tearing down a task workspace must never delete shared data.

    On Windows the shared links are junctions, and a naive rmtree of a
    junction (or, on some runtimes, of a dir containing one) follows it
    into the target. Both ``clean=True`` and ``remove_task_workspace``
    must clear the links first so shared knowledge/stores survive.
    """
    await ws.ensure_init()
    (ws.root / 'knowledge' / 'keep.md').write_text('precious')
    task_dir = await ws.prepare_task_workspace('tid-del')

    # clean=True rebuilds the workspace but must leave shared data intact
    await ws.prepare_task_workspace('tid-del', clean=True)
    assert (ws.root / 'knowledge' / 'keep.md').read_text() == 'precious'

    # explicit teardown must also leave shared data intact
    task_links.remove_task_workspace(task_dir)
    assert not task_dir.exists()
    assert (ws.root / 'knowledge' / 'keep.md').read_text() == 'precious'


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == 'nt',
    reason='POSIX-only: uses os.symlink as a junction stand-in, which '
    'itself needs the privilege this scenario simulates missing',
)
async def test_windows_strategy_avoids_privileged_symlink(ws, monkeypatch):
    """Regression guard for WinError 1314.

    A stock Windows box cannot ``os.symlink`` (needs a privilege it
    lacks). CI can't withhold that privilege — Linux and GitHub's
    Windows runners both symlink fine — so we simulate it: force the
    Windows link strategy and make ``os.symlink`` raise 1314. The
    workspace must still build, via junctions (dirs) + copy (CLAUDE.md),
    i.e. it must NOT call ``os.symlink`` for the shared resources.
    """
    real_symlink = os.symlink
    junctions: list[str] = []

    def fake_create_junction(target, link):
        junctions.append(str(link))
        real_symlink(target, link)  # stand-in so it resolves on POSIX

    def deny_symlink(*args, **kwargs):
        raise OSError(1314, 'A required privilege is not held by the client')

    monkeypatch.setattr(task_links, '_IS_WINDOWS', True)
    monkeypatch.setattr(task_links, '_create_junction', fake_create_junction)
    monkeypatch.setattr(os, 'symlink', deny_symlink)

    await ws.ensure_init()
    # Must NOT raise WinError 1314:
    task_dir = await ws.prepare_task_workspace('tid-nopriv')

    # Every shared *dir* was linked via the junction primitive:
    linked = {
        name
        for name in ('knowledge', 'stores', 'store-data')
        if any(name in j for j in junctions)
    }
    assert linked == {'knowledge', 'stores', 'store-data'}
    # CLAUDE.md was copied, not symlinked:
    claude_md = task_dir / 'CLAUDE.md'
    assert claude_md.is_file() and not claude_md.is_symlink()
    # write-through still reaches the shared dir:
    (task_dir / 'knowledge' / 'wt.md').write_text('ok')
    assert (ws.root / 'knowledge' / 'wt.md').read_text() == 'ok'


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
