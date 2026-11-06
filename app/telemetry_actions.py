"""Schedule-lifecycle telemetry from the ORM layer.

The routes that create / update / delete a schedule do not emit
telemetry — these listeners do, with the same structural pattern as
the Task terminal-event listener (``app.telemetry_tasks``): record on
the unit of work, emit after commit, drop on rollback. No caller can
miss an event, and a rolled-back write emits nothing.

``after_update`` fires only when a user-editable field changed:
bookkeeping writes (``plan_status``, the frozen ``plan``,
``last_run_at``, ``updated_at`` …) are not "the user edited the
schedule". The ``via`` channel is read at commit time — the commit
happens inside the authenticated request that made the change.
"""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session as _SyncSession, attributes, object_session

from app import telemetry
from app.models.schedule import Schedule
from app.telemetry_events import TelemetryEvent
from app.telemetry_schedules import schedule_event_props

logger = logging.getLogger(__name__)

# The fields an edit (or pause/resume) touches. Everything else on
# the row is planner/scheduler bookkeeping.
_WATCHED_FIELDS = frozenset({
    'title',
    'description',
    'store_id',
    'schedule_type',
    'schedule_time',
    'schedule_day',
    'interval_value',
    'timezone',
    'plan_mode',
    'ai_profile_id',
    'phase_mode',
    'finalize_description',
    'fanout_serial',
    'is_active',
})

_EVENT_OF = {
    'created': TelemetryEvent.SCHEDULE_CREATED,
    'updated': TelemetryEvent.SCHEDULE_UPDATED,
    'deleted': TelemetryEvent.SCHEDULE_DELETED,
}


def _watched_changed(schedule: Schedule) -> bool:
    return any(
        attributes.get_history(schedule, field).has_changes()
        for field in _WATCHED_FIELDS
    )


def _record(action: str, schedule: Schedule) -> None:
    session = object_session(schedule)
    if session is None:
        return
    session.info.setdefault('schedule_actions', []).append((
        action,
        schedule_event_props(schedule),
    ))


@event.listens_for(Schedule, 'after_insert')
def _schedule_inserted(_mapper, _connection, schedule) -> None:
    _record('created', schedule)


@event.listens_for(Schedule, 'after_update')
def _schedule_updated(_mapper, _connection, schedule) -> None:
    if _watched_changed(schedule):
        _record('updated', schedule)


@event.listens_for(Schedule, 'after_delete')
def _schedule_deleted(_mapper, _connection, schedule) -> None:
    _record('deleted', schedule)


@event.listens_for(_SyncSession, 'after_commit')
def _emit_schedule_actions(sync_session) -> None:
    actions = sync_session.info.pop('schedule_actions', None)
    if not actions:
        return
    for action, props in actions:
        try:
            telemetry.send(_EVENT_OF[action], props)
        except Exception:
            logger.debug('schedule telemetry failed', exc_info=True)


@event.listens_for(_SyncSession, 'after_rollback')
def _drop_schedule_actions(sync_session) -> None:
    sync_session.info.pop('schedule_actions', None)
