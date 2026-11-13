"""The central sync scheduler.

Every periodic "go ask the network / re-read the world" job in the
server runs through this one object: skills, knowledge, the ads skill
bundle, the provider-preset catalog, the image-model catalog, email
polling and the browser daemon reaper. Before this existed each of
them had its own idea of a schedule — a cooldown file, a
``while True: sleep()`` loop, an extra APScheduler registration — and
two of those forgot to record a probe that found nothing, so every
task launch paid a GitHub round trip.

Design (see docs/subsystems.md § Central sync scheduler):

* One registration list (:mod:`app.sync.jobs`), no import-time
  self-registration. Plugins add jobs through
  ``plugin_ctx.register_sync_job``.
* ``next_run_time`` comes from the ``sync_state`` table, not process
  start, so a restart neither resets the clock nor lets an idle server
  sit on stale content.
* One in-flight run per job, shared by the scheduled tick, ``trigger``
  and ``ready`` — N concurrent task launches can never run N
  concurrent syncs.
* The scheduled tick honours the job's AppSettings toggle; a manual
  run bypasses it.
* A run logs exactly one greppable line —
  ``sync:<name> reason=… status=…`` — which the e2e tests assert on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import logging
import time

from app.env_options import Options
from app.sync import state as sync_state
from app.sync.state import STATUS_FAILED, STATUS_SKIPPED
from app.telemetry import stable_install_id

logger = logging.getLogger('app.sync')

# Why a run is happening. ``tick`` is the scheduled one and is the only
# reason that honours the job's toggle.
REASON_TICK = 'tick'
REASON_MANUAL = 'manual'
REASON_EVENT = 'event'
REASON_BOOT = 'boot'
#: The other side of an integration told us, on a call it was answering
#: anyway, that what it ships has changed since we installed it — for
#: the ads skill, whose version rides every response header
#: (``app.ads_skill.note_service_version``). Not ``event``: that is a
#: person authorizing a store, a thing that happened. This is a thing
#: the client found out, and the log line has to tell them apart.
REASON_STALE = 'stale'

REASONS = (
    REASON_TICK,
    REASON_MANUAL,
    REASON_EVENT,
    REASON_BOOT,
    REASON_STALE,
)


class Cadence(Enum):
    """How often a job runs, in seconds."""

    FAST = 5 * 60
    HOURLY = 3600
    DAILY = 24 * 3600
    BIDAILY = 48 * 3600

    @property
    def jitter_window(self) -> int:
        """Seconds of deterministic per-install stagger for this tier.

        Only the slow tiers stagger: a FAST job's next run is five
        minutes out, so spreading the fleet over an hour would just
        make each install's own schedule less predictable.
        """
        return {
            Cadence.FAST: 0,
            Cadence.HOURLY: 0,
            Cadence.DAILY: 3600,
            Cadence.BIDAILY: 2 * 3600,
        }[self]


@dataclass(frozen=True)
class SyncContext:
    """Handed to a job's ``run``."""

    name: str
    reason: str
    force: bool = False


@dataclass(frozen=True)
class SyncResult:
    """What a run accomplished, and what to persist about it."""

    status: str
    version: str | None = None
    etag: str | None = None
    error: str | None = None
    skip_reason: str | None = None
    #: True when this run changed something only a restart picks up
    #: (gate code). False leaves the stored flag alone — it is sticky
    #: until boot clears it.
    needs_restart: bool = False
    detail: dict = field(default_factory=dict)

    @classmethod
    def skipped(cls, why: str) -> SyncResult:
        return cls(status=STATUS_SKIPPED, skip_reason=why)

    @classmethod
    def failed(cls, error: str) -> SyncResult:
        return cls(status=STATUS_FAILED, error=error)


#: Content pulled from the project's public GitHub ``main``.
SOURCE_GITHUB_MAIN = 'github_main'
#: Any other host outside this machine (models.dev, kie.ai, the ads
#: service).
SOURCE_EXTERNAL = 'external'

#: Which switch stops which source. ``VIBE_GITHUB_MAIN_SYNC=off`` is for
#: a dev checkout, a branch or CI, whose tree is ahead of ``main``;
#: ``VIBE_EXTERNAL_SYNC=off`` is for CI tests, which make no outside
#: calls except in the e2e that tests them.
_SWITCHES = {
    SOURCE_GITHUB_MAIN: Options.GITHUB_MAIN_SYNC,
    SOURCE_EXTERNAL: Options.EXTERNAL_SYNC,
}


