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

from pathlib import Path
import re

from app.ai.skill_review import skills_requiring_review
from app.ai.stop_gates import (
    ad_completeness_review,
    recorded_skills,
    report_reviewer,
)
from app.plugins import (
    registered_pretool_gates,
    registered_review_markers,
)

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


def should_mark_catalog_read(tool_name: str, tool_input: dict) -> bool:
    """Return True iff a Read of a catalog-shaped path was attempted.

    Used by the PreToolUse hook to flip the catalog-first guard off
    once the agent has *tried* to consult the catalog. Existence on
    disk deliberately doesn't matter: a fresh workspace (newly created
    store, never-run catalog-sync) has no ``stores/<slug>/CATALOG.md``
    yet, so the legitimate first move "Read the catalog, find it
    missing, then `ls` to see what's there" was indefinitely denied
    under the old `is_file()`-gated check — which broke every fresh
    task for providers (DeepSeek thinking mode in particular) that
    can't recover from a hook-denied tool_use in the same turn.

    Anti-spoofing concern (agent fabricates a fake catalog file to
    bypass the guard) is mitigated elsewhere: Write/Edit tools are
    routed through the same approval flow, and a fake CATALOG.md
    would still be empty, so the agent gains nothing by reading it
    first.
    """
    if tool_name != 'Read':
        return False
    return is_catalog_path(tool_input.get('file_path', ''))


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


# ── Report-script guard ────────────────────────────────────────────
#
# Ad-audit reports must be authored through Read+Edit, one campaign
# at a time — the contract is that every 建议 row is the model's own
# judgement of that campaign's data, not template output. Twice now
# an agent under turn pressure wrote a ``build_report.py`` that
# regenerated the whole report from TSVs, overwriting many sessions
# of hand analysis. In-prompt prohibitions did not survive turn
# pressure, so the contract lives here: any Bash command that would
# have a script (or shell redirection) WRITE an AD_AUDIT file is
# denied. Scripts remain free to READ the report or TSVs and print
# analysis to stdout; ``sed -i`` style targeted in-place fixes are
# deliberately not matched (tolerated for batch cleanup).

_REPORT_TOKEN = 'AD_AUDIT'
# Shell redirection or tee whose TARGET is an AD_AUDIT file.
_REDIRECT_TO_REPORT_RE = re.compile(
    r'(?:>>?\s*|\btee\s+(?:-a\s+)?)\S*AD_AUDIT\S*\.md'
)
# cp/mv landing ON an AD_AUDIT file from a non-AD_AUDIT source
# (restoring AD_AUDIT_PREVIOUS.md over the report stays allowed).
_COPY_TO_REPORT_RE = re.compile(
    _COMMAND_PREFIX + r'(?:cp|mv)\s+(?:-\S+\s+)*'
    r'(?!\S*AD_AUDIT)\S+\s+\S*AD_AUDIT\S*\.md'
)
# Interpreter invocation with a script-file argument.
_SCRIPT_FILE_RE = re.compile(
    _COMMAND_PREFIX
    + r'(?:python3?|node|bash|sh)\s+[^;|&\n]*?(\S+\.(?:py|js|sh))\b'
)
# Code that opens a file for writing (inline ``python -c``/heredoc
# bodies are part of the command text, so one regex serves both).
_WRITE_HINT_RE = re.compile(
    r"open\s*\([^)]*['\"][wax]b?\+?['\"]"
    r'|\.write_text\s*\(|\.write\s*\(|writeFileSync|fs\.write'
    r'|shutil\.(?:copy|move)|os\.rename|os\.replace'
)

_REPORT_SCRIPT_DENY = (
    'BLOCKED — {label} would write the AD_AUDIT report from a '
    'script. The report is hand-authored: scripts may READ TSVs '
    'and print analysis to stdout, but every report change must '
    'go through the Edit tool (one campaign at a time) so each '
    '建议 stays your own judgement of that campaign. Read the '
    'relevant TSV, then Edit the campaign section directly.'
)


