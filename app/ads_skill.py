"""Pulling the ads skill from the bound service.

The skill that tells an agent how to use an ads service ships from that
service, not from this repo. Two reasons, and they pull the same way:
the service grows endpoints on its own schedule and a released client
cannot be upgraded to match, and a deployment with no service bound has
no use for the skill at all.

**A pulled bundle is data. Executable files are refused.**
vibe-seller's skill format allows ``<skill>/gates/*.py``, which runs
inside this server. Accepting one from a remote source would make that
source a code supply-chain dependency of every deployment — so the
unpacker drops anything that is not documentation, and says so rather
than failing silently.

**An unreachable service is not an error.** The cached bundle keeps
working and task creation is never blocked on a version check: a service
hiccup must not stop a store that was working an hour ago.

**A change lands the day it ships, not the day after.** The daily
``ads_skill`` job is the backstop; :func:`note_service_version` is what
makes a refresh happen on the first ads call after the service changed,
because an agent working from a skill that documents paths which no
longer exist is the failure this whole path exists to avoid.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import tempfile
import time

from sqlalchemy import select

from app import ads_client
from app.database import async_session
from app.models.app_settings import AppSettings
from app.models.store import Store
from app.sync.scheduler import REASON_STALE, sync_scheduler
from app.workspace import skills_tree
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

SKILL_NAME = 'amazon-ads-api'
VERSION_KEY = 'ads_skill_version'

#: What may appear in a pulled bundle. Everything else is dropped.
ALLOWED_SUFFIXES = {'.md', '.txt', '.json', '.yaml', '.yml', '.csv'}

#: How long one remote version may sit between two refresh attempts.
#: Without it, a bundle download that keeps failing would turn **every**
#: agent call into a sync: the mismatch stays, so every answer re-asks,
#: and each ask costs a ``/skill/version`` round trip and a run that
#: fails again. One try per version per cooldown is enough — the daily
#: job remains the backstop either way.
STALE_REFRESH_COOLDOWN = 600.0

#: What this machine has installed, in memory; ``None`` = not read yet.
#: Answering "does the version the service just named differ from ours"
#: on every ads call must not be a database read, so the answer lives
#: here. :func:`sync_bundle` and :func:`remove` are the only writers, so
#: it cannot claim a version nobody installed; it is seeded from the
#: ``AppSettings`` row on first read, which is what makes it right after
#: a restart too.
_installed: str | None = None

#: When each remote version last asked for a refresh (monotonic).
_refreshed_at: dict[str, float] = {}

#: In-flight refreshes, so a scheduled task is not garbage-collected
#: mid-run (see app.routers.vision's ``_BACKGROUND`` for the idiom).
_pending: set = set()


def skill_dir() -> Path:
    return VIBE_SELLER_DIR / '.claude' / 'skills' / SKILL_NAME


def is_installed() -> bool:
    return (skill_dir() / 'SKILL.md').is_file()


async def sync_bundle(session, *, force: bool = False) -> dict:
    """Fetch the bundle if the service has a newer one."""
    remote = await ads_client.call(session, '/skill/version')
    version = str((remote or {}).get('version') or '')

    row = await session.get(AppSettings, VERSION_KEY)
    local = row.value if row else ''
    if version and version == local and is_installed() and not force:
        _set_installed(local)
        return {'updated': False, 'version': local}

    bundle = await ads_client.call(session, '/skill/bundle')
    files = (bundle or {}).get('files') or {}
    # Unpacking writes files and swaps directories: blocking work for
    # one thread, under the skills-tree lock — the task-workspace copy
    # must never catch the swap mid-flight (see
    # ``app.workspace.skills_tree``).
    async with skills_tree.hold():
        written, dropped = await asyncio.to_thread(_install, files)

    if row is None:
        session.add(AppSettings(key=VERSION_KEY, value=version))
    else:
        row.value = version
    await session.commit()
    # The tree on disk now answers to this version, so the memory that
    # answers "are we behind?" has to move with it — otherwise the next
    # call's header would keep matching the same old value and keep
    # asking for a refresh that already happened.
    _set_installed(version)

    if dropped:
        logger.warning(
            'ads skill bundle carried %d non-documentation file(s); '
            'dropped: %s',
            len(dropped),
            ', '.join(sorted(dropped)),
        )
    return {
        'updated': True,
        'version': version,
        'files': written,
        'dropped': sorted(dropped),
    }


def _install(files: dict[str, str]) -> tuple[int, list[str]]:
    """Write the bundle atomically, keeping only documentation.

    Staged beside the destination so the final move is a rename on the
    same filesystem: a half-written skill directory is one an agent could
    load mid-update.
    """
    destination = skill_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f'.{SKILL_NAME}-', dir=destination.parent)
    )
    backup = destination.with_name(destination.name + '.old')

    written = 0
    dropped: list[str] = []
    try:
        for relpath, content in files.items():
            safe = _safe_relpath(relpath)
            if (
                safe is None
                or Path(safe).suffix.lower() not in ALLOWED_SUFFIXES
            ):
                dropped.append(relpath)
                continue
            target = staging / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            written += 1

        shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            destination.rename(backup)
        staging.rename(destination)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)

    return written, dropped


def _safe_relpath(relpath: str) -> str | None:
    """Reject anything that could write outside the skill directory."""
    candidate = Path(relpath)
    if candidate.is_absolute():
        return None
    parts = candidate.parts
    if any(part in ('..', '') for part in parts):
        return None
    return str(candidate)


async def refresh(session) -> dict:
    """Sync the bundle, reporting failure rather than raising it.

    Called where a failure must not undo the work around it — binding
    succeeded even if the bundle did not arrive, and boot must not stop
    because the service is briefly down. The cached bundle keeps working
    and the next refresh tries again.
    """
    try:
        return await sync_bundle(session)
    except Exception as exc:
        logger.warning('ads skill refresh failed: %s', exc)
        return {'updated': False, 'error': str(exc)}


async def refresh_if_bound() -> dict | None:
    """Sync the bundle while a store is authorized; ``None`` otherwise.

    The one entry point for every periodic check — boot, and the skills
    sync that runs before tasks — so the service's version is compared
    on the same schedule as every other skill.

    A registered installation with no authorized store has no use for the
    skill, and revoking the last store removes it: checking only for the
    key would reinstall it at the next boot or skills sync.
    """
    try:
        async with async_session() as db:
            config = await ads_client.get_config(db)
            if not config.get('configured'):
                return None
            if not await _any_store_authorized(db):
                return None
            result = await refresh(db)
    except Exception as exc:
        logger.warning('ads skill refresh failed: %s', exc)
        return {'updated': False, 'error': str(exc)}
    if result.get('updated'):
        logger.info('ads skill bundle updated to %s', result.get('version'))
    return result


async def _any_store_authorized(db) -> bool:
    found = await db.execute(
        select(Store.id).where(Store.ads_authorized.is_(True)).limit(1)
    )
    return found.first() is not None


async def is_bound_and_authorized() -> bool:
    """Whether the skill applies here at all: bound service AND at
    least one authorized store.

    The sync scheduler's precondition for the ``ads_skill`` job — a
    capability question (like ``refresh_if_bound``'s own guard), not a
    user toggle, so a run is skipped without touching the service and
    without moving the job's ``last_ok_at``.
    """
    try:
        async with async_session() as db:
            config = await ads_client.get_config(db)
            if not config.get('configured'):
                return False
            return await _any_store_authorized(db)
    except Exception as exc:
        logger.warning('ads skill readiness check failed: %s', exc)
        return False


async def remove() -> None:
    """Drop the skill. Used when the service is unbound."""
    async with skills_tree.hold():
        await asyncio.to_thread(shutil.rmtree, skill_dir(), ignore_errors=True)
    # Nothing is on disk any more, so the memory must not keep claiming
    # a version. The rebind that brings the skill back is the thing that
    # installs it, with its own reason in the journal; what this line
    # buys is that "what we hold" is a fact rather than a habit.
    _set_installed('')


# ── a stale skill, noticed on any call ─────────────────────────────


def _set_installed(version: str) -> None:
    """Say what is installed. Only :func:`sync_bundle` and
    :func:`remove` call this, and they are the only things that write
    the skill."""
    global _installed
    _installed = version


async def installed_version() -> str:
    """The version this machine holds, from memory.

    The first read of a process goes to ``AppSettings``, because a
    restart must not forget what is on disk; every answer after that is
    the memory. A service that has never been bound reports ``''``.
    """
    global _installed
    if _installed is not None:
        return _installed
    async with async_session() as db:
        row = await db.get(AppSettings, VERSION_KEY)
    _installed = (row.value if row else '') or ''
    return _installed


def note_service_version(remote: str) -> None:
    """The service just answered a call naming its skill version.

    Called from :func:`app.ads_client.call` with the ``X-Skill-Version``
    header of any answer that came from the service — a refused call
    included, since an agent holding the wrong skill is exactly the
    agent that calls a path that has moved. When it differs from what
    this machine installed, the ``ads_skill`` job is triggered in the
    background:

    * **Never awaited here.** The caller is an agent's ads request, and
      a skill refresh is a download it must not sit behind.
    * **Never raises into it.** The refresh runs as a task of its own
      and logs what went wrong; the daily job remains the backstop.
    * **A running task keeps the skill it started with.** The copy into
      a task workspace happens once, at launch, under the skills-tree
      lock (``app.workspace.manager.prepare_task_dir``); this refresh
      swaps the tree under that same lock, so an agent mid-run is not
      re-read out from under itself. The **next launch** gets the new
      skill — that is the whole point, and the limit, of this path.

    The comparison is against the in-memory installed version, so a
    header costs no database read; the first news of a process does one,
    inside the task, off the request's way.

    **A machine with nothing installed is not behind.** This path answers
    "the service moved and we did not hear about it"; a version-less
    machine is answering "we have never pulled this", and that question
    already has three doors that say what they are doing — the bind
    (``reason=event``), the boot tick and the daily job. Taking it here
    would also steal the label: a store sync ``POST /me/stores`` is
    answered with the header, and the same request triggers the
    authorization run milliseconds later; whichever starts first owns
    the run's reason in the journal, so the operator would read
    ``reason=stale`` where a person authorized a store. That rule is
    applied in :func:`_refresh_for`, where the installed version is
    known, and not here, where it may still be unread.
    """
    if not remote or remote == _installed:
        # A service that said nothing, or said what we hold. Both are
        # answers, not news — and the empty machine is not decided here,
        # because here we may not know yet: ``_installed`` is ``None``
        # until the row is read, and that read happens in the task, on
        # the one side of this that can act on the answer.
        return
    now = time.monotonic()
    last = _refreshed_at.get(remote)
    if last is not None and now - last < STALE_REFRESH_COOLDOWN:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop to schedule on: nothing to refresh now
        return
    # Forget the versions this machine will not report again: entries
    # past their cooldown cannot suppress anything any more, and a
    # deployment sees a handful of them in its life.
    for known, at in list(_refreshed_at.items()):
        if now - at >= STALE_REFRESH_COOLDOWN:
            del _refreshed_at[known]
    _refreshed_at[remote] = now
    task = loop.create_task(_refresh_for(remote))
    # Held by reference until it finishes — a fire-and-forget task with
    # no owner is garbage-collected mid-run, and a refresh that quietly
    # vanishes is worse than one that fails.
    _pending.add(task)
    task.add_done_callback(lambda done: _refresh_finished(done, remote))


async def _refresh_for(remote: str) -> None:
    """The refresh a stale header asked for, off the request's way.

    Not ``force``: what ``sync_bundle(force=True)`` means is "download
    the bundle even though the version says we already hold it" — the
    question a repair asks, when the files may have been lost. Here the
    versions differ, and that comparison is what makes the run
    download; forcing would re-pull a bundle that arrived a second ago.
    ``_run_ads_skill`` does not forward ``ctx.force`` to
    ``sync_bundle`` at all, so the flag would only make the call say
    something the job then ignores.
    """
    installed = await installed_version()
    if not installed:
        # Nothing installed to be behind, which is what the memory said
        # before this read turned ``None`` into ``''``: the bind, the
        # boot tick or the daily job will say why it installs.
        return
    if remote == installed:
        # Somebody caught up while we were scheduled — a tick, a bind,
        # a peer call. Not a mismatch any more, so not a run.
        return
    # Imported here rather than at the top: jobs.py builds this
    # module's job and so imports it, so a module-level import would
    # close the loop between the two.
    from app.sync import jobs as sync_jobs  # noqa: PLC0415

    await sync_scheduler.trigger(sync_jobs.ADS_SKILL_JOB, reason=REASON_STALE)


def _refresh_finished(task: asyncio.Task, remote: str) -> None:
    """Drop the reference and say what a refresh nobody waited for did.

    ``trigger`` re-raises a failed run at its caller, and here the
    caller is a background task with no one to hear it. The scheduler
    has already logged the run's own ``sync:ads_skill`` line when the
    run happened; this line is for the cases that never reach a run at
    all — the job not registered, the version read failing — and it
    retrieves the exception, so asyncio does not also log one at
    garbage collection.
    """
    _pending.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            'ads skill refresh for service version %s failed: %s '
            '(the daily check retries it)',
            remote[:12],
            exc,
        )


def reset_for_tests() -> None:
    """Back to a cold machine. Tests only."""
    global _installed
    _installed = None
    _refreshed_at.clear()
    _pending.clear()
