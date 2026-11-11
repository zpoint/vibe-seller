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

from app.ai.shell_patterns import COMMAND_PREFIX
from app.plugins import registered_pretool_gates

# Command position only (``COMMAND_PREFIX``): ``grep pkill file`` passes.
_PKILL_INVOCATION = re.compile(COMMAND_PREFIX + r'pkill\b([^;|&\n]*)')
_KILLALL_INVOCATION = re.compile(COMMAND_PREFIX + r'killall\b')

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


# ── Pre-live bid-value sanity (the 10× / concatenation overspend bug) ────
#
# The Shadow-DOM bid input does not reliably clear, so a 1.30 target can
# become 11.3 or 3.002.27 (a real-money overspend that shipped live). The
# typed value is visible in the Bash command, so we deny the value-SHAPE
# pathology before it goes live. This guard does NOT know the report
# target (index→keyword map isn't in the command) — catching the wrong
# target is the ``ad_changes`` exit gate's job (every EXECUTION_LOG row
# against the approved CHANGES); this is purely the absurd-magnitude /
# multi-decimal catch. The value appears in a
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
    registers: unscoped pkill/killall (sibling-task safety),
    concatenated/oversized bid value (real-money typo), and takeover
    of ``generated_images/`` (those files' provenance claim).

    Each registered guard has the uniform signature
    ``check(command, task_dir, catalog_read)`` so the chain stays one
    testable unit regardless of which args a guard actually uses.
    """

    for label, check in registered_pretool_gates():
        reason = check(command, task_dir, catalog_read)
        if reason:
            return label, reason
    return None
