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
# typed value is visible in the Bash command, so the guard denies what
# the command and the task's own frozen approval already prove:
#
# * a value-SHAPE pathology — one typed value with two decimal points is
#   a concatenation, never a bid (``3.002.27``);
# * in an explicit bid context (the command mentions "bid"), a value the
#   task's FROZEN APPROVAL (``CHANGES.tsv`` frozen at the ``execute``
#   declaration) does not hold. 113.0 typed while the approval says 3.0
#   is the shipped clear-failure, and the approval is what says so.
#
# There is NO magnitude ceiling: JPY/INR bids are legitimately 50–300,
# and "how high is a real bid" is a currency-and-approval question, not
# a constant. With no frozen approval to read (a plain audit, an
# investigate, a corrupt file) only the shape check fires; the
# ``ad_changes`` exit gate reconciles every executed bid against the
# approval when the result ships. This guard also never knows the
# report target (the index→keyword map is not in the command) — the
# wrong TARGET stays ``ad_changes``' job. The value appears in a
# legacy ``browser-use input <idx> "1.30"``, a 0.13 helper
# (``type_text``/``fill_input``), or a JS ``el.value = "1.30"`` assign.
#
# Reading the approval is file I/O and this runs on the hook path: the
# caller (``claude_backend_hooks``) wraps the chain in one
# ``asyncio.to_thread`` per Bash call, so the read lands off the loop.
_BID_INPUT_RE = re.compile(
    r'(?:'
    r'browser-use\s+(?:input|type)\s+\d+\s+["\']?'
    r'|\btype_text\(\s*["\']'
    r'|\bfill_input\([^)]*["\']\s*,\s*["\']'
    r'|\.value\s*=\s*["\']'
    r')([0-9][0-9.,]*)',
    re.IGNORECASE,
)
# How close a typed value must sit to an approved bid — the same
# tolerance ``ad_changes`` reconciles executions with.
_BID_MATCH_TOLERANCE = 0.01

#: A task dir is ``.../tasks/<task_id>``; only that shape locates an
#: approval (the same id shape ``app/routers/ads.py`` pins).
_TASK_DIR_ID_RE = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')


def _frozen_approval_bids(task_dir) -> frozenset[float] | None:
    """The bids frozen for the task owning *task_dir*, None if there is
    no readable frozen approval. Blocking; call off the event loop."""
    if not task_dir:
        return None
    name = str(task_dir).rstrip('/\\').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    if not _TASK_DIR_ID_RE.fullmatch(name.lower()):
        return None
    # Lazy: bash_safety stays a leaf (regex + the plugin registry); the
    # gate module it needs here pulls the DB and the workspace.
    from app.ai.stop_gates.ad_changes import (  # noqa: PLC0415
        approved_bid_values,
    )

    return approved_bid_values(name)


def _approved_list(bids: frozenset[float]) -> str:
    listed = ', '.join(f'{b:g}' for b in sorted(bids)[:10])
    if len(bids) > 10:
        listed += ', …'
    return listed or 'none — the frozen approval holds no bid'


def check_bid_value_shape(command: str, task_dir=None) -> str | None:
    """Deny a typed bid that is a concatenation, or one the task's
    frozen approval does not hold.

    Returns a deny reason for a value with >1 decimal point
    (``3.002.27``) in any bid-typing form, and — in an explicit bid
    context with a frozen approval — a value no approved row carries
    (``113.0`` typed when the approval says ``3.0``). With no approval
    frozen there is no ceiling: the magnitude question belongs to the
    currency and the user's decision, not to a constant here. Deny-only
    — never auto-rewrites a live bid.
    """
    if not command:
        return None
    matches = list(_BID_INPUT_RE.finditer(command))
    if not matches:
        return None
    # The 0.13 helpers (type_text/fill_input/.value=) set ANY field —
    # dates, OTP, quantities — and this gate runs on every Bash command,
    # so only treat their value as a bid when the command mentions "bid".
    # The legacy 0.12 input/type form was already bid-specific.
    legacy = re.search(r'browser-use\s+(?:input|type)\s', command, re.I)
    bid_context = 'bid' in command.lower()
    if not legacy and not bid_context:
        return None
    values = []
    for m in matches:
        raw = m.group(1).replace(',', '')
        if raw.count('.') > 1:
            return (
                f'Bid value {raw!r} has more than one decimal point — a '
                'concatenation: the Shadow-DOM bid input did not clear '
                'before you typed. Clear it FULLY (keys "Control+a"), '
                'retype the exact target, verify the cell shows exactly '
                'that value, then commit. A concatenated bid is a '
                'real-money error.'
            )
        values.append(raw)
    if not bid_context:
        return None
    bids = _frozen_approval_bids(task_dir)
    if bids is None:
        return None
    for raw in values:
        try:
            val = float(raw)
        except ValueError:
            continue
        if not any(abs(val - b) <= _BID_MATCH_TOLERANCE + 1e-9 for b in bids):
            return (
                f'Bid value {raw} is not a bid the frozen approval holds '
                f'(approved: {_approved_list(bids)}). Clear the field '
                '(keys "Control+a") and type the exact value the user '
                'approved. If the user amended a bid after the approval '
                'was frozen, bring CHANGES.tsv to what they said and '
                'declare kind "execute" again — the approval is what this '
                'run is checked against, and a bid outside it is a '
                'change nobody approved.'
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
    registers: unscoped pkill/killall (sibling-task safety), a typed
    bid that concatenates or is outside the frozen approval
    (real-money typo), and takeover of ``generated_images/`` (those
    files' provenance claim).

    Each registered guard has the uniform signature
    ``check(command, task_dir, catalog_read)`` so the chain stays one
    testable unit regardless of which args a guard actually uses.
    """

    for label, check in registered_pretool_gates():
        reason = check(command, task_dir, catalog_read)
        if reason:
            return label, reason
    return None