@dataclass(frozen=True)
class SyncJob:
    """One registered job."""

    name: str
    cadence: Cadence
    run: Callable[[SyncContext], Awaitable[SyncResult]]
    #: AppSettings key gating scheduled runs (``None`` = always on).
    #: A missing row means on.
    toggle_key: str | None = None
    #: Precondition checked before every run — a capability question,
    #: NOT a user toggle. False → skip without moving ``last_ok_at``.
    when: Callable[[], Awaitable[bool]] | None = None
    #: Jobs whose in-flight run this one joins before starting.
    depends_on: tuple[str, ...] = ()
    #: Where the job reaches out to (``SOURCE_*``). Only these can be
    #: switched off; a job with none (mail, the daemon reaper) always
    #: ticks. The default is an outside host: a job that does not say
    #: (a plugin's) is stopped by ``VIBE_EXTERNAL_SYNC=off``, and only
    #: core's own work opts out by passing ``frozenset()``.
    sources: frozenset[str] = frozenset({SOURCE_EXTERNAL})


# ── Test knobs: cadence override + jitter ──────────────────────────


def jitter_multiplier() -> float:
    """Scale on the deterministic offset window.

    ``VIBE_SYNC_JITTER=0`` turns the stagger off entirely (what the
    test suite sets, so a compressed cadence fires on the nose). An
    unparseable value falls back to 1 — a typo must not silently
    concentrate every install back onto one instant.
    """
    raw = Options.SYNC_JITTER.get().strip()
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning('Ignoring unparseable VIBE_SYNC_JITTER=%r', raw)
        return 1.0
    return value if value >= 0 else 1.0


def offset_seconds(job_name: str, cadence: Cadence) -> int:
    """Deterministic per-install stagger for one job.

    ``hash(install_id, job_name) % window`` as designed, but the hash
    is SHA-256 rather than Python's ``hash()`` — the latter is salted
    per process, which would re-roll the offset on every restart and
    defeat the point. The same install + job always lands on the same
    second; different installs spread over the window.
    """
    window = int(cadence.jitter_window * jitter_multiplier())
    if window <= 0:
        return 0
    digest = hashlib.sha256(
        f'{stable_install_id()}:{job_name}'.encode()
    ).hexdigest()
    return int(digest[:8], 16) % window


def parse_cadence_overrides(raw: str | None = None) -> dict[str, float]:
    """``'skills=20,presets=30'`` → ``{'skills': 20.0, ...}``.

    Unparseable entries are dropped with a warning — one typo must not
    take every other override down with it.
    """
    text = raw if raw is not None else Options.SYNC_CADENCE_OVERRIDE.get()
    out: dict[str, float] = {}
    for chunk in (text or '').split(','):
        piece = chunk.strip()
        if not piece:
            continue
        name, sep, value = piece.partition('=')
        name = name.strip()
        try:
            if not sep or not name:
                raise ValueError('expected NAME=SECONDS')
            seconds = float(value)
            if seconds <= 0:
                raise ValueError('seconds must be > 0')
        except ValueError as exc:
            logger.warning(
                'Ignoring VIBE_SYNC_CADENCE_OVERRIDE entry %r (%s)',
                piece,
                exc,
            )
            continue
        out[name] = seconds
    return out


def cadence_seconds(name: str, cadence: Cadence) -> float:
    """The job's effective cadence, honouring the test override."""
    return parse_cadence_overrides().get(name, float(cadence.value))


def _is_strong(reason: str, force: bool) -> bool:
    """Whether this caller needs work done rather than a join.

    A scheduled tick is the clock talking; everything else — a user
    pressing Sync, the ads authorization event, any ``force`` run —
    asked for something to actually happen.
    """
    return reason != REASON_TICK or force


def _switched_off(option) -> bool:
    return option.get().strip().lower() in ('off', '0', 'false', 'no')


def warn_retired_switch() -> bool:
    """Log once at boot if the retired ``VIBE_SYNC_DISABLED`` is set.

    The variable no longer does anything; an install that still sets it
    expects its syncs off, so say which switches replaced it. Returns
    whether it warned.
    """
    raw = Options.SYNC_DISABLED_RETIRED.get().strip()
    if not raw:
        return False
    logger.warning(
        'VIBE_SYNC_DISABLED=%r is no longer read and has no effect. Use '
        'VIBE_GITHUB_MAIN_SYNC=off to stop pulls from GitHub main and '
        'VIBE_EXTERNAL_SYNC=off to stop calls to other outside hosts.',
        raw,
    )
    return True


