"""Schedule-state endpoints for scheduled-task agents.

Split out of ``app/routers/tasks.py`` so the task router can stay
under the repo's 800-line-per-file cap. These endpoints back the
``vibe_seller_get_schedule_state`` / ``vibe_seller_set_schedule_state``
MCP tools used by scheduled tasks to hand off cursor values between
runs.
"""

import asyncio
from datetime import UTC, datetime
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.email.db import get_new_emails_since, init_email_db
from app.models.email_account import EmailAccount
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.schedule_state import NO_STORE_SCOPE, ScheduleState
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.models.user import User

# Canonical cursor key for the scheduled email sweep. Kept in sync
# with scheduled_pretask.md and _EPOCH_TYPED_KEYS below.
_EMAIL_WATERMARK_KEY = 'email_watermark'
# Default first-run lookback when no cursor exists yet.
_DEFAULT_LOOKBACK_HOURS = 24

# Server-managed floor for the email watermark. Written ONLY by
# ``get_new_emails`` (never by the agent) and set to the highest
# ``next_watermark`` the server has emitted for this schedule+store.
# ``set_schedule_state('email_watermark', v)`` refuses to persist a
# value below it — see the incident write-up in
# ``tests/e2e/test_email_watermark_e2e.py``: a model that hand-derived
# the watermark from an email's ``Date`` header (a value BELOW the
# ``fetched_at`` axis the reader filters on) passed the coarse
# ±90-day window check, so run 2 re-swept the already-processed email
# and leaked its body. The floor moves that contract from prose into
# code: the cursor cannot regress below what ``get_new_emails`` showed
# the agent, and — for a store that actually has linked email accounts
# — cannot be set at all until ``get_new_emails`` has run.
_EMAIL_WATERMARK_FLOOR_KEY = '_email_watermark_floor'
# Keys beginning with this prefix are server-managed and rejected on
# the agent-facing write path (so an agent cannot lower the floor).
_RESERVED_KEY_PREFIX = '_'

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


class SetScheduleStateRequest(BaseModel):
    # Required, non-empty. Weak models interpret optional/null as
    # "valid default" and get stuck in a null/null/null circuit-breaker
    # loop. Forcing the field keeps the MCP contract unambiguous.
    #
    # `strip_whitespace=True` catches the whitespace-only value case
    # (`"   "` → `""`); `min_length=1` then fires 422 at the pydantic
    # boundary. Downstream code can trust `body.value` to be a
    # non-empty, non-whitespace string.
    value: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]


_SCHEDULE_STATE_KEY_RE = re.compile(r'^[A-Za-z0-9_.\-]{1,64}$')

# Typed slots for canonical keys. Agents hand-write SQL WHERE
# clauses with these values — string lex comparison is fragile
# around timezone / microsecond trailing bytes, so we force the
# wire format to be a numeric type the agent cannot accidentally
# truncate or reformat.
_EPOCH_SECONDS_RE = re.compile(r'^[0-9]{1,15}$')
_TYPED_VALUE_PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    'email_watermark': (
        _EPOCH_SECONDS_RE,
        (
            'value for key=email_watermark must be a unix epoch '
            'timestamp in seconds, as an integer string (e.g. '
            '"1776441057"). String timestamps are rejected because '
            'they are unsafe under lexicographic SQL comparison.'
        ),
    ),
}

# Sanity window for epoch-typed cursors. Some models translate an
# ISO date into epoch in their head and silently land a year off
# (observed: MiniMax-M2.7 wrote a 2025 epoch for a 2026 email).
# The cursor semantically tracks the latest *processed* item, so
# it should land within reaching distance of "now" — a multi-month
# stale value is almost always a model error, not a real cursor.
# Reject obviously-wrong epochs at the boundary so the agent has
# to retry instead of poisoning the next run's read.
_EPOCH_PAST_WINDOW_DAYS = 90
_EPOCH_FUTURE_WINDOW_HOURS = 1
# Re-use the same window for any cursor we mark as "epoch-typed"
# below; today only `email_watermark` qualifies, but adding a
# `last_processed_epoch` later should inherit the same guard.
_EPOCH_TYPED_KEYS: frozenset[str] = frozenset({'email_watermark'})


