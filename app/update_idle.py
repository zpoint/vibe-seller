"""When an update may restart the server by itself — and when it must ask.

A small update that installs itself is only polite while nobody is
using the server. **Idle** here means all three of:

- no task agent is live right now (the same set a shutdown would kill,
  ``app.ai.claude_backend_manager.agent_manager.live_agent_count``);
- no task is queued or will start without a human first doing nothing —
  see :data:`BUSY_TASK_STATUSES` for exactly which states that is and
  why;
- no schedule fires inside the next :data:`SCHEDULE_LOOKAHEAD_SECONDS`,
  because a server that restarts a minute before 09:00 turns a daily
  report into a task that starts late or not at all.

Idle is re-checked every :data:`IDLE_CHECK_INTERVAL_SECONDS` while an
update sits ready. A machine that is never idle — a store running tasks
around the clock — must not lose the update either: after
:data:`READY_POPUP_AFTER_SECONDS` the admin gets the popup #244 built
("please restart") and applies it by hand. There is deliberately **no
forced drain**: nothing here ever interrupts a running task on its own.

The ready clock is kept in a file, not in memory, because the wait can
outlast the process that started it: a server that restarts for an
unrelated reason at hour 23 would otherwise reset the timer and the
admin would never be asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import time
from typing import Any

from sqlalchemy import func, select

from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

#: How often a ready update looks for its moment. One cheap query per
#: interval; an update landing within a minute of the machine going
#: quiet is soon enough.
IDLE_CHECK_INTERVAL_SECONDS = 60

#: A schedule inside this window counts as busy even though no task
#: exists yet — the cron job is about to create one.
SCHEDULE_LOOKAHEAD_SECONDS = 10 * 60

#: How long a ready-but-never-idle update waits before the admin is
#: asked to restart by hand.
READY_POPUP_AFTER_SECONDS = 24 * 3600

#: Task states that mean the server is in somebody's work. Every one of
#: them either holds an agent now (RUNNING, DESIGNING) or will start one
#: without anyone first having to be idle: QUEUED/PENDING wait on the
#: dispatcher, PLANNED waits only on an approval that could arrive at
#: any second, and WAITING can be woken by an inbound email or a channel
#: message at any second.
#:
#: COMPLETED and FAILED are the only states excluded — they hold nothing
#: and start nothing. The cost of the conservative read is a busy store
#: waiting 24 h for a manual restart; the cost of the loose one is a
#: task killed mid-flight, which is why it is the conservative one.
BUSY_TASK_STATUSES = tuple(
    status
    for status in (
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.DESIGNING,
        TaskStatus.PLANNED,
        TaskStatus.RUNNING,
        TaskStatus.WAITING,
    )
)

#: Job ids APScheduler holds for user schedules (``cron.py``:
#: ``schedule_{id}``). The other jobs are housekeeping — email sync, the
#: reapers — and a restart delays them by seconds, which is their normal
#: behaviour under any deploy.
SCHEDULE_JOB_PREFIX = 'schedule_'


@dataclass(frozen=True)
class Verdict:
    """Why the server is, or is not, free to restart."""

    idle: bool
    reason: str | None = None


def schedule_due_within(
    seconds: float, now: datetime | None = None
) -> str | None:
    """The id of a schedule job due inside *seconds*, else None.

    Reads APScheduler's in-memory job list (no I/O, no await): each
    job's ``next_run_time`` is maintained by the scheduler itself, so
    this says when the NEXT fire is, including one added a moment ago.
    """
    # Late import: app.scheduler.cron pulls in the task runner, and this
    # module is imported by app.auto_update at server start.
    from app.scheduler.cron import scheduler  # noqa: PLC0415

    for job in scheduler.get_jobs():
        if not str(job.id).startswith(SCHEDULE_JOB_PREFIX):
            continue
        when = getattr(job, 'next_run_time', None)
        if when is None:  # paused
            continue
        here = now or datetime.now(when.tzinfo)
        if (when - here).total_seconds() <= seconds:
            return str(job.id)
    return None


async def busy_task_count() -> int:
    """Tasks queued, running, or waiting to start.

    ``app.database`` is imported HERE, not at module top. The chain
    ``app.database`` → ``app.knowledge_contract`` → ``app.ai`` → … →
    ``app.sync.state`` → ``app.database`` is already a cycle that only
    works because ``app.database`` has finished by the time the last
    link runs; a module that imports it at the top of a file the server
    loads early (``app.main`` → ``app.auto_update`` → here) enters it
    first and dies with "cannot import name 'async_session' from
    partially initialized module" — the server then does not start at
    all, which is what the installed-build e2e caught on a real machine.
    """
    from app.database import async_session  # noqa: PLC0415 — the cycle above
    from app.models.task import Task  # noqa: PLC0415 — same

    async with async_session() as db:
        result = await db.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_(BUSY_TASK_STATUSES))
        )
        return int(result.scalar() or 0)


async def evaluate(agents: Any = None) -> Verdict:
    """Whether the server may restart itself right now.

    *agents* is the agent manager (anything with ``live_agent_count``);
    the caller passes the singleton so the policy stays testable.
    """
    live = agents.live_agent_count() if agents is not None else 0
    if live:
        return Verdict(False, f'{live} task agent(s) running')
    queued = await busy_task_count()
    if queued:
        return Verdict(False, f'{queued} task(s) queued or waiting to start')
    due = schedule_due_within(SCHEDULE_LOOKAHEAD_SECONDS)
    if due:
        return Verdict(False, f'schedule {due} fires within the window')
    return Verdict(True)


async def is_idle(agents: Any = None) -> bool:
    return (await evaluate(agents)).idle


# ── the ready clock ──────────────────────────────────────────────────


def ready_file() -> Any:
    """``<VIBE_HOME>/updates/update-ready.json``.

    Late import: ``app.config`` creates the data directory at import
    time, a side effect that belongs to server boot rather than to
    every importer of this module.
    """
    from app.config import VIBE_SELLER_DIR  # noqa: PLC0415

    return VIBE_SELLER_DIR / 'updates' / 'update-ready.json'


def _read_ready() -> dict:
    try:
        doc = json.loads(ready_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def note_ready(version: str, path: str, now: float | None = None) -> dict:
    """Record that *version* became ready by way of *path*.

    Idempotent per (version, path): the update cycle re-runs every six
    hours and must not restart the 24 h clock each time, or a busy
    server would never reach the popup. A change of either — a new
    target, or the same target now arriving by pip instead of the
    installer — starts a fresh clock, because that is a different
    decision with different consequences.
    """
    prior = _read_ready()
    since = now if now is not None else time.time()
    if (
        prior.get('version') == version
        and prior.get('path') == path
        and isinstance(prior.get('since'), int | float)
    ):
        since = float(prior['since'])
    else:
        doc = {
            'version': version,
            'path': path,
            'since': since,
            'since_utc': time.strftime(
                '%Y-%m-%dT%H:%M:%SZ', time.gmtime(since)
            ),
        }
        ready_file().parent.mkdir(parents=True, exist_ok=True)
        ready_file().write_text(
            json.dumps(doc, indent=2, sort_keys=True), encoding='utf-8'
        )
    return {
        'version': version,
        'path': path,
        'since': since,
    }


def clear_ready() -> None:
    try:
        ready_file().unlink()
    except OSError:
        pass


def waited_too_long(record: dict | None, now: float | None = None) -> bool:
    """Whether the admin should be asked to restart by hand."""
    if not record:
        return False
    since = record.get('since')
    if not isinstance(since, int | float):
        return False
    return ((now if now is not None else time.time()) - since) >= (
        READY_POPUP_AFTER_SECONDS
    )


def popup_due(
    path: str, ready_since: float | None, now: float | None = None
) -> bool:
    """Whether the ready state should put a modal in front of the admin.

    The installer path pops as soon as it is ready — that is #244's
    behaviour and it is the only way that update ever gets applied.
    The pip path stays quiet on the sidebar for a day, because it will
    apply itself the moment the machine is idle and a popup would be a
    question nobody needs to answer.
    """
    if path != 'pip':
        return True
    return waited_too_long({'since': ready_since}, now)
