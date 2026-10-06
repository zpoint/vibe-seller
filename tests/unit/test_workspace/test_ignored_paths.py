"""Ignored runtime paths leave the workspace's index and its history."""

import asyncio
import os
from pathlib import Path
import subprocess

import pytest

from app.workspace import ignored_paths
from app.workspace.ignored_paths import (
    PURGE_MARKER,
    PURGE_PENDING,
    PurgeError,
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


def db_blobs(root: Path) -> set[str]:
    """Every blob ``data/app.db`` has had in history."""
    blobs = set()
    for commit in git(root, 'log', '--all', '--format=%H').split():
        found = subprocess.run(
            [
                'git',
                'rev-parse',
                '--verify',
                '--quiet',
                f'{commit}:data/app.db',
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if found.returncode == 0:
            blobs.add(found.stdout.strip())
    return blobs


def object_exists(root: Path, oid: str) -> bool:
    return (
        subprocess.run(
            ['git', 'cat-file', '-e', oid], cwd=root, capture_output=True
        ).returncode
        == 0
    )


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
    old_blobs = db_blobs(workspace)
    assert len(old_blobs) == 2
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
    # The old database blobs are deleted, loose or packed, not just
    # unreferenced.
    for blob in old_blobs:
        assert not object_exists(workspace, blob)


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
    old_blobs = db_blobs(workspace)
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))

    assert run(purge_ignored_history(workspace, lock)) is True

    assert git(workspace, 'log', '--all', '--format=%H', '--', 'data') == ''
    assert (
        git(workspace, 'show', 'before-something:knowledge/notes.md') == 'v1\n'
    )
    for blob in old_blobs:
        assert not object_exists(workspace, blob)


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
    # Root-anchored; and '/r/' is added although 'r/' is inside '.cursor/'.
    assert '/r/' in lines and '.cursor/' in lines
    assert '/data/' in lines and '/browser_profiles/' in lines


def test_a_nested_folder_with_a_runtime_name_stays_tracked(workspace):
    """An unanchored legacy 'data/' line also matches knowledge/x/data/;
    untracking is scoped to the root runtime dirs, so that file stays."""
    nested = workspace / 'knowledge' / 'supplier' / 'data'
    nested.mkdir(parents=True)
    (nested / 'prices.md').write_text('kept\n')
    git(workspace, 'add', '-f', 'knowledge/supplier/data/prices.md')
    git(workspace, 'commit', '-q', '-m', 'a knowledge file under a data/ dir')

    run(untrack_ignored(workspace, asyncio.Lock()))

    tracked = git(workspace, 'ls-files')
    assert 'knowledge/supplier/data/prices.md' in tracked
    assert 'data/app.db' not in tracked


def test_a_legacy_unanchored_line_is_left_alone(workspace):
    run(WorkspaceManager(root=workspace).ensure_init(create_venv=False))
    lines = (workspace / '.gitignore').read_text().splitlines()
    assert 'data/' in lines and '/data/' not in lines
    assert '/browser_profiles/' in lines


def test_a_failed_history_probe_is_not_marked_clean(workspace, monkeypatch):
    """`git log` failing used to read as "nothing to purge" and wrote the
    marker, so that install never retried."""
    real_git = ignored_paths._git  # noqa: SLF001

    async def log_fails(root, *args, **kw):
        if args[:1] == ('log',):
            return 128, '', 'fatal: simulated'
        return await real_git(root, *args, **kw)

    monkeypatch.setattr(ignored_paths, '_git', log_fails)
    with pytest.raises(PurgeError):
        run(purge_ignored_history(workspace, asyncio.Lock()))
    assert not (workspace / '.git' / PURGE_MARKER).exists()


def test_a_failed_reclaim_is_retried_next_boot(workspace, monkeypatch):
    """Once rewritten, the history probe reads clean; the pending file is
    what makes the next boot run the gc that failed."""
    lock = asyncio.Lock()
    run(untrack_ignored(workspace, lock))
    old_blobs = db_blobs(workspace)
    real_reclaim = ignored_paths._reclaim  # noqa: SLF001

    async def gc_fails(_root):
        raise PurgeError('git gc failed: simulated')

    monkeypatch.setattr(ignored_paths, '_reclaim', gc_fails)
    with pytest.raises(PurgeError):
        run(purge_ignored_history(workspace, lock))
    git_dir = workspace / '.git'
    assert (git_dir / PURGE_PENDING).exists()
    assert not (git_dir / PURGE_MARKER).exists()
    assert (
        git(workspace, 'log', '--branches', '--format=%H', '--', 'data') == ''
    )

    monkeypatch.setattr(ignored_paths, '_reclaim', real_reclaim)
    assert run(purge_ignored_history(workspace, lock)) is True

    assert (git_dir / PURGE_MARKER).exists()
    assert not (git_dir / PURGE_PENDING).exists()
    for blob in old_blobs:
        assert not object_exists(workspace, blob)


@pytest.mark.skipif(os.name != 'posix', reason='process groups are POSIX')
def test_cancelling_a_git_call_kills_the_child(tmp_path):
    """A shutdown cancels the purge; the git it was running must not go on."""
    git(tmp_path, 'init', '-q')
    tag = 'vs-cancel-test-41.7'
    script = tmp_path / f'{tag}.sh'
    script.write_text('#!/bin/sh\nsleep 30\n')
    script.chmod(0o755)

    async def go():
        call = asyncio.create_task(
            ignored_paths._git(  # noqa: SLF001
                tmp_path, '-c', f'alias.hold=!{script}', 'hold'
            )
        )
        for _ in range(50):
            await asyncio.sleep(0.1)
            if running(tag):
                break
        assert running(tag)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    run(go())
    assert not running(tag)


def running(tag: str) -> bool:
    return (
        subprocess.run(['pgrep', '-f', tag], capture_output=True).returncode
        == 0
    )