def check_report_script_write(command: str, task_dir=None) -> str | None:
    """Return a deny reason if *command* would script-write a report.

    Three surfaces, mirroring the observed bypasses: shell
    redirection/tee onto an ``AD_AUDIT*.md``; cp/mv landing on one
    (from a non-AD_AUDIT source); and running a script whose code —
    inline in the command or on disk under *task_dir* — both names
    ``AD_AUDIT`` and opens a file for writing.
    """
    if not command or _REPORT_TOKEN not in command:
        # Fast path; the script-file branch below re-checks content.
        if not (command and _SCRIPT_FILE_RE.search(command)):
            return None

    if _REDIRECT_TO_REPORT_RE.search(command):
        return _REPORT_SCRIPT_DENY.format(label='shell redirection')
    if _COPY_TO_REPORT_RE.search(command):
        return _REPORT_SCRIPT_DENY.format(label='cp/mv')
    # Inline code (python -c, heredoc): body is in the command text.
    if _REPORT_TOKEN in command and _WRITE_HINT_RE.search(command):
        return _REPORT_SCRIPT_DENY.format(label='inline script')
    # Script file on disk: read it and look for report writes.
    for m in _SCRIPT_FILE_RE.finditer(command):
        script = Path(m.group(1))
        if not script.is_absolute():
            if task_dir is None:
                continue
            script = Path(task_dir) / script
        try:
            src = script.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if _REPORT_TOKEN in src and _WRITE_HINT_RE.search(src):
            return _REPORT_SCRIPT_DENY.format(label=f'{m.group(1)}')
    return None


_REPORT_FILE_RE = re.compile(r'(?:^|/)AD_AUDIT[^/]*\.md$')

_REPORT_OVERWRITE_DENY = (
    'BLOCKED — Write would OVERWRITE the existing AD_AUDIT report '
    'wholesale. That has twice replaced the report with a stale '
    'transform and destroyed sessions of hand revisions. The report '
    'is updated incrementally: use the Edit tool for every change '
    '(block-level Edits are fine). Write is only allowed to CREATE '
    'the report when no file exists yet (the scaffold step).'
)


def check_report_overwrite(
    tool_name: str, tool_input: dict, task_dir=None
) -> str | None:
    """Deny a Write that overwrites OR forks the AD_AUDIT report.

    Closes the Write-tool hop around ``check_report_script_write``:
    a script may generate a "corrected" report in /tmp (allowed —
    read-only analysis) and the agent then dumps it over the real
    report with the built-in Write tool. Same wholesale-regeneration
    bug class, different verb — observed live replacing a 334KB
    report with a stale 298KB base.

    The task dir holds ONE canonical report. Two denials enforce it:
    - Write to the existing report path (wholesale overwrite).
    - Write to a NEW ``AD_AUDIT_*.md`` while another already exists —
      the fork workaround observed live: overwrite denied → agent
      creates ``AD_AUDIT_<today>.md`` seeded from a stale temp copy
      and burns a whole session fixing the zombie.
    First-report scaffold creation stays allowed; Edit never touched.
    """
    if tool_name != 'Write':
        return None
    path = tool_input.get('file_path', '')
    if not isinstance(path, str) or not _REPORT_FILE_RE.search(path):
        return None
    target = Path(path)
    if not target.is_absolute():
        if task_dir is None:
            return None
        target = Path(task_dir) / target
    if target.exists():
        return _REPORT_OVERWRITE_DENY
    siblings = [
        p for p in target.parent.glob('AD_AUDIT_*.md') if p.name != target.name
    ]
    if siblings:
        return (
            f'任务目录已有报告 {siblings[0].name} ——一个任务只有一份'
            '报告，不要另建新的 AD_AUDIT 文件（fork 会从陈旧副本起步，'
            '把已修复的问题全部带回来）。直接用 Edit 工具修改'
            f' {siblings[0].name} 本身。'
        )
    return None


# ── Skill-file write guard ────────────────────────────────────────
#
# The task's ``.claude/`` tree is a per-task COPY of the shared
# workspace (WorkspaceManager copies, not symlinks, because Claude
# Code's Glob won't traverse symlinked ** — see workspace/manager.py).
# So a built-in Write/Edit against ``.claude/skills/<slug>/…`` reports
# "updated successfully" but only mutates the throwaway copy — the
# durable skill under ~/.vibe-seller/.claude/skills/ is untouched and
# the change vanishes when the task ends. Observed live: an agent asked
# to "update that skill to also post to WeCom" Edited the task-local
# SKILL.md, saw success, and moved on — the skill never changed.
#
# The only durable path is the vibe_seller_save_skill MCP tool. Deny
# built-in file writes anywhere under .claude/skills/ and point there.