def _validate_schedule_state_key(key: str) -> None:
    """Reject keys that could confuse URL routing or the sqlite PK.

    Keeps keys to the character set actually expected of agents —
    ascii letters and digits plus ``_``, ``-``, ``.`` — so callers
    cannot sneak in ``/``, ``#``, ``%``, or whitespace that would
    break routing or matching.
    """
    if not _SCHEDULE_STATE_KEY_RE.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail=(
                'Invalid key: must match [A-Za-z0-9_.-] and be 1-64 chars.'
            ),
        )


def _reject_reserved_key(key: str) -> None:
    """Reject agent access to server-managed (``_``-prefixed) keys.

    Applied on BOTH the read and write paths so an agent can neither
    read the email-watermark floor nor lower it. The server's own
    floor read/write goes through ``db`` directly, not these
    endpoints, so it is unaffected.
    """
    if key.startswith(_RESERVED_KEY_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=(
                f"keys starting with '{_RESERVED_KEY_PREFIX}' are reserved "
                'for server-managed cursors and cannot be read or written '
                'via the schedule-state tools.'
            ),
        )


def _validate_typed_value(key: str, value: str) -> None:
    """Reject values that do not match a canonical key's format.

    Only enforced for keys registered in ``_TYPED_VALUE_PATTERNS``;
    other keys keep the generic KV semantics. For epoch-typed keys
    the value must additionally land within a sane window around
    "now" — see ``_EPOCH_TYPED_KEYS``.
    """
    typed = _TYPED_VALUE_PATTERNS.get(key)
    if typed is None:
        return
    pattern, detail = typed
    if not pattern.fullmatch(value):
        raise HTTPException(status_code=400, detail=detail)
    if key in _EPOCH_TYPED_KEYS:
        now_epoch = int(datetime.now(UTC).timestamp())
        epoch = int(value)
        floor = now_epoch - _EPOCH_PAST_WINDOW_DAYS * 86400
        ceiling = now_epoch + _EPOCH_FUTURE_WINDOW_HOURS * 3600
        if epoch < floor or epoch > ceiling:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'value for key={key} ({value}) is outside the '
                    f'sane window [now-{_EPOCH_PAST_WINDOW_DAYS}d, '
                    f'now+{_EPOCH_FUTURE_WINDOW_HOURS}h]. Likely a '
                    'mentally-translated ISO date with the year off; '
                    'select the epoch in your SQL via '
                    "CAST(strftime('%s', date) AS INTEGER) AS epoch "
                    'and persist that column verbatim — never '
                    'translate ISO → epoch in your head.'
                ),
            )


