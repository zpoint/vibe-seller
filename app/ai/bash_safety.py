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

    Only fires when the input actually looks like a *broad sweep*:
    a wildcard (``*`` / ``**`` / ``?``) somewhere in the pattern, or
    the ``path`` field is set (directory traversal). A wildcard-free
    pattern referring to one specific file (e.g.
    ``Glob(pattern='knowledge/project/CATALOG.md')``) is essentially
    a file-existence check — the L2/L3 catalog-generation agents
    do this legitimately while building the catalog itself, and
    blocking it deadlocks the catalog-sync flow.
    """
    if catalog_read:
        return None
    path = tool_input.get('path', '')
    if isinstance(path, str) and path and _PATTERN_TOUCHES_CATALOG.search(path):
        return _CATALOG_FIRST_DENY
    pattern = tool_input.get('pattern', '')
    if (
        isinstance(pattern, str)
        and pattern
        and _PATTERN_TOUCHES_CATALOG.search(pattern)
        and any(ch in pattern for ch in ('*', '?'))
    ):
        return _CATALOG_FIRST_DENY
    return None


# ── Ad-tuning reviewer-status guard ───────────────────────────────
#
# When an ads-tuning audit task is about to finalize its deliverable
# (transition to Stop), the agent must have run the
# ``ads-format-review`` reviewer subagent against
# ``AD_AUDIT_<date>.md`` and got back ``Status: ok``. The reviewer
# writes its findings to
# ``<task_workspace>/REVIEW_<YYYY-MM-DD>_iter<N>.md`` with a
# canonical header that includes a ``Status: ok|gaps|incomplete``
# line. The Stop hook reads the latest review file and decides:
#
# - ``ok``               → allow stop
# - ``gaps``             → deny stop, point agent at the gaps
# - ``incomplete``       → allow only if iter ≥ 5 (post-mortem trail)
# - file missing         → deny stop; reviewer must run at least once
# - file unparseable     → deny stop with format reminder
#
# Semantic quality of the review is the reviewer subagent's job;
# code only gates on the file contents. See
# ``amazon-ads/references/reviewer-loop.md`` for the contract and
# the subagent prompt the main agent uses.

_REVIEW_FILE_GLOB = 'REVIEW_*_iter*.md'
_REVIEW_STATUS_RE = re.compile(r'^Status:\s*(\w+)', re.MULTILINE)
_REVIEW_ITER_RE = re.compile(r'_iter(\d+)\.md$')

# Max iterations before we accept ``incomplete`` as a terminal state
# (matches the loop cap documented in ``reviewer-loop.md``).
_REVIEW_MAX_ITERS = 5


def check_review_status(task_dir) -> str | None:
    """Return a deny reason if the ads-audit reviewer hasn't returned
    ``ok`` (or ``incomplete`` at iter ≥ 5); otherwise ``None``.

    Reads the highest-iteration ``REVIEW_*_iter*.md`` in *task_dir*.
    Quiet no-op for non-ads tasks (no ``AD_AUDIT_*.md`` present in
    the workspace — caller should pre-check; this function still
    returns ``None`` when no review file exists AND no audit file
    exists, but DENIES when audit exists without a review).
    """
    if task_dir is None:
        return None
    try:
        audit_files = list(task_dir.glob('AD_AUDIT_*.md'))
    except OSError:
        return None
    if not audit_files:
        return None  # Not an ads-tuning task; nothing to gate.

    try:
        review_files = sorted(task_dir.glob(_REVIEW_FILE_GLOB))
    except OSError:
        review_files = []
    if not review_files:
        return (
            'Reviewer never ran. Before finalizing, spawn the '
            '``ads-format-review`` subagent (subagent_type='
            '"general-purpose") against your AD_AUDIT_*.md per '
            '``amazon-ads/references/reviewer-loop.md``, and write '
            'the result to ``REVIEW_<YYYY-MM-DD>_iter1.md`` in this '
            'workspace. Re-run reviewer until Status: ok or until '
            f'iter {_REVIEW_MAX_ITERS} with Status: incomplete.'
        )

    def _iter_of(p):
        m = _REVIEW_ITER_RE.search(p.name)
        return int(m.group(1)) if m else 0

    # Pick the most recently written review file, not the highest
    # iter number. A workspace can accumulate REVIEW files from
    # several audit cycles (e.g. ``REVIEW_2026-05-22_iter7.md``
    # from yesterday's audit + ``REVIEW_2026-05-24_iter1.md`` from
    # today's) — choosing by iter number would pick yesterday's
    # iter7 over today's iter1, gating the new audit against a
    # stale verdict. mtime correctly reflects which review covers
    # the current audit.
    try:
        latest = max(review_files, key=lambda p: p.stat().st_mtime)
    except OSError:
        latest = max(review_files, key=_iter_of)
    try:
        content = latest.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return f'{latest.name} could not be read; rewrite the review file.'

    match = _REVIEW_STATUS_RE.search(content)
    if not match:
        return (
            f'{latest.name} has no ``Status:`` line. The reviewer '
            'output must begin with one of: ``Status: ok`` | '
            '``Status: gaps`` | ``Status: incomplete``. See '
            '``amazon-ads/references/reviewer-loop.md`` for the '
            'canonical format.'
        )

    status = match.group(1).lower()
    iter_num = _iter_of(latest)
    if status == 'ok':
        return None
    if status == 'incomplete' and iter_num >= _REVIEW_MAX_ITERS:
        return None  # accept as terminal; gaps are on-disk for post-mortem
    if status == 'gaps':
        return (
            f'Reviewer iter {iter_num} found gaps in the audit. '
            f'Read {latest.name} for the list, fix the audit in '
            f'place (Edit tool, not re-drill), then spawn the '
            'reviewer again to write '
            f'``REVIEW_*_iter{iter_num + 1}.md``. Repeat until '
            f'Status: ok or iter {_REVIEW_MAX_ITERS} with '
            'Status: incomplete.'
        )
    if status == 'incomplete' and iter_num < _REVIEW_MAX_ITERS:
        return (
            f'``Status: incomplete`` only valid at iter '
            f'{_REVIEW_MAX_ITERS}+. Current is iter {iter_num} — '
            'keep iterating.'
        )
    return (
        f'Unknown reviewer status {status!r} in {latest.name}. '
        'Must be one of: ok | gaps | incomplete.'
    )


# ── Ad-tuning execution-review guard ───────────────────────────────
#
# Once an audit has been delivered (REVIEW_*_iter*.md Status: ok) and
# the user instructs the agent to execute the plan, the agent creates
# ``EXECUTION_LOG.md`` and begins applying each Recommendation row to
# the live Amazon/Noon console. Before the agent may stop, an
# ``ads-execution-review`` subagent must verify that:
#
# - Every actionable Recommendation in the audit has a corresponding
#   EXECUTION_LOG row marked ``applied``.
# - For every ``applied`` row, the per-campaign TSV reflects the new
#   value (bid, status, negative-keyword, etc.).
# - No row marked ``failed`` is left without a retry or an explicit
#   note explaining why it was skipped.
#
# The reviewer writes ``EXEC_REVIEW_<YYYY-MM-DD>_iter<N>.md`` with the
# same canonical ``Status: ok|gaps|incomplete`` header. The gate is
# triggered only when ``EXECUTION_LOG.md`` exists in the task
# workspace — non-execution tasks (audit-only) pass through.
#
# Semantic quality is the reviewer subagent's job. See
# ``amazon-ads/references/reviewer-loop.md § Execution-review mode``.

_EXEC_LOG_NAME = 'EXECUTION_LOG.md'
_EXEC_REVIEW_FILE_GLOB = 'EXEC_REVIEW_*_iter*.md'
_EXEC_REVIEW_MAX_ITERS = 5


def check_exec_review_status(task_dir) -> str | None:
    """Return a deny reason if the ads-execution reviewer hasn't
    returned ``ok`` (or ``incomplete`` at iter ≥ 5); otherwise None.

    Quiet no-op when ``EXECUTION_LOG.md`` is absent — the task is
    not in execution mode.
    """
    if task_dir is None:
        return None
    try:
        exec_log = task_dir / _EXEC_LOG_NAME
        if not exec_log.exists():
            return None  # Not an execution task; nothing to gate.
    except OSError:
        return None

    try:
        review_files = sorted(task_dir.glob(_EXEC_REVIEW_FILE_GLOB))
    except OSError:
        review_files = []
    if not review_files:
        return (
            'Execution reviewer never ran. After applying every '
            'Recommendation row from AD_AUDIT_*.md, spawn the '
            '``ads-execution-review`` subagent (subagent_type='
            '"general-purpose") per ``amazon-ads/references/'
            'reviewer-loop.md § Execution-review mode``, and write '
            'the result to ``EXEC_REVIEW_<YYYY-MM-DD>_iter1.md`` in '
            'this workspace. Re-run until Status: ok or until '
            f'iter {_EXEC_REVIEW_MAX_ITERS} with Status: incomplete.'
        )

    def _iter_of(p):
        m = _REVIEW_ITER_RE.search(p.name)
        return int(m.group(1)) if m else 0

    latest = max(review_files, key=_iter_of)
    try:
        content = latest.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return (
            f'{latest.name} could not be read; rewrite the '
            'execution-review file.'
        )

    match = _REVIEW_STATUS_RE.search(content)
    if not match:
        return (
            f'{latest.name} has no ``Status:`` line. The execution '
            'reviewer output must begin with one of: ``Status: ok`` '
            '| ``Status: gaps`` | ``Status: incomplete``. See '
            '``amazon-ads/references/reviewer-loop.md § '
            'Execution-review mode``.'
        )

    status = match.group(1).lower()
    iter_num = _iter_of(latest)
    # Stale-review guard: a Status: ok review predating the latest
    # EXECUTION_LOG row count is no longer valid. Compare mtimes.
    # Without this, an agent can pass an iter1 ok, then add more
    # rows post-review, and finalize without re-running the reviewer.
    try:
        log_mtime = exec_log.stat().st_mtime
        review_mtime = latest.stat().st_mtime
        if log_mtime > review_mtime + 5:
            return (
                f'Execution reviewer {latest.name} predates the '
                f'current EXECUTION_LOG.md (log mtime {log_mtime:.0f}'
                f' > review mtime {review_mtime:.0f}). New rows have '
                'been added since the last review. Spawn the '
                '``ads-execution-review`` subagent again to write '
                f'``EXEC_REVIEW_*_iter{iter_num + 1}.md`` covering '
                'the current log state. Old reviews are not carried '
                'forward — every set_task_result must be backed by a '
                'review that postdates the latest log changes.'
            )
    except OSError:
        pass
    if status == 'ok':
        return None
    if status == 'incomplete' and iter_num >= _EXEC_REVIEW_MAX_ITERS:
        return None
    if status == 'gaps':
        return (
            f'Execution reviewer iter {iter_num} found gaps. Read '
            f'{latest.name} for the list: missing actions, '
            'unverified claims, or incorrect applications. Apply '
            'the missing/incorrect changes on the live page, '
            're-read the bid/status field to verify, update the '
            'per-campaign TSV, then spawn the reviewer again to '
            f'write ``EXEC_REVIEW_*_iter{iter_num + 1}.md``. Repeat '
            f'until Status: ok or iter {_EXEC_REVIEW_MAX_ITERS} '
            'with Status: incomplete.'
        )
    if status == 'incomplete' and iter_num < _EXEC_REVIEW_MAX_ITERS:
        return (
            f'``Status: incomplete`` only valid at iter '
            f'{_EXEC_REVIEW_MAX_ITERS}+. Current is iter {iter_num} '
            '— keep iterating on the unresolved actions.'
        )
    return (
        f'Unknown execution-reviewer status {status!r} in '
        f'{latest.name}. Must be one of: ok | gaps | incomplete.'
    )
