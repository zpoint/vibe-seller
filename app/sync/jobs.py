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

from app import ads_skill, vision, vision_catalog_sync
from app.ai import model_pricing_sync, provider_presets_sync
from app.browser.daemon_reaper import reap_orphaned_daemons
from app.plugins import registered_sync_jobs
from app.scheduler.email_sync import sync_all_email_accounts
from app.sync import state as sync_state
from app.sync.scheduler import (
    SOURCE_EXTERNAL,
    SOURCE_GITHUB_MAIN,
    Cadence,
    SyncContext,
    SyncJob,
    SyncResult,
    SyncScheduler,
    warn_retired_switch,
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
VISION_JOB = 'vision_catalog'
PRICING_JOB = 'model_pricing'

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
            'deleted': int(outcome.get('deleted') or 0),
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
            # sync_bundle reports a COUNT of files written.
            detail={'files': int(outcome.get('files') or 0)},
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


# ── vision catalog: image models, live prices, availability ───────


async def _vision_ready() -> bool:
    """Only an install that can generate images keeps the catalog
    fresh: without a kie.ai key there is nothing to price or offer, and
    the fake (offline) mode must never reach the network."""
    return vision.get_kie_api_key() is not None and not vision.is_fake()


async def _run_vision(ctx: SyncContext) -> SyncResult:
    api_key = vision.get_kie_api_key()
    if not api_key:
        return SyncResult.skipped('precondition')
    async with _client_factory() as client:
        steps = await vision_catalog_sync.sync_once(
            api_key, client, force=ctx.force
        )
    statuses = {name: step.get('status') for name, step in steps.items()}
    detail: dict = dict(statuses)
    for name, step in steps.items():
        for key in ('priced', 'hidden', 'families'):
            if key in step:
                detail[f'{name}_{key}'] = step[key]
    errors = [
        f'{name}: {step["error"]}'
        for name, step in steps.items()
        if step.get('error')
    ]
    if all(s in ('failed', 'rejected') for s in statuses.values()):
        return SyncResult(
            status=STATUS_FAILED, error='; '.join(errors), detail=detail
        )
    updated = any(s == 'updated' for s in statuses.values())
    return SyncResult(
        status=STATUS_UPDATED if updated else STATUS_UNCHANGED,
        error='; '.join(errors) or None,
        detail=detail,
    )


# ── model pricing: per-token rates + context windows (models.dev) ─


async def _run_pricing(ctx: SyncContext) -> SyncResult:
    outcome = await model_pricing_sync.fetch_once()
    status = {
        'updated': STATUS_UPDATED,
        'unchanged': STATUS_UNCHANGED,
    }.get(str(outcome.get('status')), STATUS_FAILED)
    return SyncResult(
        status=status,
        version=outcome.get('sha256'),
        error=outcome.get('error'),
        detail={
            key: outcome[key]
            for key in ('providers', 'models')
            if key in outcome
        },
    )


# ── email: one pass over the enabled accounts ──────────────────────


async def _run_email(ctx: SyncContext) -> SyncResult:
    """Poll every enabled email account.

    Which accounts are enabled is the per-account flag the sync has
    always read — that is a property of the account, not a toggle on
    this job.
    """
    await sync_all_email_accounts()
    return SyncResult(status=STATUS_UPDATED, detail={'accounts': 'all'})


# ── daemon reaper: orphaned browser-use daemons ───────────────────


async def _run_reaper(ctx: SyncContext) -> SyncResult:
    killed = await reap_orphaned_daemons()
    return SyncResult(status=STATUS_UPDATED, detail={'reaped': killed})


# ── the list ───────────────────────────────────────────────────────


def core_jobs() -> list[SyncJob]:
    """Every sync job core ships, in registration order."""
    return [
        SyncJob(
            name=SKILLS_JOB,
            cadence=Cadence.DAILY,
            run=_run_skills,
            toggle_key=SKILLS_TOGGLE_KEY,
            sources=frozenset({SOURCE_GITHUB_MAIN}),
        ),
        SyncJob(
            name=KNOWLEDGE_JOB,
            cadence=Cadence.DAILY,
            run=_run_knowledge,
            sources=frozenset({SOURCE_GITHUB_MAIN}),
        ),
        SyncJob(
            name=ADS_SKILL_JOB,
            cadence=Cadence.DAILY,
            run=_run_ads_skill,
            when=_ads_ready,
            depends_on=(SKILLS_JOB,),
            sources=frozenset({SOURCE_EXTERNAL}),
        ),
        SyncJob(
            name=PRESETS_JOB,
            cadence=Cadence.BIDAILY,
            run=_run_presets,
            sources=frozenset({SOURCE_GITHUB_MAIN}),
        ),
        SyncJob(
            name=VISION_JOB,
            cadence=Cadence.DAILY,
            run=_run_vision,
            when=_vision_ready,
            sources=frozenset({SOURCE_GITHUB_MAIN, SOURCE_EXTERNAL}),
        ),
        SyncJob(
            name=PRICING_JOB,
            cadence=Cadence.DAILY,
            run=_run_pricing,
            sources=frozenset({SOURCE_EXTERNAL}),
        ),
        SyncJob(
            name=EMAIL_JOB,
            cadence=Cadence.FAST,
            run=_run_email,
            sources=frozenset(),
        ),
        SyncJob(
            name=REAPER_JOB,
            cadence=Cadence.FAST,
            run=_run_reaper,
            sources=frozenset(),
        ),
    ]


async def register_jobs(scheduler: SyncScheduler) -> list[str]:
    """Register core + plugin jobs; returns the registered names.

    Every job is registered, so a manual run, an event trigger and
    ``GET /api/sync/status`` always work. A switched-off source only
    keeps its jobs from ticking (see ``scheduler.timers_armed``).
    """
    warn_retired_switch()
    jobs = [*core_jobs(), *registered_sync_jobs()]
    await scheduler.register_all(jobs)
    logger.info('sync scheduler: registered %s', [j.name for j in jobs])
    return [job.name for job in jobs]