_SKILL_FILE_RE = re.compile(r'(?:^|/)\.claude/skills/[^/]+/')

_SKILL_WRITE_DENY = (
    'BLOCKED — do not create or edit skills with the Write/Edit tool. '
    "A file under .claude/skills/ lives in the task's throwaway copy of "
    'the workspace: the built-in tools report success but the change is '
    'discarded when the task ends, so the skill is NOT durably saved. '
    'To create or extend a user-space skill, load the "save-skill" '
    'skill and use the vibe_seller_save_skill MCP tool — it overwrites '
    'an existing updatable skill (that is how you extend) or creates a '
    'new one. Built-in (maintainer-shipped) skills are read-only; if one '
    'is the closest match, create a new user-space skill instead.'
)


def check_skill_file_write(tool_name: str, tool_input: dict) -> str | None:
    """Deny a built-in Write/Edit targeting a ``.claude/skills/**`` path.

    Skill files must be written through ``vibe_seller_save_skill``; the
    built-in file tools silently fail to persist through the per-task
    ``.claude`` copy. See the module comment above.
    """
    if tool_name not in ('Write', 'Edit', 'MultiEdit'):
        return None
    path = tool_input.get('file_path', '')
    if not isinstance(path, str) or not _SKILL_FILE_RE.search(path):
        return None
    return _SKILL_WRITE_DENY


# ── Ad-tuning reviewer-status guard ───────────────────────────────
#
# Before an ads-report task may Stop, the ``ads-report-review`` reviewer
# subagent must have written ``REVIEW_<date>_iter<N>.md`` with
# ``Status: ok`` (or ``incomplete`` at iter ≥ 5). Status → hook action:
# ok=allow, gaps=deny+point at gaps, incomplete=allow only at iter ≥ 5,
# missing/unparseable=deny. Semantic quality is the reviewer's job; code
# only gates on the file. Contract: ``amazon-ads/references/reviewer-loop.md``.

# The REPORT-reviewer verdict logic lives in
# ``stop_gates.report_reviewer`` so BOTH completion paths (this Stop hook
# AND set_task_result) enforce the same sign-off — see that module's
# docstring. Status parsing lives there too (``effective_status``, shared
# by the EXEC-review guard below so the fail-closed rule has one home);
# only the iter-number regex is still needed locally.
_REVIEW_ITER_RE = re.compile(r'_iter(\d+)\.md$')

# Audits a SERVER-side completeness gate reviews at set_task_result.
# amazon/noon combo-section audits → ``ad_completeness_review``; plugins
# register their own markers via ``register_review_marker`` (ORed in by
# ``_is_server_reviewed``). Used only for NON-ad-skill tasks now (ad-skill
# tasks are keyed at the task level — see ``check_review_status``).
_SERVER_REVIEWED_RE = re.compile(
    r'(?im)^##.*(amazon|noon)\s+(sa|ae|mx|us|eg|com)\b'
)


def _is_server_reviewed(audit_text: str) -> bool:
    """True if a server-side completeness gate already reviews this audit.

    Core's amazon/noon markers OR any plugin-registered marker. A bad
    plugin pattern is skipped, never raised — a typo in one plugin must
    not break the gate for everyone.
    """
    if _SERVER_REVIEWED_RE.search(audit_text):
        return True

    for pattern in registered_review_markers():
        try:
            if re.search(pattern, audit_text):
                return True
        except re.error:
            continue
    return False


