"""Schedule-state endpoints for scheduled-task agents.

Split out of ``app/routers/tasks.py`` so the task router can stay
under the repo's 800-line-per-file cap. These endpoints back the
``vibe_seller_get_schedule_state`` / ``vibe_seller_set_schedule_state``
MCP tools used by scheduled tasks to hand off cursor values between
runs.
"""

from datetime import UTC, datetime
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.schedule_state import NO_STORE_SCOPE, ScheduleState
from app.models.task import Task
from app.models.user import User

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
        known_keys = sorted(row[0] for row in existing.all())
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
    # `body.value` is already stripped + non-empty — enforced by
    # SetScheduleStateRequest's StringConstraints (pydantic 422s on
    # null / missing / empty / whitespace-only at the boundary).
    _validate_typed_value(key, body.value)
    task = await _load_scheduled_task(db, task_id)
    store_scope = task.store_id or NO_STORE_SCOPE
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
