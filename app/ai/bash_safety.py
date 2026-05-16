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


# ── Catalog-first guard ────────────────────────────────────────────
#
# The system prompt instructs the agent to read the store/global
# catalog BEFORE any filesystem search of knowledge/ or stores/.
# Prose-only enforcement is unreliable — glm-4.7 routinely went
# straight to `find`/`ls` against those paths when the user prompt
# included a search verb. This guard turns the prose contract into
# a mechanism: filesystem-search commands targeting knowledge/ or
# stores/ paths are denied until the agent has read a catalog file
# this session. After that, normal `find`/`ls`/`grep` flows work.
#
# The denied set is a small allowlist of search verbs in command
# position, not arbitrary shell — `echo "ls stores"`, `grep pattern
# file.txt`, etc. are all unaffected. False negatives (an obscure
# search form slips through) are fine; false positives (blocking
# innocuous bash) would punish the agent for no design reason.

_SEARCH_INVOCATION = re.compile(
    _COMMAND_PREFIX + r'(find|ls|grep|rg|fd|tree)\b([^;|&\n]*)'
)
# Paths that point into either catalog tree. Matches absolute
# (``/home/<user>/.vibe-seller/stores``), home-relative
# (``~/.vibe-seller/knowledge``), and workspace-relative
# (``stores/<slug>/...``, ``knowledge/...``) forms.
_CATALOG_PATH = re.compile(
    r'(?:^|\s|=)'
    r'(?:'
    r'(?:[a-zA-Z]:)?/?(?:[^/\s]*/)*\.vibe-seller/(?:stores|knowledge)'
    r'|'
    r'~/?\.vibe-seller/(?:stores|knowledge)'
    r'|'
    r'(?:\./)?(?:stores|knowledge)(?:/|\b)'
    r')'
)

_CATALOG_FIRST_DENY = (
    'Blocked: direct filesystem search of `knowledge/` or `stores/` '
    'before reading the catalog. The catalog (Read '
    '`stores/<slug>/CATALOG.md` for store tasks or '
    '`knowledge/CATALOG.md` for no-store tasks) is the complete '
    'manifest of available files with a one-line summary of each — '
    'searching by `find`/`ls`/`grep` against those trees usually '
    'duplicates information the catalog already contains. Read the '
    'catalog first; after that, normal search commands work normally.'
)


def check_catalog_first(command: str, catalog_read: bool) -> str | None:
    """Return a deny reason if *command* searches knowledge/stores
    before the agent has read a catalog this session.

    Once any catalog has been read this session (tracked by the
    caller and passed as *catalog_read*), the guard is disabled and
    the agent is free to use `find`/`grep`/etc. against those paths.
    """
    if catalog_read or not command:
        return None
    for match in _SEARCH_INVOCATION.finditer(command):
        args = match.group(2)
        if _CATALOG_PATH.search(args):
            return _CATALOG_FIRST_DENY
    return None


# Path patterns that identify a *catalog* file (any level). Reading
# any of these flips the catalog-first guard off for the rest of
# the session — at that point the agent has seen the manifest and
# may search freely. The pattern is intentionally lax (matches L1
# `knowledge/project/CATALOG.md`, L2 `knowledge/CATALOG.md`, and
# L3 `stores/<slug>/CATALOG.md`).
_CATALOG_FILE = re.compile(
    r'(?:knowledge|stores/[^/]+)(?:/[^/]+)*/CATALOG\.md$'
)


def is_catalog_path(path: str) -> bool:
    """Return True if *path* points at any-level CATALOG.md."""
    if not path:
        return False
    return bool(_CATALOG_FILE.search(path))


# Same intent as ``check_catalog_first`` for Bash, but applied to the
# Claude Code built-in ``Glob`` and ``Grep`` tools. Without this, an
# agent denied at the Bash layer pivots to ``Glob(pattern='stores/...')``
# or ``Grep(path='knowledge/...')`` and gets the same broad sweep
# through a different tool — exactly what the catalog-first contract
# is supposed to prevent. The check looks at the tool's ``path`` /
# ``pattern`` arguments; if either references a catalog tree the
# guard fires until the agent has read a CATALOG.md.
_PATTERN_TOUCHES_CATALOG = re.compile(
    r'(?:^|/|\*|\\)(stores|knowledge)(?:/|\b|$)'
)


def check_catalog_first_tool_args(
    tool_input: dict, catalog_read: bool
) -> str | None:
    """Return a deny reason if a Glob/Grep tool call sweeps the
    knowledge/ or stores/ trees before the agent has read a catalog.

    Inspects the ``path`` and ``pattern`` fields — either may carry
    the offending directory reference. Once the agent has read any
    CATALOG.md this session, the guard turns off and Glob/Grep work
    normally against those trees.
    """
    if catalog_read:
        return None
    for field in ('path', 'pattern'):
        val = tool_input.get(field, '')
        if not isinstance(val, str) or not val:
            continue
        if _PATTERN_TOUCHES_CATALOG.search(val):
            return _CATALOG_FIRST_DENY
    return None
