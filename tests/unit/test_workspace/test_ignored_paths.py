"""Ignored runtime paths leave the workspace's index and its history."""

import asyncio
from pathlib import Path
import subprocess

import pytest

from app.workspace import ignored_paths
from app.workspace.ignored_paths import (
    PURGE_MARKER,
    purge_ignored_history,
    untrack_ignored,
)
from app.workspace.manager import WorkspaceManager


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args],
        cwd=root,
        env=ignored_paths.git_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace committed before ``data/`` was ignored — the bad state."""
    git(tmp_path, 'init', '-q')
    (tmp_path / 'knowledge').mkdir()
    (tmp_path / 'knowledge' / 'notes.md').write_text('v1\n')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'app.db').write_bytes(b'db-1' * 1000)
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-q', '-m', 'Initial workspace setup')
    # data/ is ignored only now, and the next task commit still takes it.
    (tmp_path / '.gitignore').write_text('data/\n')
    (tmp_path / 'knowledge' / 'notes.md').write_text('v2\n')
    (tmp_path / 'data' / 'app.db').write_bytes(b'db-2' * 1000)
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-q', '-m', 'Knowledge update after task')
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_an_ignored_but_tracked_file_is_untracked_and_kept_on_disk(workspace):
    assert 'data/app.db' in git(workspace, 'ls-files')

    assert run(untrack_ignored(workspace, asyncio.Lock())) == 1

    assert 'data/app.db' not in git(workspace, 'ls-files')
    assert (workspace / 'data' / 'app.db').read_bytes() == b'db-2' * 1000
    # Committed, so the next `git add -A` cannot bring it back.
    (workspace / 'data' / 'app.db').write_bytes(b'db-3')
    git(workspace, 'add', '-A')
    assert git(workspace, 'diff', '--cached', '--name-only') == ''


def test_untrack_is_a_no_op_once_clean(workspace):
    run(untrack_ignored(workspace, asyncio.Lock()))
    head = git(workspace, 'rev-parse', 'HEAD')
    assert run(untrack_ignored(workspace, asyncio.Lock())) == 0
    assert git(workspace, 'rev-parse', 'HEAD') == head


def test_the_history_loses_the_ignored_paths_and_keeps_the_rest(workspace):
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))

    assert run(purge_ignored_history(workspace, lock)) is True

    assert git(workspace, 'log', '--all', '--format=%H', '--', 'data') == ''
    assert git(workspace, 'for-each-ref', 'refs/original/') == ''
    # The knowledge file's history survives, both versions.
    versions = git(
        workspace, 'log', '--format=%H', '--', 'knowledge/notes.md'
    ).split()
    assert len(versions) == 2
    assert (
        git(workspace, 'show', f'{versions[-1]}:knowledge/notes.md') == 'v1\n'
    )
    # And the live files were never touched.
    assert (workspace / 'data' / 'app.db').read_bytes() == b'db-2' * 1000
    assert (workspace / 'knowledge' / 'notes.md').read_text() == 'v2\n'
    # The old blobs are gone, not just unreferenced.
    assert git(workspace, 'count-objects', '-v').count('count: 0') == 1


def test_the_purge_runs_once(workspace):
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))
    run(purge_ignored_history(workspace, lock))
    assert (workspace / '.git' / PURGE_MARKER).exists()
    head = git(workspace, 'rev-parse', 'HEAD')

    assert run(purge_ignored_history(workspace, lock)) is False
    assert git(workspace, 'rev-parse', 'HEAD') == head


def test_a_clean_history_is_not_rewritten(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / '.gitignore').write_text('data/\n')
    (tmp_path / 'notes.md').write_text('x\n')
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-q', '-m', 'init')
    head = git(tmp_path, 'rev-parse', 'HEAD')

    assert run(purge_ignored_history(tmp_path, asyncio.Lock())) is False
    assert git(tmp_path, 'rev-parse', 'HEAD') == head
    assert (tmp_path / '.git' / PURGE_MARKER).read_text() == 'clean\n'


def test_uncommitted_task_edits_survive_the_rewrite(workspace):
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))
    (workspace / 'knowledge' / 'notes.md').write_text('v3, not committed\n')

    assert run(purge_ignored_history(workspace, lock)) is True

    assert (workspace / 'knowledge' / 'notes.md').read_text() == (
        'v3, not committed\n'
    )
    assert git(workspace, 'status', '--porcelain') == ''


def test_no_commit_yet_is_left_alone(tmp_path):
    git(tmp_path, 'init', '-q')
    assert run(purge_ignored_history(tmp_path, asyncio.Lock())) is False
    assert not (tmp_path / '.git' / PURGE_MARKER).exists()


def test_ensure_init_untracks_on_every_install(workspace):
    """An upgrade is the whole rollout: the next boot's ensure_init does it."""
    run(WorkspaceManager(root=workspace).ensure_init(create_venv=False))

    assert 'data/app.db' not in git(workspace, 'ls-files')
    assert (workspace / 'data' / 'app.db').exists()


def test_a_tag_on_the_old_history_moves_with_it(workspace):
    git(workspace, 'tag', 'before-something', 'HEAD~1')
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))

    assert run(purge_ignored_history(workspace, lock)) is True

    assert git(workspace, 'log', '--all', '--format=%H', '--', 'data') == ''
    assert (
        git(workspace, 'show', 'before-something:knowledge/notes.md') == 'v1\n'
    )
    assert git(workspace, 'count-objects', '-v').count('count: 0') == 1


def test_a_fresh_install_never_commits_the_runtime_dirs(tmp_path):
    """A .gitignore written before data/ was a runtime dir used to get the
    first commit, database and all, before ensure_init appended data/."""
    (tmp_path / '.gitignore').write_text('*.pyc\n.cursor/\n')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'app.db').write_bytes(b'db')
    (tmp_path / 'browser_profiles').mkdir()
    (tmp_path / 'browser_profiles' / 'cache.bin').write_bytes(b'c')

    run(WorkspaceManager(root=tmp_path).ensure_init(create_venv=False))

    assert git(tmp_path, 'log', '--all', '--format=%H', '--', 'data') == ''
    assert (
        git(tmp_path, 'log', '--all', '--format=%H', '--', 'browser_profiles')
        == ''
    )
    lines = (tmp_path / '.gitignore').read_text().splitlines()
    # 'r/' is a substring of '.cursor/' and must still be added.
    assert 'r/' in lines and '.cursor/' in lines
    assert 'data/' in lines and 'browser_profiles/' in lines
