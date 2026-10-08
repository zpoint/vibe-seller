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

* :func:`untrack_ignored` — on every ``ensure_init``: whatever under the
  root runtime dirs is tracked but ignored is removed from the index
  (``git rm --cached``; the files on disk are untouched) and committed.
  Cheap when there is nothing to do.
* :func:`purge_ignored_history` — once, in the background at boot: if any
  commit still holds one of :data:`PURGE_PATHS`, rewrite the history
  without them, then drop the rewrite's backup refs, expire the reflog and
  ``gc --prune=now`` so the space comes back. A marker in ``.git``, written
  only once all of that succeeded, stops it running again; anything short
  of that is retried on the next boot.

Rewriting changes commit ids. Nothing in the app stores a workspace commit
id (file history is read live from ``git log``), so that costs nothing
here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import signal

logger = logging.getLogger(__name__)

#: Every directory the app itself creates under the workspace root for
#: runtime state — the app and email databases (``data/``), task dirs,
#: Chrome profiles and caches, downloads, browser-harness sockets and
#: temp files, generated ``browser-use`` wrappers, pid and log files. None
#: of it is knowledge. ``WorkspaceManager.ensure_init`` writes each into
#: ``.gitignore`` (root-anchored), and both the untracking and the history
#: purge touch exactly these, at the workspace root: anything else a user
#: chose to ignore keeps whatever tracking and history it has.
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

#: Written into ``.git`` once the history holds none of ``PURGE_PATHS``
#: and the old objects are gone. Every later boot is then a no-op.
PURGE_MARKER = 'vibe-seller-history-purged-v1'

#: Written into ``.git`` between the rewrite and a successful reclaim.
PURGE_PENDING = 'vibe-seller-history-purge-pending'


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

    **Cancelling this kills git.** Cancelling ``communicate()`` alone leaves
    the child running, and a ``filter-branch`` orphaned by a shutdown would
    go on rewriting while the next boot's purge started a second one (whose
    ``-f`` clears the first one's temp dir). Its own process group on POSIX,
    so the shell ``filter-branch`` runs as and its ``git rm`` children go
    together.
    """
    env = git_env()
    if extra_env:
        env.update(extra_env)
    posix = os.name == 'posix'
    proc = await asyncio.create_subprocess_exec(
        'git',
        *args,
        cwd=str(root),
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=posix,
    )
    try:
        out, err = await proc.communicate(stdin)
    except asyncio.CancelledError:
        _kill(proc, posix)
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        out.decode('utf-8', 'replace'),
        err.decode('utf-8', 'replace'),
    )


def _kill(proc: asyncio.subprocess.Process, posix: bool) -> None:
    if proc.returncode is not None:
        return
    try:
        if posix:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass


async def _commit_staged(root: Path, message: str) -> bool:
    rc, _, _ = await _git(root, 'diff', '--cached', '--quiet')
    if rc == 0:
        return False
    rc, _, err = await _git(root, 'commit', '--quiet', '-m', message)
    if rc:
        raise RuntimeError(f'git commit failed: {err.strip()}')
    return True


async def untrack_ignored(root: Path, lock: asyncio.Lock) -> int:
    """Untrack the ignored files under the root :data:`RUNTIME_DIRS`.

    Only those: the pathspecs are root-relative, so a ``data/`` or ``bin/``
    folder inside ``knowledge/``, a store or a skill — which an unanchored
    ``data/`` line also matches — keeps whatever tracking it has, and so
    does anything else a user chose to ignore. The files stay on disk (only
    the index forgets them), and the removal is committed so the next
    ``git add -A`` cannot bring them back.
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
            '--',
            *RUNTIME_DIRS,
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
            root,
            f'Stop tracking {len(paths)} runtime file(s) .gitignore ignores',
        )
    logger.info('untrack_ignored: untracked %d ignored file(s)', len(paths))
    return len(paths)


class PurgeError(RuntimeError):
    """A step of the purge failed; nothing is marked done, so the next
    boot tries again."""


async def _history_holds_purge_paths(root: Path) -> bool:
    """Do any branch or tag still reach a commit holding a runtime dir?

    Branches and tags only — ``refs/original/`` is the rewrite's own backup
    and is the cleanup's to delete. A git failure raises rather than read
    as "clean", which would write the marker and never retry.
    """
    rc, out, err = await _git(
        root,
        'log',
        '--branches',
        '--tags',
        '-1',
        '--format=%H',
        '--',
        *PURGE_PATHS,
    )
    if rc:
        raise PurgeError(f'git log failed: {err.strip()}')
    return bool(out.strip())


async def _checked(root: Path, *args: str) -> str:
    rc, out, err = await _git(root, *args)
    if rc:
        raise PurgeError(f'git {args[0]} failed: {err.strip()[-500:]}')
    return out


async def _reclaim(root: Path) -> None:
    """Drop the rewrite's backups and let gc delete the old objects.

    ``refs/original/`` and the reflog both still reach the old history, and
    gc keeps everything they reach. Every step is checked: a gc that failed
    has reclaimed nothing, and must not be marked done.
    """
    refs = await _checked(
        root, 'for-each-ref', '--format=%(refname)', 'refs/original/'
    )
    for ref in refs.split():
        await _checked(root, 'update-ref', '-d', ref)
    await _checked(root, 'reflog', 'expire', '--expire=now', '--all')
    await _checked(root, 'gc', '--prune=now', '--quiet')


async def purge_ignored_history(root: Path, lock: asyncio.Lock) -> bool:
    """Rewrite the history without :data:`PURGE_PATHS`; True once done.

    Run after :func:`untrack_ignored`, so the current commit already lacks
    those paths and the rewrite's final checkout cannot touch them on disk.
    Holds the git lock throughout, so task auto-commits wait for it rather
    than racing the rewrite.

    Two stages, so that a failure anywhere is retried on the next boot
    rather than left half-done: the rewrite, then the reclaim. Between them
    :data:`PURGE_PENDING` is on disk — once the branches are rewritten the
    history probe reads clean, and without it a gc that failed would never
    run again while the old objects sat on disk. :data:`PURGE_MARKER` is
    written only after the reclaim succeeded. Raises :class:`PurgeError`
    on any failure.
    """
    git_dir = root / '.git'
    marker = git_dir / PURGE_MARKER
    pending = git_dir / PURGE_PENDING
    if not git_dir.is_dir() or marker.exists():
        return False
    async with lock:
        rc, _, _ = await _git(root, 'rev-parse', '--verify', '--quiet', 'HEAD')
        if rc:
            return False  # no commit yet — nothing to purge
        if not pending.exists():
            if not await _history_holds_purge_paths(root):
                marker.write_text('clean\n')
                return False
            logger.info(
                'purge_ignored_history: rewriting workspace history without %s',
                ', '.join(PURGE_PATHS),
            )
            # filter-branch refuses a dirty tree; commit what a task left.
            await _checked(root, 'add', '-A')
            await _commit_staged(
                root, 'Workspace changes before history cleanup'
            )
            rc, _, err = await _git(
                root,
                'filter-branch',
                '-f',
                '--prune-empty',
                # Tags move to the rewritten commits too; a tag left on the
                # old history would keep every old object alive through gc.
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
                # filter-branch moves refs only at its very end: a failure
                # leaves the history as it was, and the next boot retries.
                raise PurgeError(f'filter-branch failed: {err.strip()[-500:]}')
            pending.write_text('rewritten; reclaim pending\n')
        await _reclaim(root)
        marker.write_text('purged\n')
        pending.unlink(missing_ok=True)
    logger.info('purge_ignored_history: done')
    return True
