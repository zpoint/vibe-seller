"""The ONE registration list for the central sync scheduler.

Core's jobs are defined here and nowhere else; plugins add theirs
through ``plugin_ctx.register_sync_job``. Nothing registers itself at
import time — :func:`register_jobs` is called from the app lifespan
(and from tests), so "what syncs, how often, and under what toggle" is
a list you can read.

Not in this list, deliberately:

* boot-time LOCAL package sync (``knowledge_sync.fetch`` /
  ``skills_sync.fetch``) — awaited in the lifespan before the skill
  gates load, not on a schedule;
* the stall / plan / finalize / idle / waiting / cleanup reapers and
  the task-queue tick — already APScheduler jobs (app/scheduler/);
* Dida365's on-use token refresh.
"""

from __future__ import annotations

import logging

import httpx

from app import ads_skill
from app.ai import provider_presets_sync
from app.plugins import registered_sync_jobs
from app.sync import state as sync_state
from app.sync.scheduler import (
    Cadence,
    SyncContext,
    SyncJob,
    SyncResult,
    SyncScheduler,
    sync_disabled,
)
from app.sync.state import STATUS_FAILED, STATUS_UNCHANGED, STATUS_UPDATED
from app.workspace.knowledge_sync import knowledge_sync
from app.workspace.skills_sync import skills_sync

logger = logging.getLogger('app.sync')

SKILLS_JOB = 'skills'
KNOWLEDGE_JOB = 'knowledge'
ADS_SKILL_JOB = 'ads_skill'
PRESETS_JOB = 'presets'
EMAIL_JOB = 'email'
REAPER_JOB = 'daemon_reaper'

#: AppSettings key behind the skills toggle (Settings → General).
#: Knowledge and provider presets have NO toggle — an explicit product
#: decision: their content is not something a user opts out of.
SKILLS_TOGGLE_KEY = 'skills_auto_sync_enabled'

#: Injectable so tests can hand a job a MockTransport-backed client
#: without patching httpx globally.
_client_factory = httpx.AsyncClient


# ── skills / knowledge: probe, then pull what moved ────────────────


async def _run_tree(ctx: SyncContext, name: str, manager) -> SyncResult:
    """One remote-tree sync: cheap probe first, download only if moved.

    The probe is a conditional GET (``If-None-Match`` on the stored
    validator) and a 304 answer is a SUCCESSFUL run: it moves
    ``last_ok_at`` so the next tick is a cadence away. Recording only
    the downloads was the old bug — once 24h passed, every task launch
    hammered the GitHub API again.
    """
    row = await sync_state.get_row(name)
    etag = row.etag if row is not None else None
    known = row.version if row is not None else None

    async with _client_factory() as client:
        probe = await manager.probe_remote(client, etag=etag)
        if not ctx.force:
            if probe['not_modified']:
                return SyncResult(
                    status=STATUS_UNCHANGED,
                    version=known,
                    etag=probe['etag'],
                )
            if probe['ok'] and probe['commit'] and probe['commit'] == known:
                return SyncResult(
                    status=STATUS_UNCHANGED,
                    version=probe['commit'],
                    etag=probe['etag'],
                )
            if not probe['ok']:
                return SyncResult.failed(
                    probe.get('error') or 'upstream probe failed'
                )
        outcome = await manager.do_remote_sync(client)

    if outcome.get('status') != 'success':
        return SyncResult.failed(outcome.get('error') or 'remote sync failed')
    copied = int(outcome.get('copied') or 0)
    return SyncResult(
        status=STATUS_UPDATED if copied else STATUS_UNCHANGED,
        version=probe['commit'],
        etag=probe['etag'],
        needs_restart=bool(outcome.get('needs_restart')),
        detail={
            'copied': copied,
            'skipped': int(outcome.get('skipped') or 0),
        },
    )


async def _run_skills(ctx: SyncContext) -> SyncResult:
    return await _run_tree(ctx, SKILLS_JOB, skills_sync)


async def _run_knowledge(ctx: SyncContext) -> SyncResult:
    return await _run_tree(ctx, KNOWLEDGE_JOB, knowledge_sync)


# ── ads skill: the bundle the bound service ships ──────────────────


async def _ads_ready() -> bool:
    return await ads_skill.is_bound_and_authorized()


async def _run_ads_skill(ctx: SyncContext) -> SyncResult:
    """Refresh the ads service's skill bundle.

    Replaces all four of its old entry points (boot task, the call
    inside the skills sync, the manual button, and the post-authorization
    hook in routers/ads.py) — the last of which now just calls
    ``trigger('ads_skill', reason='event', force=True)``.
    """
    outcome = await ads_skill.refresh_if_bound()
    if outcome is None:
        return SyncResult.skipped('precondition')
    if outcome.get('error'):
        return SyncResult.failed(str(outcome['error']))
    version = str(outcome.get('version') or '')
    if outcome.get('updated'):
        return SyncResult(
            status=STATUS_UPDATED,
            version=version or None,
            detail={'files': len(outcome.get('files') or [])},
        )
    return SyncResult(status=STATUS_UNCHANGED, version=version or None)


# ── provider presets: the model catalog from main ──────────────────


async def _run_presets(ctx: SyncContext) -> SyncResult:
    outcome = await provider_presets_sync.fetch_once(force=ctx.force)
    status = {
        'updated': STATUS_UPDATED,
        'unchanged': STATUS_UNCHANGED,
        'rejected': STATUS_FAILED,
        'failed': STATUS_FAILED,
    }.get(str(outcome.get('status')), STATUS_FAILED)
    return SyncResult(
        status=status,
        version=outcome.get('sha256'),
        etag=outcome.get('etag'),
        error=outcome.get('error'),
        detail={
            key: value
            for key, value in outcome.items()
            if key in ('presets', 'meta_refreshed') and value is not None
        },
    )


# ── the list ───────────────────────────────────────────────────────


def core_jobs() -> list[SyncJob]:
    """Every sync job core ships, in registration order."""
    return [
        SyncJob(
            name=SKILLS_JOB,
            cadence=Cadence.DAILY,
            run=_run_skills,
            toggle_key=SKILLS_TOGGLE_KEY,
        ),
        SyncJob(
            name=KNOWLEDGE_JOB,
            cadence=Cadence.DAILY,
            run=_run_knowledge,
        ),
        SyncJob(
            name=ADS_SKILL_JOB,
            cadence=Cadence.DAILY,
            run=_run_ads_skill,
            when=_ads_ready,
            depends_on=(SKILLS_JOB,),
        ),
        SyncJob(
            name=PRESETS_JOB,
            cadence=Cadence.BIDAILY,
            run=_run_presets,
        ),
    ]


async def register_jobs(scheduler: SyncScheduler) -> list[str]:
    """Register core + plugin jobs; returns the registered names.

    ``VIBE_SYNC_DISABLED=1`` registers nothing — what the test suite
    sets, so nothing under tests/ ever reaches GitHub or an ads
    service on its own.
    """
    if sync_disabled():
        logger.info('sync scheduler: no jobs registered (VIBE_SYNC_DISABLED)')
        return []
    jobs = [*core_jobs(), *registered_sync_jobs()]
    await scheduler.register_all(jobs)
    logger.info('sync scheduler: registered %s', [j.name for j in jobs])
    return [job.name for job in jobs]