def check_review_status(task_dir) -> str | None:
    """Deny reason if the DoD reviewer hasn't returned ``ok`` (or
    ``incomplete`` at iter ≥ 5); else ``None``. Fires for ads skills AND
    any skill declaring a ``review:`` block; no-op otherwise.
    """
    if task_dir is None:
        return None
    # Identify ad-report tasks by BOUND SKILL, not filename (no escape).
    skills = recorded_skills(task_dir.name)
    is_ad_task = bool(skills & report_reviewer.AD_SKILLS)
    # Phase 2: any non-ad skill that declares a ``review:`` block also
    # requires the active reviewer verdict before the turn may end.
    needs_general_review = not is_ad_task and bool(
        skills_requiring_review(skills, task_dir)
    )
    try:
        audit_files = list(task_dir.glob('AD_AUDIT_*.md'))
    except OSError:
        audit_files = []
    if not audit_files:
        if is_ad_task or needs_general_review:
            # Even with no report file, route to the reviewer — it
            # decides whether the task needed one (real deliverable →
            # gaps) or had nothing to verify (quick lookup → signs off
            # fast).
            return report_reviewer.reviewer_verdict(task_dir)
        return None  # Nothing bound that requires review.

    # This path also gates the ENDING-TURN bypass (streaming result
    # persisted without set_task_result — the 3/24 bug) under the same
    # contract, with the bounded fail-open.
    newest_audit = max(audit_files, key=lambda p: p.stat().st_mtime)
    try:
        audit_text = newest_audit.read_text(encoding='utf-8')
    except OSError:
        audit_text = ''
    if is_ad_task:
        # Core ad report → HYBRID: the deterministic coverage FLOOR
        # (AUDIT_SCOPE combo + active-id coverage + monotonic drills —
        # ground truth an LLM can't fake) AND the active ads-report-review
        # verification (opens the live console + cross-checks).
        floor = ad_completeness_review.drill_incomplete_reason(
            audit_text, task_dir.name
        )
        if floor is not None:
            return floor
        # Floor passed → fall through to the REVIEW_*.md reviewer check.
    elif _is_server_reviewed(audit_text):
        # A plugin's own server-side completeness gate, or a bare
        # amazon/noon-headed audit from a NON-ad-skill task: keep the
        # legacy floor-only behavior — don't force the ads reviewer on it.
        return ad_completeness_review.drill_incomplete_reason(
            audit_text, task_dir.name
        )

    # Reviewer sign-off — shared with the set_task_result path.
    return report_reviewer.reviewer_verdict(task_dir)


# ── Ad-tuning execution-review guard ───────────────────────────────
#
# After the user asks the agent to EXECUTE the plan, it creates
# ``EXECUTION_LOG.md`` and applies each Recommendation on the live
# console. Before Stop, the ``ads-execution-review`` subagent must verify
# every actionable row was applied (EXECUTION_LOG + per-campaign TSV
# reflect the new value; failures retried or noted) and write
# ``EXEC_REVIEW_<date>_iter<N>.md`` with the same Status header. Gated
# only when ``EXECUTION_LOG.md`` exists (audit-only tasks pass through).
# Contract: ``amazon-ads/references/reviewer-loop.md § Execution-review``.

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
    # Prior-turn exec-review verdicts are moved to .prev_turns/ at turn
    # start (report_reviewer.rollover_reviews), so this glob sees only
    # the current turn's — a follow-up can't inherit a stale verdict.
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

    # Fail-closed on a self-contradictory verdict (a leading ``ok`` with a
    # bolded ``incomplete`` conclusion) — same rule as the report gate,
    # shared so the bug class can't recur in only one path.
    status, _statuses = report_reviewer.effective_status(content)
    if status is None:
        return (
            f'{latest.name} has no ``Status:`` line. The execution '
            'reviewer output must begin with one of: ``Status: ok`` '
            '| ``Status: gaps`` | ``Status: incomplete``. See '
            '``amazon-ads/references/reviewer-loop.md § '
            'Execution-review mode``.'
        )

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


