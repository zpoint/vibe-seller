"""Schedule-level telemetry helpers."""

from app import telemetry
from app.models.schedule import Schedule
from app.telemetry_events import TelemetryEvent
from app.telemetry_via import current_via


def cadence_kind(schedule_type: str) -> str:
    """Coarse cadence label for the schedule_type column values."""
    if schedule_type in ('minutes', 'hours'):
        return 'interval'
    return schedule_type


def schedule_event_props(schedule: Schedule) -> dict:
    return {
        'schedule_id': schedule.id,
        'is_store': schedule.store_id is not None,
        'plan_mode': bool(schedule.plan_mode),
        'cadence': cadence_kind(schedule.schedule_type),
        'via': current_via(),
    }


def send_schedule_created(schedule: Schedule) -> None:
    telemetry.send(
        TelemetryEvent.SCHEDULE_CREATED,
        schedule_event_props(schedule),
    )


def send_schedule_updated(schedule: Schedule) -> None:
    telemetry.send(
        TelemetryEvent.SCHEDULE_UPDATED,
        schedule_event_props(schedule),
    )


def send_schedule_deleted(schedule: Schedule) -> None:
    telemetry.send(
        TelemetryEvent.SCHEDULE_DELETED,
        schedule_event_props(schedule),
    )


def send_schedule_fired(
    schedule_id: str | None,
    *,
    schedule_type: str | None,
    phase_mode: str,
    target_count: int,
    is_catalog: bool = False,
    task_id: str | None = None,
) -> None:
    """One fire of a schedule (``task_id`` when it is 1:1)."""
    props = {
        'schedule_id': schedule_id,
        'schedule_type': schedule_type,
        'phase_mode': phase_mode,
        'fanout_target_count_bucket': telemetry.count_bucket(target_count),
        'is_catalog': is_catalog,
    }
    if task_id is not None:
        props['task_id'] = task_id
    telemetry.send(TelemetryEvent.SCHEDULE_FIRED, props)