async def _load_scheduled_task(db: AsyncSession, task_id: str) -> Task:
    """Look up ``task_id`` and reject non-scheduled tasks."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if not task.schedule_id:
        raise HTTPException(
            status_code=400,
            detail=(
                'This task has no schedule_id; schedule-state tools are'
                ' only available to scheduled tasks.'
            ),
        )
    return task


async def _store_has_email_accounts(
    db: AsyncSession, store_id: str | None
) -> bool:
    """True if ``store_id`` has at least one linked email account.

    Gates the watermark-floor enforcement: only a store that actually
    runs email sweeps must go through ``get_new_emails`` before writing
    ``email_watermark``. Stores with no linked account (and the many
    tests that use ``email_watermark`` as a generic KV sample) keep the
    plain typed-value semantics.
    """
    if not store_id:
        return False
    row = await db.execute(
        select(StoreEmailLink.id)
        .where(StoreEmailLink.store_id == store_id)
        .limit(1)
    )
    return row.first() is not None


async def _get_watermark_floor(
    db: AsyncSession, schedule_id: str, store_scope: str
) -> int | None:
    """Return the server-managed email-watermark floor, or None.

    None means ``get_new_emails`` has not run yet for this
    schedule+store, so no cursor has been shown to the agent.
    """
    state = await db.get(
        ScheduleState,
        (schedule_id, store_scope, _EMAIL_WATERMARK_FLOOR_KEY),
    )
    if state is None or not state.value:
        return None
    # Written only by the server as ``str(int)`` — int() is safe.
    return int(state.value)


class RegisterFinalizeRequest(BaseModel):
    # Non-empty NL instruction for the gather/reduce step. Same
    # strip+min_length contract as schedule-state so the MCP boundary
    # 422s on null/empty/whitespace rather than registering a blank
    # finalize the reaper would then fire with no guidance.
    description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]


@router.post('/{task_id}/register-finalize')
async def register_finalize(
    task_id: str,
    body: RegisterFinalizeRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Register the parent finalize step for this task's schedule.

    Backs the ``vibe_seller_register_finalize`` MCP tool. The
    plan-phase agent calls it when it judges the task needs a
    cross-store gather/combine AFTER all per-store children finish.
    Writes ``Schedule.finalize_description`` — the existing
    ``finalize_reaper`` then fires one finalize task per batch once
    every child is terminal.

    Only valid for all-stores fanout schedules (``store_id IS NULL``
    + ``phase_mode='fanout'``): a single-store or single-phase
    schedule has no batch to reduce, so registering a finalize would
    silently never fire.
    """
    task = await _load_scheduled_task(db, task_id)
    sched = await db.get(Schedule, task.schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail='Schedule not found')
    if sched.store_id is not None or sched.phase_mode != PhaseMode.FANOUT:
        raise HTTPException(
            status_code=400,
            detail=(
                'register-finalize only applies to all-stores fanout '
                'schedules (the finalize step reduces a per-store batch). '
                f'This schedule is store_id={sched.store_id!r}, '
                f'phase_mode={sched.phase_mode!r}.'
            ),
        )
    sched.finalize_description = body.description
    sched.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    return {'ok': True, 'finalize_description': body.description}


