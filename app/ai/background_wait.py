"""No fixed-length ``sleep`` while background work is pending.

A foreground ``sleep 200; ls REVIEW_…`` waiting on a reviewer subagent
blocks for the full 200s even when the subagent is done in 60: its
completion notice is delivered only after the Bash call returns. The
wait primitive that returns on completion is ``TaskOutput`` (see
``report_reviewer.WAIT_FOR_BACKGROUND``). Narrow on purpose: only a
``sleep`` in command position, only at or above the threshold, only in
a foreground call, and only while the session tracks background work it
launched — short poll sleeps (``sleep 20`` between page checks) are
untouched.

Not in ``bash_safety.first_bash_deny``: that chain's guards see only the
command, and this one needs the session's pending-work set.
"""

import re

from app.ai.shell_patterns import COMMAND_PREFIX
from app.ai.stop_gates.report_reviewer import WAIT_FOR_BACKGROUND

_SLEEP_INVOCATION = re.compile(
    COMMAND_PREFIX + r'sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b'
)
_SLEEP_UNIT_S = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
LONG_SLEEP_S = 30


def check_blocking_sleep(command: str, pending: str | None) -> str | None:
    """Deny a long ``sleep`` in *command* while background work is pending.

    ``pending`` names the still-running subagents / background commands
    (``None`` or empty when there are none).
    """
    if not command or not pending:
        return None
    for m in _SLEEP_INVOCATION.finditer(command):
        seconds = float(m.group(1)) * _SLEEP_UNIT_S[m.group(2)]
        if seconds >= LONG_SLEEP_S:
            return (
                f'Blocked: `sleep {m.group(1)}{m.group(2)}` while '
                f'background work you launched is still running '
                f'({pending}). ' + WAIT_FOR_BACKGROUND
            )
    return None


def long_sleep_deny(tool_input: dict, async_agents) -> tuple | None:
    """``(label, reason)`` for the Bash PreToolUse hook, else ``None``.

    A backgrounded sleep blocks nothing, so ``run_in_background`` passes.
    ``async_agents`` is the session's pending map (id → name).
    """
    if tool_input.get('run_in_background'):
        return None
    pending = ', '.join(v or k for k, v in (async_agents or {}).items())
    reason = check_blocking_sleep(tool_input.get('command', ''), pending)
    return ('long-sleep', reason) if reason else None
