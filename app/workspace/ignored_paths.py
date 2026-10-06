"""Keep what the workspace's ``.gitignore`` ignores out of its git history.

The workspace is a git repo, and every task ends with ``git add -A`` +
commit (``WorkspaceManager._auto_commit``). ``.gitignore`` lists the
runtime paths that must never be committed (:data:`RUNTIME_DIRS`: the app
and email databases under ``data/``, Chrome profiles, downloads, …) — but
ignoring a path does not untrack it. A workspace
whose ``.gitignore`` gained ``data/`` after its first commit kept
committing ``data/vibe_seller.db`` after every task: one copy of the
database per task, until ``.git`` was hundreds of times the size of the
workspace and ``git gc --auto`` tried to repack all of it, taking the
machine's memory with it.

Two steps, both run by the app itself so an upgrade fixes every install:

* :func:`untrack_ignored` — on every ``ensure_init``: whatever is tracked
  but ignored is removed from the index (``git rm --cached``; the files on
  disk are untouched) and committed. Cheap when there is nothing to do.
* :func:`purge_ignored_history` — once, in the background at boot: if any
  commit still holds one of :data:`PURGE_PATHS`, rewrite the history
  without them, then drop the rewrite's backup refs, expire the reflog and
  ``gc --prune=now`` so the space comes back. A marker in ``.git`` stops it
  running again.

Rewriting changes commit ids. Nothing in the app stores a workspace commit
id (file history is read live from ``git log``), so that costs nothing
here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Every directory the app itself creates under the workspace root for
#: runtime state — the app and email databases (``data/``), task dirs,
#: Chrome profiles and caches, downloads, browser-harness sockets and
#: temp files, generated ``browser-use`` wrappers, pid and log files. None
#: of it is knowledge. ``WorkspaceManager.ensure_init`` writes each into
#: ``.gitignore``, and the history purge removes exactly these: anything
#: else a user ignored is untracked going forward but left in their
#: history.
RUNTIME_DIRS = (
    'data',
    'task_history',
    'tasks',
    'node_modules',
    'browser_profiles',
    'downloads',
    'bh-tmp',
    'bin',
    'r',
    'pids',
    'logs',
    'chat_staging',
)
PURGE_PATHS = RUNTIME_DIRS

#: Written into ``.git`` once the history holds none of ``PURGE_PATHS``.
PURGE_MARKER = 'vibe-seller-history-purged-v1'


def git_env() -> dict[str, str]:
    """``os.environ`` plus a fallback identity, so commits work on hosts
    with no global ``user.name`` / ``user.email`` (a real identity wins)."""
    env = dict(os.environ)
    env.setdefault('GIT_AUTHOR_NAME', 'Vibe Seller')
    env.setdefault('GIT_AUTHOR_EMAIL', 'agent@vibe-seller.local')
    env.setdefault('GIT_COMMITTER_NAME', 'Vibe Seller')
    env.setdefault('GIT_COMMITTER_EMAIL', 'agent@vibe-seller.local')
    return env


async def _git(
    root: Path,
    *args: str,
    stdin: bytes | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run git and return ``(returncode, stdout, stderr)``.

    ``communicate()``, not ``wait()`` then read: ``ls-files`` on a real
    workspace prints more than a pipe buffer holds, and waiting first would
    deadlock on the full pipe.
    """
    env = git_env()
    if extra_env:
        env.update(extra_env)
    proc = await asyncio.create_subprocess_exec(
        'git',
        *args,
        cwd=str(root),
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(stdin)
    return (
        proc.returncode or 0,
        out.decode('utf-8', 'replace'),
        err.decode('utf-8', 'replace'),
    )


async def _commit_staged(root: Path, message: str) -> bool:
    rc, _, _ = await _git(root, 'diff', '--cached', '--quiet')
    if rc == 0:
        return False
    rc, _, err = await _git(root, 'commit', '--quiet', '-m', message)
    if rc:
        raise RuntimeError(f'git commit failed: {err.strip()}')
    return True


async def untrack_ignored(root: Path, lock: asyncio.Lock) -> int:
    """Untrack every tracked file ``.gitignore`` ignores; return how many.

    The files stay on disk — only the index forgets them — and the removal
    is committed so the next ``git add -A`` cannot bring them back.
    """
    if not (root / '.git').exists():
        return 0
    async with lock:
        rc, out, err = await _git(
            root,
            'ls-files',
            '-z',
            '--cached',
            '--ignored',
            '--exclude-standard',
        )
        if rc:
            logger.warning('untrack_ignored: ls-files failed: %s', err.strip())
            return 0
        paths = [p for p in out.split('\0') if p]
        if not paths:
            return 0
        rc, _, err = await _git(
            root,
            'rm',
            '-r',
            '--cached',
            '--quiet',
            '--ignore-unmatch',
            '--pathspec-from-file=-',
            '--pathspec-file-nul',
            stdin='\0'.join(paths).encode(),
        )
        if rc:
            logger.warning('untrack_ignored: git rm failed: %s', err.strip())
            return 0
        await _commit_staged(
            root, f'Stop tracking {len(paths)} file(s) .gitignore ignores'
        )
    logger.info('untrack_ignored: untracked %d ignored file(s)', len(paths))
    return len(paths)


async def _history_holds_purge_paths(root: Path) -> bool:
    rc, out, _ = await _git(
        root, 'log', '--all', '-1', '--format=%H', '--', *PURGE_PATHS
    )
    return rc == 0 and bool(out.strip())


async def purge_ignored_history(root: Path, lock: asyncio.Lock) -> bool:
    """Rewrite the history without :data:`PURGE_PATHS`; True if it did.

    Run after :func:`untrack_ignored`, so the current commit already lacks
    those paths and the rewrite's final checkout cannot touch them on disk.
    Holds the git lock throughout, so task auto-commits wait for it rather
    than racing the rewrite.
    """
    git_dir = root / '.git'
    marker = git_dir / PURGE_MARKER
    if not git_dir.is_dir() or marker.exists():
        return False
    async with lock:
        rc, _, _ = await _git(root, 'rev-parse', '--verify', '--quiet', 'HEAD')
        if rc:
            return False  # no commit yet — nothing to purge
        if not await _history_holds_purge_paths(root):
            marker.write_text('clean\n')
            return False
        logger.info(
            'purge_ignored_history: rewriting workspace history without %s',
            ', '.join(PURGE_PATHS),
        )
        # filter-branch refuses a dirty tree; commit what a task left.
        await _git(root, 'add', '-A')
        await _commit_staged(root, 'Workspace changes before history cleanup')
        rc, _, err = await _git(
            root,
            'filter-branch',
            '-f',
            '--prune-empty',
            # Tags move to the rewritten commits too; a tag left on the old
            # history would keep every old object alive through the gc.
            '--tag-name-filter',
            'cat',
            '--index-filter',
            'git rm -r --cached --quiet --ignore-unmatch -- '
            + ' '.join(PURGE_PATHS),
            '--',
            '--all',
            extra_env={'FILTER_BRANCH_SQUELCH_WARNING': '1'},
        )
        if rc:
            logger.warning(
                'purge_ignored_history: filter-branch failed, history left '
                'as it was: %s',
                err.strip()[-500:],
            )
            return False
        # The rewrite keeps the old history under refs/original/ and in the
        # reflog; both must go or gc keeps every old object alive.
        _, refs, _ = await _git(
            root, 'for-each-ref', '--format=%(refname)', 'refs/original/'
        )
        for ref in refs.split():
            await _git(root, 'update-ref', '-d', ref)
        await _git(root, 'reflog', 'expire', '--expire=now', '--all')
        rc, _, err = await _git(root, 'gc', '--prune=now', '--quiet')
        if rc:
            logger.warning('purge_ignored_history: gc failed: %s', err.strip())
        marker.write_text('purged\n')
    logger.info('purge_ignored_history: done')
    return True
