"""Pre-tool-use safety checks for Bash commands.

Concurrent task isolation: agents share a single OS user and a single
process namespace, so a Bash command in task A can signal processes
that belong to task B. Real incident: an agent in one fanout sub-task
ran ``pkill -f "until browser-use"`` to clean up its own background
poll loop and the pattern matched bash subprocesses in two sibling
tasks, killing those agents mid-run.

This module rejects Bash commands whose kill scope is broader than the
calling task's own subprocess tree — system-wide ``pkill -f``,
``killall``, and unscoped ``pkill <name>``. Agents see the deny
reason and route around it (use ``kill <pid>`` after a scoped
``pgrep``, or ``pkill -P <ppid>`` for a single subtree).

The reject is a string match against the command text, not real
shell parsing, so the patterns are deliberately conservative — false
negatives (an obscure form slips through) are fine; false positives
(blocking a safe ``kill <pid>``, or denying ``grep pkill file``)
would be bad.
"""

import re

# Match `pkill`/`killall` only in *command position* — not as an
# argument (``grep pkill file``) or inside a string (``echo "pkill -f
# x"``). Command position = start of input, after a shell separator
# (``;``, ``&&``, ``||``, ``|``, ``&``, newline) or the opener of a
# subshell/command-substitution (``(``, backtick), optionally preceded
# by ``sudo`` / ``time`` / similar leading words. Quote handling is
# ad-hoc — a pkill literal that survives shell quoting and lands at
# the start of a real command will be caught; a pkill *inside* a
# matching pair of quotes won't be in command position so it's not
# matched.
_COMMAND_PREFIX = r'(?:^|[;&|\n(`])\s*(?:(?:sudo|time|exec|nohup)\s+)*'
_PKILL_INVOCATION = re.compile(_COMMAND_PREFIX + r'pkill\b([^;|&\n]*)')
_KILLALL_INVOCATION = re.compile(_COMMAND_PREFIX + r'killall\b')

# Scope flags that make a ``pkill`` invocation safe (it kills only
# children of a known parent PID): ``-P 1234``, ``-P$$``, ``-Pf``
# (combined short opt), ``--parent 1234``. Anything else lets pkill
# match by name or full cmdline and is global.
_SCOPE_FLAG = re.compile(r'(?:^|\s)(?:-\w*P\w*|--parent)\b')


_DENY_REASON_TEMPLATE = (
    'Blocked: `{label}` matches processes outside this task. '
    'In this runtime all concurrent tasks share one OS user and '
    'one process namespace, so an unscoped pkill/killall can '
    'silently kill sibling tasks (this has happened in '
    'production). Scoped alternatives that work:\n'
    '  • `kill <pid>` with a specific PID you got from '
    "`pgrep -P $$ ...` (your bash's own children)\n"
    '  • `pkill -P $$ <pattern>` (matches only direct children '
    'of this bash)\n'
    '  • `pkill -P <pid>` (matches children of a specific parent '
    'PID you control)\n'
    'For an `until ...; do sleep N; done` background poll: save '
    'its PID with `COMMAND & echo $!` and `kill $PID` it '
    'directly when done.'
)


def check_dangerous_kill(command: str) -> str | None:
    """Return a deny reason if *command* contains an unscoped kill.

    *command* is the literal Bash command string the agent submitted.
    Returns ``None`` for safe commands. The returned reason is
    surfaced to the agent verbatim, so it explains BOTH why the
    command was blocked AND a working alternative.
    """
    if not command:
        return None

    for match in _PKILL_INVOCATION.finditer(command):
        args = match.group(1)
        if not _SCOPE_FLAG.search(args):
            return _DENY_REASON_TEMPLATE.format(label='pkill (unscoped)')

    if _KILLALL_INVOCATION.search(command):
        return _DENY_REASON_TEMPLATE.format(label='killall')

    return None