def timers_armed(job: SyncJob) -> bool:
    """Whether ``job`` ticks on its own schedule.

    A job that reaches a switched-off source stays registered and still
    runs on demand (a manual run, an event) — it just does not tick. A
    job with no outside source (mail, the daemon reaper) always ticks:
    there is no switch for the product's own work.
    """
    return not any(_switched_off(_SWITCHES[s]) for s in job.sources)


# ── Scheduler ──────────────────────────────────────────────────────


class SyncScheduler:
    """Runs the registered :class:`SyncJob` s on one schedule.

    Bound to the server's existing APScheduler (passed in) — the
    user-visible Schedule jobs live there too and there must only ever
    be one scheduler.
    """

    def __init__(self, aps=None) -> None:
        self._aps = aps
        self._jobs: dict[str, SyncJob] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._tracked: set[asyncio.Task] = set()
        self._next: dict[str, datetime] = {}
        # Whether the newest run for a job was started by a strong
        # caller — the basis for "is this run good enough to join?".
        self._strong: dict[str, bool] = {}

    @property
    def aps(self):
        """The server's one APScheduler (app/scheduler/cron.py).

        Imported lazily and on purpose: that module pulls in the task
        queue, which launches tasks, which join sync runs — importing
        it at module load would close that loop before either side
        exists. Tests hand in their own scheduler instead.
        """
        if self._aps is None:
            from app.scheduler.cron import scheduler as aps  # noqa: PLC0415

            self._aps = aps
        return self._aps

    # ── registration ───────────────────────────────────────────

    @property
    def jobs(self) -> dict[str, SyncJob]:
        return dict(self._jobs)

    def has(self, name: str) -> bool:
        return name in self._jobs

    def names(self) -> list[str]:
        return list(self._jobs)

    async def failed_jobs(self) -> list[str]:
        """Registered jobs whose last run failed."""
        rows = await sync_state.get_all()
        return sorted(
            name
            for name, row in rows.items()
            if name in self._jobs and row.get('status') == STATUS_FAILED
        )

    def cadence_of(self, name: str) -> float:
        """The job's effective cadence in seconds (override included)."""
        job = self._require(name)
        return cadence_seconds(name, job.cadence)

    def is_running(self, name: str) -> bool:
        """Whether a run of ``name`` is in flight right now."""
        task = self._inflight.get(name)
        return task is not None and not task.done()

    async def register(self, job: SyncJob) -> None:
        """Register one job and schedule its next run.

        Duplicate names are a programming error — two modules claiming
        one job would silently share a state row — so this raises
        instead of replacing.
        """
        if job.name in self._jobs:
            raise ValueError(f'duplicate sync job {job.name!r}')
        self._jobs[job.name] = job
        # Registration keeps an overdue anchor overdue: the job fires
        # as soon as the scheduler runs ("overdue → run now"). A job whose
        # source is switched off has no timer — it runs only on demand.
        await self._schedule_next(job.name, clamp=False)
        logger.info(
            'sync job registered: %s cadence=%.0fs toggle=%s armed=%s',
            job.name,
            cadence_seconds(job.name, job.cadence),
            job.toggle_key or '(none)',
            timers_armed(job),
        )

    async def register_all(self, jobs) -> None:
        for job in jobs:
            await self.register(job)

    def reset(self) -> None:
        """Forget every registration (tests swap jobs in and out),
        timers included: a timer left behind would fire for a job this
        scheduler no longer knows."""
        # Every copy: a scheduler that has not started keeps pending jobs
        # in a list, where one id can appear more than once.
        for aps_job in self.aps.get_jobs():
            if aps_job.id.startswith(self._job_id('')):
                try:
                    aps_job.remove()
                except Exception:  # noqa: BLE001 — already fired
                    pass
        self._jobs.clear()
        self._next.clear()

    # ── running ────────────────────────────────────────────────

    async def trigger(
        self,
        name: str,
        reason: str = REASON_MANUAL,
        force: bool = False,
    ) -> SyncResult:
        """Run ``name`` now.

        A caller joins the run already in flight when that run will do
        what it asked for: a scheduled tick joins anything, and a strong
        request (``force``, or any reason but ``tick``) joins a run that
        is itself strong. What it will NOT do is be served by a weaker
        run — the in-flight one may be a probe that says "nothing
        changed", or a skip, and a Sync button press asked for work.
        That case waits the weak run out and takes one fresh run, which
        concurrent strong callers share (so "press Sync while the tick
        is running" is one extra sync, not one per press).

        Returns the run's result. A failure raises — the caller asked
        for this run and must hear about it (the scheduled tick, which
        nobody is waiting on, swallows it instead).
        """
        job = self._require(name)
        strong = _is_strong(reason, force)
        previous = self._inflight.get(name)
        if previous is not None and not previous.done():
            if not strong or self._strong.get(name):
                return await previous
            await self._wait_out(previous)
            current = self._inflight.get(name)
            if current is not previous:
                # A peer strong caller started the fresh run while we
                # waited — that is the run we share.
                return await current
        return await self._share_or_start(job, reason, force)

    @staticmethod
    async def _wait_out(task: asyncio.Task) -> None:
        """Wait for someone else's run without adopting its failure."""
        try:
            await task
        except Exception:  # noqa: BLE001 — not this caller's run
            pass

    async def ready(self, names=None, timeout: float | None = None) -> None:
        """Wait for in-flight runs of ``names`` (default: all).

        Never starts anything: this is the join point a task launch
        uses so it doesn't copy a skills tree that is mid-sync. Failures
        are not raised here — the caller didn't ask for a run.

        ``timeout`` bounds the wait: the applies are atomic, so waiting
        only buys freshness, and a download that hangs (a slow GitHub, a
        dead proxy) must not hold every task launch. On timeout we
        proceed on the tree we have.
        """
        wanted = self.names() if names is None else list(names)
        pending = [
            task
            for name in wanted
            if (task := self._inflight.get(name)) is not None
            and not task.done()
        ]
        if not pending:
            return
        if timeout is None:
            await asyncio.wait(pending)
            return
        _done, still = await asyncio.wait(pending, timeout=timeout)
        if still:
            logger.warning(
                'sync ready timed out for %s; launching on the current tree',
                ', '.join(sorted(wanted)),
            )

    async def drain(self) -> None:
        """Cancel and await every in-flight run (lifespan shutdown).

        APScheduler's ``shutdown(wait=False)`` does not do this: left
        running, a sync would write into a torn-down server and show up
        as "Task was destroyed but it is pending!".
        """
        tasks = [task for task in self._tracked if not task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — drained, nothing to do
                pass

    # ── internals ──────────────────────────────────────────────

    def _require(self, name: str) -> SyncJob:
        job = self._jobs.get(name)
        if job is None:
            raise KeyError(f'unknown sync job {name!r}')
        return job

    def _share_or_start(
        self, job: SyncJob, reason: str, force: bool
    ) -> asyncio.Task:
        """The one in-flight future for this job, starting one if idle.

        No await between the check and the store, so on a single event
        loop two concurrent callers always land on the same task.
        """
        task = self._inflight.get(job.name)
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(self._execute(job, reason, force))
        self._inflight[job.name] = task
        self._strong[job.name] = _is_strong(reason, force)
        self._tracked.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tracked.discard(task)
        # Retrieve the exception so a run nobody awaited (a tick whose
        # result was logged already) does not also log "Task exception
        # was never retrieved" at GC time. ``await task`` still raises
        # it for every awaiter — this only marks it as seen.
        if not task.cancelled() and task.exception() is not None:
            logger.debug('sync run raised; see its status=failed line')

    async def _execute(
        self, job: SyncJob, reason: str, force: bool
    ) -> SyncResult:
        started = time.monotonic()
        try:
            try:
                result = await self._run_job(job, reason, force)
            except Exception as exc:
                await sync_state.record(
                    job.name, status=STATUS_FAILED, error=str(exc)
                )
                self._log(job, reason, SyncResult.failed(str(exc)), started)
                raise
            await sync_state.record(
                job.name,
                status=result.status,
                error=result.error,
                version=result.version,
                etag=result.etag,
                needs_restart=result.needs_restart or None,
                detail=self._detail_to_store(result),
            )
            self._log(job, reason, result, started)
            return result
        finally:
            # Re-arm after EVERY attempt, including a skip or a failure:
            # the one-shot timer is gone once it fired, and a stale
            # anchor would otherwise make a job that never moves
            # ``last_ok_at`` re-fire in a tight loop.
            try:
                await self._schedule_next(job.name, clamp=True)
            except Exception:
                logger.exception('Failed to re-arm sync:%s', job.name)

    async def _run_job(
        self, job: SyncJob, reason: str, force: bool
    ) -> SyncResult:
        """Toggle, precondition, dependencies — then the job itself."""
        if reason == REASON_TICK and job.toggle_key:
            if not await sync_state.read_toggle(job.toggle_key):
                return SyncResult.skipped('toggle off')
        if job.when is not None and not await job.when():
            return SyncResult.skipped('precondition')
        if job.depends_on:
            await self.ready(job.depends_on)
        return await job.run(
            SyncContext(name=job.name, reason=reason, force=force)
        )

    @staticmethod
    def _detail_to_store(result: SyncResult) -> dict | None:
        """The run's bookkeeping, with its skip reason folded in."""
        if not result.detail and not result.skip_reason:
            return None
        detail = dict(result.detail)
        if result.skip_reason:
            detail['skip'] = result.skip_reason
        return detail

    @staticmethod
    def _log(
        job: SyncJob,
        reason: str,
        result: SyncResult,
        started: float,
    ) -> None:
        """The one greppable line per run."""
        parts = [
            f'sync:{job.name}',
            f'reason={reason}',
            f'status={result.status}',
        ]
        if result.skip_reason:
            parts.append(f'skip={result.skip_reason}')
        if result.version:
            parts.append(f'version={result.version[:12]}')
        for key, value in sorted(result.detail.items()):
            if isinstance(value, str | int | float | bool):
                parts.append(f'{key}={value}')
        if result.error:
            parts.append(f'error={result.error}')
        parts.append(f'duration={time.monotonic() - started:.1f}s')
        line = ' '.join(parts)
        if result.status == STATUS_FAILED:
            logger.warning(line)
        else:
            logger.info(line)

    # ── scheduling ─────────────────────────────────────────────

    async def next_run_time(self, name: str) -> datetime | None:
        """When ``name`` next fires, or ``None`` when unscheduled.

        Read from our own bookkeeping rather than APScheduler: a job
        added to a scheduler that has not started yet is still pending
        and has no ``next_run_time`` of its own, and the API must be
        able to answer before (and without) the scheduler running.
        """
        return self._next.get(name)

    def _job_id(self, name: str) -> str:
        return f'sync:{name}'

    async def _compute_next_run(self, name: str) -> datetime:
        """When this job should next fire.

        ``last_ok_at + cadence + offset`` — anchored on the DB, not on
        process start, so a restart neither resets the clock nor lets
        the schedule drift with uptime. A job that has never run fires
        NOW: a boot must not sit on stale remote content for the sake
        of the stagger (the offset, which only the DAILY/BIDAILY tiers
        have at all, then spaces out every run after it).
        """
        job = self._jobs[name]
        row = await sync_state.get_row(name)
        offset = await asyncio.to_thread(offset_seconds, name, job.cadence)
        now = datetime.now(UTC)
        if row is not None and row.last_ok_at:
            try:
                base = datetime.fromisoformat(row.last_ok_at)
            except ValueError:
                logger.warning(
                    'sync:%s has an unparseable last_ok_at %r; treating '
                    'it as never run',
                    name,
                    row.last_ok_at,
                )
                return now
            return base + timedelta(
                seconds=cadence_seconds(name, job.cadence) + offset
            )
        return now

    async def _schedule_next(self, name: str, *, clamp: bool) -> None:
        """(Re)arm the one-shot timer for ``name``.

        ``clamp`` is set after an attempt: the formula then points at
        the PAST whenever the job didn't move ``last_ok_at`` (skipped,
        or failed), and re-firing immediately would spin at full speed.
        Registration deliberately does not clamp — an overdue job must
        fire at once.
        """
        job = self._require(name)
        if not timers_armed(job):
            # Its source is switched off: the job exists and runs on
            # demand, it just never ticks by itself.
            self._next.pop(name, None)
            return
        next_run = await self._compute_next_run(name)
        if self._jobs.get(name) is not job:
            # Unregistered (or replaced) while we read the anchor: arming
            # now would leave a timer for a job nobody owns.
            return
        if clamp and next_run <= datetime.now(UTC):
            next_run = datetime.now(UTC) + timedelta(
                seconds=cadence_seconds(name, job.cadence)
            )
        self._next[name] = next_run
        self.aps.add_job(
            self._run_tick,
            trigger='date',
            run_date=next_run,
            id=self._job_id(name),
            kwargs={'name': name},
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            replace_existing=True,
        )

    async def _run_tick(self, name: str) -> None:
        """Entry point for the scheduled run: nobody awaits this."""
        try:
            await self.trigger(name, reason=REASON_TICK)
        except Exception:  # noqa: BLE001 — logged as status=failed
            logger.debug('sync:%s tick failed', name, exc_info=True)


#: The process-wide sync scheduler. It binds to the server's ONE
#: APScheduler on first use (the Schedule-table jobs live there too —
#: there must never be a second scheduler).
sync_scheduler = SyncScheduler()
