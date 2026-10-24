"""Tools a headless task agent must not be offered."""

from pathlib import Path

_BACKEND = (
    Path(__file__).resolve().parents[2] / 'app' / 'ai' / 'claude_backend.py'
)


def test_session_scheduling_tools_are_disallowed():
    """A task agent exits when done, so nothing it schedules can fire.

    Observed: an agent waiting on a feed called ScheduleWakeup and was
    told "Wakeup not scheduled" -- a turn lost to a tool that cannot work.
    """
    src = _BACKEND.read_text(encoding='utf-8')
    start = src.index("'--disallowedTools',")
    block = src[start : src.index('])', start)]
    for tool in ('ScheduleWakeup', 'CronCreate', 'CronDelete', 'CronList'):
        assert tool in block, tool
    assert 'mcp__playwright__*' in block