# ── Pre-live bid-value sanity (the 10× / concatenation overspend bug) ────
#
# The Shadow-DOM bid input does not reliably clear, so a 1.30 target can
# become 11.3 or 3.002.27 (a real-money overspend that shipped live). The
# typed value is visible in the Bash command, so we deny the value-SHAPE
# pathology before it goes live. This guard does NOT know the report
# target (index→keyword map isn't in the command) — catching the wrong
# target is the ``ad_execution_fidelity`` exit gate's job; this is purely
# the absurd-magnitude / multi-decimal catch. The value appears in a
# legacy ``browser-use input <idx> "1.30"``, a 0.13 helper
# (``type_text``/``fill_input``), or a JS ``el.value = "1.30"`` assign.
_BID_INPUT_RE = re.compile(
    r'(?:'
    r'browser-use\s+(?:input|type)\s+\d+\s+["\']?'
    r'|\btype_text\(\s*["\']'
    r'|\bfill_input\([^)]*["\']\s*,\s*["\']'
    r'|\.value\s*=\s*["\']'
    r')([0-9][0-9.,]*)',
    re.IGNORECASE,
)
# Coarse ceiling: real SP/noon CPC bids are single-digit in their
# currency; anything above this is almost certainly a clear-failure.
_BID_SANITY_CEILING = 50.0


def check_bid_value_shape(command: str) -> str | None:
    """Deny a ``browser-use input`` bid that is a concatenation / absurd value.

    Returns a deny reason for a value with >1 decimal point (``3.002.27``)
    or above the sanity ceiling (``113.0`` from a 3.0 clear-failure typed
    into a field still showing ``11``, concatenating into ``11`` + ``3.0``),
    else None. Deny-only — never
    auto-rewrites a live bid.
    """
    m = _BID_INPUT_RE.search(command)
    if not m:
        return None
    # The 0.13 helpers (type_text/fill_input/.value=) set ANY field —
    # dates, OTP, quantities — and this gate runs on every Bash command,
    # so only treat their value as a bid when the command mentions "bid".
    # The legacy 0.12 input/type form was already bid-specific.
    legacy = re.search(r'browser-use\s+(?:input|type)\s', command, re.I)
    if not legacy and 'bid' not in command.lower():
        return None
    raw = m.group(1).replace(',', '')
    if raw.count('.') > 1:
        return (
            f'Bid value {raw!r} has more than one decimal point — a '
            'concatenation: the Shadow-DOM bid input did not clear before '
            'you typed. Clear it FULLY (keys "Control+a") , retype the exact '
            'target, verify the cell shows exactly that value, then commit. '
            'A concatenated bid is a real-money error.'
        )
    try:
        val = float(raw)
    except ValueError:
        return None
    # Fire the ceiling on a decimal (a CPC bid is a currency decimal, so
    # 113.0 is caught in any context) OR in an explicit bid context ("bid"
    # in the command → an integer bid like 999 is still caught). The legacy
    # input form WITHOUT "bid" is OTP/postal/quantity-ambiguous — integers
    # there pass (firing on a 6-digit OTP blocked live login on two stores).
    if val > _BID_SANITY_CEILING and ('.' in raw or 'bid' in command.lower()):
        return (
            f'Bid value {raw} exceeds the {_BID_SANITY_CEILING:g} sanity '
            'ceiling — almost certainly a clear-failure concatenation (e.g. '
            'target 3.0 typed into a field still showing 11 → 113.0). Clear '
            'the field FULLY (keys "Control+a"), retype the exact target, '
            'verify, then commit. A real CPC bid is single-digit; if you '
            'truly intend a bid this high, it is not in the audit report.'
        )
    return None


# ── Ordered Bash deny chain ─────────────────────────────────────────


def first_bash_deny(command, task_dir=None, catalog_read=False):
    """Run the ordered Bash PreToolUse guards; first deny wins.

    Returns ``(label, reason)`` for the first guard that denies
    ``command``, else ``None``. The guard set comes from the
    :mod:`app.plugins` registry (OSS guards via the builtin plugin;
    any customer guards via their own externally-installed plugin
    wheels) — so core no longer names a customer guard here. The builtin
    registers the OSS guards in their historical order:

      1. unscoped pkill/killall (sibling-task safety)
      2. concatenated/oversized bid value (real-money typo)
      3. Pix transfer to a key not in the store config (money-safety)
      4. report-script guard (AD_AUDIT is hand-authored via Edit)
      5. catalog-first (no fs search of knowledge/ or stores/ first)

    Each registered guard has the uniform signature
    ``check(command, task_dir, catalog_read)`` so the chain stays one
    testable unit regardless of which args a guard actually uses.
    """

    for label, check in registered_pretool_gates():
        reason = check(command, task_dir, catalog_read)
        if reason:
            return label, reason
    return None
