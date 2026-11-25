"""When a schedule fires: the rules for its type, time and day.

ONE place for them, used by every write (the REST routes, which the MCP
tools call in-process) and by the trigger builder
(``app.scheduler.cron.build_trigger``).

The day rule is why this module exists. A weekly schedule fires on ONE
weekday and a monthly one on ONE day of the month, so both need that
day. It used to be optional: a weekly or monthly schedule created
without it (over the API or MCP — the web form always sends one) got a
trigger with only the hour and minute, which APScheduler reads as
EVERY DAY, while the UI showed "weekly, Monday". Each check returns the
error to show, or None.
"""

VALID_TYPES = ('minutes', 'hours', 'days', 'weekly', 'monthly')

#: The day each type needs (inclusive). Other types take no day.
DAY_RANGES = {'weekly': (1, 7), 'monthly': (1, 31)}


def schedule_type_error(schedule_type: str) -> str | None:
    if schedule_type in VALID_TYPES:
        return None
    return (
        f'Invalid schedule_type: {schedule_type}. Must be one of {VALID_TYPES}'
    )


def schedule_time_error(schedule_time: str) -> str | None:
    """HH:MM or HH:MM:SS, in range."""
    parts = schedule_time.split(':')
    if len(parts) not in (2, 3):
        return 'Invalid schedule_time format. Expected HH:MM or HH:MM:SS'
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return 'Invalid schedule_time values'
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return 'Invalid schedule_time values'
    return None


def schedule_day_error(
    schedule_type: str, schedule_day: int | None
) -> str | None:
    """A weekly schedule needs its weekday (ISO, 1=Mon..7=Sun), a monthly
    one its day of the month (1..31); other types ignore the day."""
    span = DAY_RANGES.get(schedule_type)
    if span is None:
        return None
    lo, hi = span
    if schedule_day is None:
        if schedule_type == 'weekly':
            return (
                'A weekly schedule needs schedule_day: the weekday it runs '
                'on, 1..7 (1=Mon, 7=Sun).'
            )
        return (
            'A monthly schedule needs schedule_day: the day of the month '
            'it runs on, 1..31.'
        )
    if not lo <= schedule_day <= hi:
        if schedule_type == 'weekly':
            return (
                'weekly schedule_day must be 1..7 (ISO weekday, 1=Mon, '
                f'7=Sun); got {schedule_day}.'
            )
        return f'monthly schedule_day must be 1..31; got {schedule_day}.'
    return None