@router.get('/{task_id}/schedule-state/{key}')
async def get_schedule_state(
    task_id: str,
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return this schedule's persisted value for `key`, or null.

    Scope is resolved server-side from the calling task's
    `schedule_id` — the id is never included in the response so
    the MCP tool output cannot expose it to the agent.
    """
    _validate_schedule_state_key(key)
    _reject_reserved_key(key)
    task = await _load_scheduled_task(db, task_id)
    # Scope by the calling task's store_id so fanout siblings each
    # see their own cursor. NO_STORE_SCOPE ('') is the sentinel for
    # tasks without a store (e.g. email-sweep schedules) — see the
    # ScheduleState model docstring for the full incident report.
    store_scope = task.store_id or NO_STORE_SCOPE
    state = await db.get(ScheduleState, (task.schedule_id, store_scope, key))
    # Reference timestamp so the agent has a concrete "now" anchor
    # before deciding what epoch to persist — surfaced for
    # epoch-typed cursors to make the year-off failure mode harder
    # (the agent can compare its candidate value against this and
    # notice "wait, mine is 1y less than now"). Cheap to include
    # for non-epoch keys too, kept for parity.
    now_epoch = int(datetime.now(UTC).timestamp())
    if state is None:
        # Null hit — often the agent hallucinated a key variant
        # (e.g. ``last_email_watermark`` instead of the canonical
        # ``email_watermark`` from ``scheduled_pretask.md``). Surface
        # the set of keys that ALREADY have values on this schedule
        # so the agent can self-correct on the next call. Empty list
        # is unambiguously "first run, nothing persisted yet".
        existing = await db.execute(
            select(ScheduleState.key).where(
                ScheduleState.schedule_id == task.schedule_id,
                ScheduleState.store_id == store_scope,
            )
        )
        # Omit server-managed cursors (e.g. the email-watermark floor)
        # from the hint list. Direct reads of them are also rejected
        # (see _reject_reserved_key), so the agent never sees or mimics
        # them by either path.
        known_keys = sorted(
            row[0]
            for row in existing.all()
            if not row[0].startswith(_RESERVED_KEY_PREFIX)
        )
        return {
            'key': key,
            'value': None,
            'updated_at': None,
            'updated_by_task_id': None,
            # New field: other keys that DO have values for this
            # schedule. If your target cursor is listed here under a
            # different name, you hallucinated — retry GET with the
            # listed key. Empty list means no prior run has written
            # any cursor yet.
            'other_known_keys': known_keys,
            'now_epoch': now_epoch,
        }
    return {
        'key': state.key,
        'value': state.value,
        'updated_at': state.updated_at,
        'updated_by_task_id': state.updated_by_task_id,
        'now_epoch': now_epoch,
    }


@router.put('/{task_id}/schedule-state/{key}')
async def set_schedule_state(
    task_id: str,
    key: str,
    body: SetScheduleStateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Upsert this schedule's persisted value for `key`.

    Uses SQLite ``INSERT ... ON CONFLICT DO UPDATE`` so concurrent
    manual triggers cannot race on the primary-key insert and
    raise IntegrityError. Response omits ``schedule_id`` so the
    MCP tool result never leaks it to the agent.
    """
    _validate_schedule_state_key(key)
    _reject_reserved_key(key)
    # `body.value` is already stripped + non-empty — enforced by
    # SetScheduleStateRequest's StringConstraints (pydantic 422s on
    # null / missing / empty / whitespace-only at the boundary).
    _validate_typed_value(key, body.value)
    task = await _load_scheduled_task(db, task_id)
    store_scope = task.store_id or NO_STORE_SCOPE

    # Email-watermark cursor authority: for a store that actually runs
    # email sweeps, the watermark may only advance through what
    # get_new_emails showed the agent. This makes the run-1→run-2 leak
    # (persisting a Date-header epoch below the fetched_at axis)
    # impossible from this surface — see _EMAIL_WATERMARK_FLOOR_KEY.
    if key == _EMAIL_WATERMARK_KEY and await _store_has_email_accounts(
        db, task.store_id
    ):
        floor = await _get_watermark_floor(db, task.schedule_id, store_scope)
        if floor is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    'This store has linked email account(s): call '
                    'vibe_seller_get_new_emails first and persist its '
                    '`next_watermark` verbatim. The watermark must come '
                    'from that tool (it keys off the fetched_at axis the '
                    'reader filters on) — do not set email_watermark by '
                    'hand or from an email Date header.'
                ),
            )
        if int(body.value) < floor:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'value {body.value} is below the email-watermark '
                    f'floor {floor} — the newest email '
                    'vibe_seller_get_new_emails has shown you. Persisting '
                    'it would re-sweep already-processed emails next run. '
                    'Persist the `next_watermark` from get_new_emails '
                    'verbatim; never derive the watermark from an email '
                    'Date header.'
                ),
            )

    now = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(ScheduleState).values(
        schedule_id=task.schedule_id,
        store_id=store_scope,
        key=key,
        value=body.value,
        updated_at=now,
        updated_by_task_id=task_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            ScheduleState.schedule_id,
            ScheduleState.store_id,
            ScheduleState.key,
        ],
        set_={
            'value': stmt.excluded.value,
            'updated_at': stmt.excluded.updated_at,
            'updated_by_task_id': stmt.excluded.updated_by_task_id,
        },
    )
    await db.execute(stmt)
    await db.commit()
    return {
        'ok': True,
        'key': key,
        'value': body.value,
        'updated_at': now,
        'updated_by_task_id': task_id,
    }


