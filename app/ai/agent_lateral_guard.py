"""Lateral-movement and machine-settings denies for task agents.

The vendor-private categories (paths, ports, profile databases) live
in :mod:`app.ai.vendor_guard`; this module holds the categories that
are not about any one vendor:

- remote and lateral access (``ssh`` / ``scp`` / ``sftp`` / rsync over
  ssh, port forwards, ``~/.ssh`` reads) — incident 97db66d2's agent
  used the developer's ssh alias to reach the production Mac;
- FOREIGN and system-wide process inspection (D-C3-7): ``ps aux``,
  unscoped ``pgrep`` / ``lsof``, the Windows process tools without an
  own-PID filter, and ``/proc/<literal pid>`` reads — how both
  incidents discovered other sessions and the vendor's key. The
  self-scoped forms bash_safety's own deny message recommends
  (``pgrep -P $$``, ``pkill -P …``, ``kill <pid>``, ``ps -o … -p $$``,
  ``lsof -p``) keep working: the two guards must never contradict
  each other;
- OS-settings mutation (incident ccef25ee): registry writes, ``netsh``,
  ``setx``, ``schtasks``, ``sc config``, hosts writes,
  ``networksetup``, ``scutil --set``, ``defaults write``,
  ``launchctl load/bootstrap``, ``systemsetup``, ``crontab``,
  ``systemctl --user enable`` and persistent rc-file writes — a
  restore write is still a write;
- vibe-seller's own home beyond the task workspace: the home root,
  ``data/`` (the DB — source_guard's relative-after-``cd`` gap),
  ``profiles.json`` (provider env), ``r/`` and other sessions'
  ``bh-tmp``. The shared workspace dirs and the task's own
  screenshot dir under ``bh-tmp`` stay readable (D-C3-2).

Server-side code is unaffected: these checks sit only on agent tool
calls (the PreToolUse hook), never on the app's own subprocesses —
winchrome's Task Scheduler use and the adapter's own process tools
are not agent commands.

Limits, same as the guard: the agent runs as the same OS user with a
full shell, so a determined agent can evade by an unlisted spelling.
What this buys is that the observed behaviours cannot happen by
accident.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from app.ai.shell_patterns import COMMAND_PREFIX
from app.config import VIBE_SELLER_DIR

_HOME = str(Path.home())

# ── deny reasons ────────────────────────────────────────────────────

SSH_DENY = (
    'Blocked: task agents do not use ssh or other remote access '
    '(scp, sftp, tunnels). Everything this task needs is on this '
    'machine and in your working directory; if it is not, report '
    'that to the user.'
)
SSH_DIR_DENY = (
    "Blocked: `{path}` holds this machine's ssh configuration and "
    'keys. They are not data for this task — never read, copy or '
    'use them.'
)
PROC_DENY = (
    'Blocked: inspecting other processes (command lines, '
    'environments, open files) is not part of any task — that is how '
    "agents have leaked other sessions' data. Work from your task's "
    'own files and the wrapper instead.'
)
OS_SETTINGS_DENY = (
    'Blocked: changing operating-system or machine settings '
    '(registry, network, scheduled tasks, startup files) is not part '
    'of any task — not even to "fix" a page that will not load. '
    'Report the problem to the user instead.'
)
HOME_DENY = (
    "Blocked: `{path}` is vibe-seller's own data, not data for this "
    'task. Task files, stores and knowledge are in your working '
    'directory; your mailboxes are the db_path values '
    "`vibe_seller_email_info` gives; other tasks and the server's "
    'own state are not yours to read.'
)

# ── remote access ───────────────────────────────────────────────────

# Command-position verbs (COMMAND_PREFIX keeps a word like 'ssh'
# inside an argument or a filename out of command position).
_SSH_VERB_RE = re.compile(COMMAND_PREFIX + r'(?:ssh|scp|sftp)\b', re.IGNORECASE)
# rsync only when it goes over ssh: a remote spec (host:path) or an
# explicit -e ssh. Local-to-local rsync is left alone.
_RSYNC_SSH_RE = re.compile(
    COMMAND_PREFIX + r'rsync\b[^;&|\n]*(?:-e\s*\S*ssh|\S+@\S+:)',
    re.IGNORECASE,
)

_SSH_DIR_NAMES = ('ssh_config', 'ssh_config.d', 'known_hosts')


def ssh_verb_hit(cmd: str) -> bool:
    """An ssh-family verb (or rsync over ssh) in command position."""
    return bool(_SSH_VERB_RE.search(cmd) or _RSYNC_SSH_RE.search(cmd))


def ssh_dir_hit(path_text: str) -> str | None:
    """A ``~/.ssh``-family path in *path_text* (any spelling)."""
    lowered = path_text.replace('\\', '/').lower()
    for spelling in (
        f'{_HOME}/.ssh',
        '~/.ssh',
        '$home/.ssh',
        '%userprofile%/.ssh',
    ):
        if spelling in lowered:
            return spelling
    if re.search(r'(?:^|[/\s\'"=])\.ssh(?=$|[/\s\'"&;)])', lowered):
        return '.ssh'
    for name in _SSH_DIR_NAMES:
        if re.search(
            r'(?:^|[/\s\'"=])' + re.escape(name) + r'(?=$|[/\s\'"&;)])',
            lowered,
        ):
            return name
    return None


# ── process inspection (D-C3-7) ─────────────────────────────────────

# /proc/<pid> reads: a LITERAL pid names some other process (that is
# exactly how incident 97db66d2 read another session's environ). Own
# spellings — `$$`, `self`, `$`-variables, `$(…)` substitutions — stay
# allowed: bash_safety's scoped pgrep/pkill forms hand the agent its
# own children, and a bare `/proc/<pid>` existence check is not a
# read. System-wide /proc listings (`/proc/*/environ` globs) are too.
_PROC_LITERAL_RE = re.compile(r'/proc/\d+/[^\s]*')
_PROC_ANY_LISTING_RE = re.compile(r'/proc/\*(?:/[^\s]*)?')

_PS_INVOCATION_RE = re.compile(
    COMMAND_PREFIX + r'ps\b([^;&|\n]*)', re.IGNORECASE
)
_PS_SYSTEMIC_ARGS_RE = re.compile(
    r'(?:^|\s)(?:-?[a-zA-Z]*[eA][a-zA-Z]*|a[u]?x|ax|eww?)\s*$'
    r'|(?:^|\s)(?:aux|axu|ax|[-–][eA][fF]?|--all|eww?)\b',
)
_PS_SCOPED_RE = re.compile(r'(?:^|\s)(?:-p\b|--pid\b|-C\b)', re.IGNORECASE)
# A bare `ps` with only output-format flags of its own terminal
# (`ps`, `ps -o args`, `ps -f`): not a system-wide listing.
_PS_FORMAT_ONLY_RE = re.compile(r'^-(?:o|f|j|l|u)\b.*')
_PGREP_INVOCATION_RE = re.compile(
    COMMAND_PREFIX + r'pgrep\b([^;&|\n]*)', re.IGNORECASE
)
_PGROP_SCOPE_RE = re.compile(r'(?:^|\s)(?:-\w*P\w*|--parent)\b')
_LSOF_INVOCATION_RE = re.compile(
    COMMAND_PREFIX + r'lsof\b([^;&|\n]*)', re.IGNORECASE
)
_LSOF_SCOPE_RE = re.compile(r'(?:^|\s)-p\b', re.IGNORECASE)
_TREE_VERB_RE = re.compile(
    COMMAND_PREFIX + r'(?:pstree|top|htop)\b', re.IGNORECASE
)
# Windows process tools: system-wide by nature; allowed only with an
# own-PID filter (/FI "PID eq", -Id, -Filter "ProcessId =").
_WIN_PS_RE = re.compile(
    r'\b(?:tasklist(?:\.exe)?|taskmgr(?:\.exe)?)\b'
    r'|wmic(?:\.exe)?\s+process'
    r'|\bGet-Process\b'
    r'|\bGet-CimInstance\b[^;&|\n]{0,60}\bWin32_Process\b',
    re.IGNORECASE,
)
_WIN_PS_SCOPE_RE = re.compile(
    r'(?:PID\s+eq\b|-Id\b|-Filter\b[^;&|\n]{0,40}ProcessId\s*='
    r'|where\s+ProcessId\s*=)',
    re.IGNORECASE,
)


def process_inspection_hit(cmd: str) -> str | None:
    """Foreign / system-wide process inspection, if any.

    Self-scoped forms pass (D-C3-7): the pgrep/pkill/kill spellings
    bash_safety recommends must keep working, and ``ps -o … -p $$`` /
    ``lsof -p <child>`` are scoped reads of the agent's own subtree.
    """
    if _PROC_LITERAL_RE.search(cmd) or _PROC_ANY_LISTING_RE.search(cmd):
        return '/proc/<pid>'
    for mm in _PS_INVOCATION_RE.finditer(cmd):
        args = mm.group(1)
        if _PS_SCOPED_RE.search(args):
            continue
        stripped = args.strip()
        if not stripped or _PS_FORMAT_ONLY_RE.fullmatch(stripped):
            continue  # bare `ps`: the agent's own terminal only
        if _PS_SYSTEMIC_ARGS_RE.search(args):
            return f'ps {stripped.split()[0]}'
    for mm in _PGREP_INVOCATION_RE.finditer(cmd):
        if not _PGROP_SCOPE_RE.search(mm.group(1)):
            return 'pgrep (unscoped)'
    for mm in _LSOF_INVOCATION_RE.finditer(cmd):
        if not _LSOF_SCOPE_RE.search(mm.group(1)):
            return 'lsof (unscoped)'
    if _TREE_VERB_RE.search(cmd):
        return 'process tree'
    for mm in _WIN_PS_RE.finditer(cmd):
        window = cmd[mm.start() : mm.end() + 80]
        if not _WIN_PS_SCOPE_RE.search(window):
            return mm.group(0).split()[0]
    return None


# ── OS-settings mutation ────────────────────────────────────────────

# Distinctive verbs matched anywhere in the command (they are also
# reached inside PowerShell -Command strings); 'sc' keeps command
# position because the bare word is common.
_REG_CMD_RE = re.compile(
    r'\breg(?:\.exe)?\s+(?:add|delete|import|load|restore|unload)\b',
    re.IGNORECASE,
)
_PS_REGISTRY_RE = re.compile(
    r'\b(?:Set|New|Remove)-ItemProperty\b', re.IGNORECASE
)
_HIVE_RE = re.compile(r'\b(?:HKCU|HKLM|HKCR|HKU|HKEY_)[:\\]', re.IGNORECASE)
_NETSH_RE = re.compile(
    r'\bnetsh(?:\.exe)?\s+(?:\w+\s+){0,2}'
    r'(?:winhttp|interface|advfirewall)\b',
    re.IGNORECASE,
)
_WIN_OS_RE = re.compile(
    r'\b(?:setx(?:\.exe)?|schtasks(?:\.exe)?|networksetup|systemsetup'
    r'|crontab)\b'
    r'|\bsc(?:\.exe)?\s+(?:config|create|delete|stop|start)\b',
    re.IGNORECASE,
)
_SCUTIL_SET_RE = re.compile(r'\bscutil\b[^;&|\n]*--set', re.IGNORECASE)
_DEFAULTS_WRITE_RE = re.compile(
    r'\bdefaults\b[^;&|\n]*\b(?:write|delete|import)\b', re.IGNORECASE
)
_LAUNCHCTL_RE = re.compile(
    r'\blaunchctl\b[^;&|\n]*\b(?:load|unload|bootstrap|bootout'
    r'|enable|disable)\b',
    re.IGNORECASE,
)
_SYSTEMCTL_USER_RE = re.compile(
    r'\bsystemctl\b[^;&|\n]*--user[^;&|\n]*\b(?:enable|disable)\b',
    re.IGNORECASE,
)
# hosts file: any mention — an agent never has a legitimate need, and
# both read (recon) and write are covered by one rule.
_HOSTS_RE = re.compile(r'(?:^|[/\\\'"\s=])etc[/\\]hosts\b', re.IGNORECASE)

# rc / startup files: matched as final path segments; in Bash they are
# denied only together with a write indicator (a plain 'grep alias
# ~/.bashrc' reads and stays allowed).
_RC_FILE_RE = re.compile(
    r'(?:^|[/\s\'"=])'
    r'(\.bashrc|\.zshrc|\.bash_profile|\.zprofile|\.profile'
    r'|/etc/profile|/etc/environment)'
    r'(?=$|[\s\'"&;)])'
)
_WRITE_INDICATORS = ('>>', 'tee ', 'sed -i', 'perl -pi', 'perl -i')


def os_settings_hit(cmd: str) -> str | None:
    """An OS-settings mutation in *cmd*, if any."""
    if _REG_CMD_RE.search(cmd):
        return 'reg'
    if _PS_REGISTRY_RE.search(cmd) and _HIVE_RE.search(cmd):
        return 'registry property cmdlet'
    if _NETSH_RE.search(cmd):
        return 'netsh'
    if _WIN_OS_RE.search(cmd):
        return 'windows/mac settings verb'
    if _SCUTIL_SET_RE.search(cmd):
        return 'scutil --set'
    if _DEFAULTS_WRITE_RE.search(cmd):
        return 'defaults write'
    if _LAUNCHCTL_RE.search(cmd):
        return 'launchctl'
    if _SYSTEMCTL_USER_RE.search(cmd):
        return 'systemctl --user'
    if _HOSTS_RE.search(cmd):
        return 'hosts file'
    m = _RC_FILE_RE.search(cmd)
    if m and _rc_write_indicator(cmd, m.end()):
        return f'rc file {m.group(1)}'
    return None


def _rc_write_indicator(cmd: str, name_end: int) -> bool:
    """Whether *cmd* writes to the rc file the regex matched.

    ``cat ~/.bashrc > /tmp/x`` reads it (allowed); ``echo x >>
    ~/.profile`` and ``sed -i … ~/.bashrc`` write it (denied). The
    redirect test looks for a ``>`` BEFORE the path, so a redirect
    FROM the rc file elsewhere stays a read.
    """
    if any(ind in cmd for ind in _WRITE_INDICATORS):
        return True
    before = cmd[:name_end]
    for mm in re.finditer(r'(?<![02>])>(?!>)', before):
        if _RC_FILE_RE.search(cmd[mm.end() :]):
            return True
    return False


# ── the vibe-seller home ────────────────────────────────────────────

# The root and the closed children are denied; the shared workspace
# dirs stay open. 'data' is source_guard's DATA_DIR (kept here for
# the relative-after-cd spelling and non-file tools).
_HOME_OPEN_CHILDREN = (
    'tasks',
    'stores',
    'knowledge',
    'tools',
    'downloads',
    'bin',
    '.claude',
    'store-data',
)
_HOME_CLOSED_CHILDREN = ('data', 'profiles.json', 'r', 'bh-tmp')


def _home_spellings() -> tuple[str, ...]:
    home = str(Path(VIBE_SELLER_DIR).resolve())
    out = {home}
    if home.startswith(_HOME + os.sep):
        rest = home[len(_HOME) :]
        out.add('~' + rest)
        out.add('$HOME' + rest)
        out.add('%USERPROFILE%' + rest.replace('/', os.sep))
    return tuple(sorted(out))


_HOME_SPELLINGS = _home_spellings()

# <home> bare (end of token, a trailing '/', or a separator right after
# the slash), or <home> followed by a CLOSED child. Paths into the open
# children (tasks/stores/…) and into unknown top-level names do not
# match: the shared workspace is the task's own, and a future home
# entry gets its own rule when it appears.
HOME_BARE_RE = re.compile(
    '(?:'
    + '|'.join(re.escape(s) for s in _HOME_SPELLINGS)
    + r')(?:(?=$|[\s\'";|&)])|(?=/($|[\s\'";|&)])))'
)
_HOME_CLOSED_RE_TEMPLATE = (
    '(?:'
    + '|'.join(re.escape(s) for s in _HOME_SPELLINGS)
    + r')/(?:data|profiles\.json|r)(?=$|[/\s\'";|&)])'
)
# bh-tmp is closed ONLY for other sessions: browser-use writes this
# task's screenshots to ``<home>/bh-tmp/<session>/``. The session kinds
# a task really gets (derived from the generated wrappers, D-C3-9):
# the store default ``{slug}-{task8}``, worker slots
# ``{slug}-{task8}-w{N}`` and the store-less ``web-{task8}`` all carry
# the task's 8-char id prefix; the store's ``{slug}-aux`` session does
# NOT (the wrapper assigns it that exact name in task mode), so it is
# exempted by exact owned name instead.
_HOME_BH_TMP_RE_TEMPLATE = (
    '(?:'
    + '|'.join(re.escape(s) for s in _HOME_SPELLINGS)
    + r')/bh-tmp/(?![^/\s\'"]*(?:{owned}))'
)
_HOME_RE_CACHE: dict[tuple, re.Pattern] = {}


def home_deny_re(
    task_prefix: str, owned_session_names: frozenset[str] = frozenset()
) -> re.Pattern:
    """The home-deny regex for one task (cached by prefix + names)."""
    key = (task_prefix, tuple(sorted(owned_session_names)))
    rx = _HOME_RE_CACHE.get(key)
    if rx is None:
        owned = [re.escape(task_prefix)] if task_prefix else []
        owned += [re.escape(n) for n in sorted(owned_session_names)]
        # Nothing owned (unit calls without a task): every bh-tmp child
        # is another session's — but an EMPTY lookahead would deny even
        # the literal text, so fall back to a never-matching pattern.
        pattern = (
            _HOME_CLOSED_RE_TEMPLATE
            + '|'
            + _HOME_BH_TMP_RE_TEMPLATE.format(owned='|'.join(owned) or r'(?!)')
        )
        rx = re.compile(pattern)
        _HOME_RE_CACHE[key] = rx
    return rx


def under_closed_home(
    expanded: str,
    task_prefix: str,
    owned_session_names: frozenset[str] = frozenset(),
) -> bool:
    """Whether *expanded* is the home root or a closed child of it.

    ``bh-tmp`` children carrying this task's id prefix, or named
    exactly one of its owned sessions (the ``{slug}-aux`` kind that
    carries no prefix), are open; every other session's is not.
    """
    home = str(Path(VIBE_SELLER_DIR).resolve())
    if not expanded.startswith(home):
        return False
    rest = expanded[len(home) :].lstrip('/').replace('\\', '/')
    if not rest:
        return True
    first = rest.split('/', 1)[0]
    if first == 'bh-tmp':
        session = rest.split('/', 2)[1] if '/' in rest else ''
        return not (
            (task_prefix and task_prefix in session)
            or session in owned_session_names
        )
    return first in _HOME_CLOSED_CHILDREN