@router.get('/{task_id}/new-emails')
async def get_new_emails(
    task_id: str,
    lookback_hours: int = _DEFAULT_LOOKBACK_HOURS,
    folder: str = 'INBOX',
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return only the emails that arrived since this schedule's cursor.

    The watermark sweep, done server-side. Reads the
    ``email_watermark`` cursor for the calling task's schedule+store
    scope, queries each linked account's SQLite DB for emails strictly
    newer than it, and returns them with a ready-to-persist
    ``next_watermark``.

    This exists so a scheduled email agent never has to hand-write a
    raw ``SELECT`` against the email DB. An unfiltered query (the
    natural "let me see the inbox" first move) drags
    already-processed email bodies into the agent's transcript and
    leaks them into the current run — the exact failure
    ``tests/e2e/test_email_watermark_e2e.py`` pins. Moving the filter
    into code makes that bug class unreachable from the agent surface;
    the prose contract in ``scheduled_pretask.md`` becomes a pointer to
    this one call instead of a SQL template the model can run too early.

    Scope is resolved server-side from the task (never trust an
    agent-supplied store id): the cursor is read for the task's own
    store, and only that store's linked accounts are queried.
    """
    task = await _load_scheduled_task(db, task_id)
    if not task.store_id:
        raise HTTPException(
            status_code=400,
            detail=(
                'new-emails requires a store-scoped scheduled task; this'
                ' task has no store_id.'
            ),
        )
    store_scope = task.store_id

    now_epoch = int(datetime.now(UTC).timestamp())
    state = await db.get(
        ScheduleState,
        (task.schedule_id, store_scope, _EMAIL_WATERMARK_KEY),
    )
    if state is not None and state.value:
        # Cursor is epoch-validated on write (see _validate_typed_value),
        # so int() is safe here.
        since_epoch = int(state.value)
        first_run = False
    else:
        lookback = max(1, lookback_hours) * 3600
        since_epoch = now_epoch - lookback
        first_run = True

    link_rows = (
        (
            await db.execute(
                select(EmailAccount)
                .join(
                    StoreEmailLink,
                    StoreEmailLink.email_account_id == EmailAccount.id,
                )
                .where(StoreEmailLink.store_id == store_scope)
            )
        )
        .scalars()
        .all()
    )

    accounts_out: list[dict] = []
    total = 0
    next_watermark = since_epoch
    for acct in link_rows:
        # init guards the case where sync has not created the file yet.
        await asyncio.to_thread(init_email_db, acct.id)
        emails, acct_max = await asyncio.to_thread(
            get_new_emails_since, acct.id, since_epoch, folder
        )
        next_watermark = max(next_watermark, acct_max)
        total += len(emails)
        accounts_out.append({
            'account_id': acct.id,
            'email': acct.email,
            'new_emails': emails,
        })

    # Record the cursor floor server-side. The floor is the highest
    # next_watermark we have shown the agent;
    # set_schedule_state('email_watermark', …) refuses any lower value,
    # so the agent can never persist a watermark that re-sweeps what it
    # just saw. The update is ATOMIC and monotonic: the ON CONFLICT
    # clause takes MAX(existing, new) in SQL rather than a
    # read-then-write in Python, so two overlapping GET /new-emails
    # calls (or a retry) can never clobber a higher floor with a lower
    # one. CAST to INTEGER so the comparison is numeric, not the unsafe
    # lexicographic ordering the epoch-string format otherwise invites.
    floor_now = datetime.now(UTC).isoformat()
    floor_stmt = sqlite_insert(ScheduleState).values(
        schedule_id=task.schedule_id,
        store_id=store_scope,
        key=_EMAIL_WATERMARK_FLOOR_KEY,
        value=str(next_watermark),
        updated_at=floor_now,
        updated_by_task_id=task_id,
    )
    floor_stmt = floor_stmt.on_conflict_do_update(
        index_elements=[
            ScheduleState.schedule_id,
            ScheduleState.store_id,
            ScheduleState.key,
        ],
        set_={
            'value': func.max(
                cast(floor_stmt.excluded.value, Integer),
                cast(ScheduleState.value, Integer),
            ),
            'updated_at': floor_stmt.excluded.updated_at,
            'updated_by_task_id': floor_stmt.excluded.updated_by_task_id,
        },
    )
    await db.execute(floor_stmt)
    await db.commit()

    return {
        'key': _EMAIL_WATERMARK_KEY,
        'first_run': first_run,
        'watermark_used': since_epoch,
        'now_epoch': now_epoch,
        'count': total,
        # Persist this verbatim once you have reported the bodies:
        # set_schedule_state('email_watermark', next_watermark).
        'next_watermark': str(next_watermark),
        'accounts': accounts_out,
        'next_action': (
            'Report each new_emails body verbatim, then call '
            "vibe_seller_set_schedule_state('email_watermark', "
            'next_watermark). Do NOT query the email DB with a raw '
            'sqlite SELECT for this sweep.'
        ),
    }
