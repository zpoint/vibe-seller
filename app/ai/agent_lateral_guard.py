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
WRAPPER_READ_DENY = (
    'Blocked: `{path}` is a vibe-seller browser wrapper, and it embeds '
    'a task-scoped token. RUN it (it is how you drive the store '
    'browser); never read, grep or copy it.'
)
FOREIGN_HOME_DENY = (
    'Blocked: `{path}` belongs to ANOTHER vibe-seller install — its '
    'profiles.json holds provider credentials, its database the '
    "server's own state. Only THIS task's server home is yours, and "
    'only its workspace part.'
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
    '.claude',
    'store-data',
)
# ``vendors`` joined the closed set with incident 3 (an agent read a
# vendor's login-state.json — server-kept credentials). ``bin`` is
# closed for PATH checks (Read/Grep/Glob — the wrappers embed a
# task-scoped JWT) but NOT for Bash text, where EXECUTING a wrapper is
# the product path: :func:`wrapper_read_hit` is position-aware there.
_HOME_CLOSED_CHILDREN = (
    'data',
    'profiles.json',
    'r',
    'bh-tmp',
    'vendors',
    'bin',
)


# ── ONE normal form for every path the home family matches ────────
# Found by the native-Windows proofs, twice: the wrapper matcher
# flattened the command's backslashes but not its regex spellings
# (proof 3), and under_closed_home lstripped only '/' so backslash-
# joined children (\data, \vendors) passed. Path normalisation had
# been done ad hoc per matcher; now ONE helper is applied to BOTH the
# command / Write content AND the home spelling, and every home-family
# matcher runs in that form.


def normalize_home(path: str) -> str:
    """The normal form of an absolute path (no prefix expansion)."""
    return re.sub(r'(?<!:)/{2,}', '/', path.replace('\\', '/')).lower()


def normalize_path_text(text: str) -> str:
    """The single normal form for path matching.

    - separators become ``/`` and runs collapse (``http://`` survives)
    - case folds the Windows way: a differently-cased spelling of the
      same directory is the same file there, and on POSIX it is a path
      that does not exist — matching it is harmless either way
    - ``~/``, ``$HOME/`` and ``%USERPROFILE%/`` expand to the user's
      home, so every spelling of the vibe home reaches the matchers as
      its absolute form (the multi-spelling regexes collapse to one)
    """
    t = text.replace('\\', '/')
    t = re.sub(r'(?<!:)/{2,}', '/', t)
    t = t.replace('%USERPROFILE%/', _HOME_NORM + '/')
    t = t.replace('$HOME/', _HOME_NORM + '/')
    t = t.replace('~/', _HOME_NORM + '/')
    return t.lower()


#: the user's home, normalised (prefix expansion target).
_HOME_NORM = normalize_home(str(Path.home()))

#: the vibe home, normalised — the ONE spelling every matcher uses.
_NORM_HOME = normalize_home(str(Path(VIBE_SELLER_DIR).resolve()))


def norm_home_now() -> str:
    """The vibe home's normal form, read at CALL time (test seam)."""
    return _NORM_HOME


# <home> bare (end of token, a trailing '/', or a separator right after
# the slash), or <home> followed by a CLOSED child. Paths into the open
# children (tasks/stores/…) and into unknown top-level names do not
# match: the shared workspace is the task's own, and a future home
# entry gets its own rule when it appears.
def _home_bare_re(home: str) -> re.Pattern:
    return re.compile(
        re.escape(home) + r'(?:(?=$|[\s\'";|&)])|(?=/($|[\s\'";|&)])))'
    )


def _closed_re_for(home: str) -> str:
    return (
        re.escape(home) + r'/(?:data|profiles\.json|r|vendors)'
        r'(?=$|[/\s\'";|&)])'
    )


def _bh_tmp_re_for(home: str) -> str:
    # bh-tmp is closed ONLY for other sessions: browser-use writes
    # this task's screenshots to ``<home>/bh-tmp/<session>/``. The
    # session kinds a task really gets (D-C3-9) all carry the task's
    # 8-char id prefix; the store's ``{slug}-aux`` session does NOT,
    # so it is exempted by exact owned name instead.
    return re.escape(home) + r'/bh-tmp/(?![^/\s\'"]*(?:{owned}))'


HOME_BARE_RE = _home_bare_re(_NORM_HOME)


def home_bare_re() -> re.Pattern:
    """The bare-home regex, read at CALL time (test seam — a direct
    ``from … import`` freezes the module-level original)."""
    return _home_bare_re(norm_home_now())


_HOME_RE_CACHE: dict[tuple, re.Pattern] = {}


def home_deny_re(
    task_prefix: str, owned_session_names: frozenset[str] = frozenset()
) -> re.Pattern:
    """The home-deny regex for one task (cached by prefix + names)."""
    key = (
        task_prefix,
        tuple(sorted(owned_session_names)),
        norm_home_now(),
    )
    rx = _HOME_RE_CACHE.get(key)
    if rx is None:
        owned = [re.escape(task_prefix.lower())] if task_prefix else []
        owned += [re.escape(n.lower()) for n in sorted(owned_session_names)]
        # Nothing owned (unit calls without a task): every bh-tmp child
        # is another session's — but an EMPTY lookahead would deny even
        # the literal text, so fall back to a never-matching pattern.
        home = norm_home_now()
        pattern = (
            _closed_re_for(home)
            + '|'
            + _bh_tmp_re_for(home).format(owned='|'.join(owned) or r'(?!)')
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
    carries no prefix), are open; every other session's is not. The
    path is compared in the ONE normal form — separators, case and
    duplicate slashes alike (proof: ``\\data`` used to pass a
    ``lstrip('/')``).
    """
    home = norm_home_now()
    norm = normalize_path_text(expanded)
    if not norm.startswith(home):
        return False
    rest = norm[len(home) :].lstrip('/')
    if not rest:
        return True
    first = rest.split('/', 1)[0]
    if first == 'bh-tmp':
        session = rest.split('/', 2)[1] if '/' in rest else ''
        return not (
            (task_prefix and task_prefix.lower() in session)
            or session in {n.lower() for n in owned_session_names}
        )
    return first in _HOME_CLOSED_CHILDREN


# ── the store wrappers: readable never, executable always ───────────
# Incident 3 (task 601a2d79): the agent READ its store's browser-use
# wrapper — ``<home>/bin/<slug>/browser-use`` — to lift the task-scoped
# JWT embedded in it. Executing that same wrapper is the product path,
# so the Bash-text rule is POSITION-aware: an occurrence in command
# position (the first token, after a separator, after an env-var
# prefix, or inside a quoted command an introducer like ``bash -c``
# owns) is allowed; anywhere else — an argument to cat, grep, a copy —
# is denied. Path checks (Read/Grep/Glob tools) go through
# :func:`under_closed_home`, where ``bin`` is simply closed: no agent
# tool ever needs to read a wrapper.
def _wrapper_file_re(home: str) -> re.Pattern:
    """The wrapper-file regex, from the ONE normalised home spelling
    (the command text is normalised before matching — both sides in
    the same form)."""
    return re.compile(
        re.escape(home) + r'/bin/[^\s\'";|&)]*?/(?:browser-use|_guard)'
    )


_WRAPPER_FILE_RE = _wrapper_file_re(_NORM_HOME)
# Tokens that hand the command position to what follows them. A
# wrapper after one of these (possibly behind flags, numbers or env
# assignments) is being RUN.
_COMMAND_INTRODUCERS = frozenset({
    'bash',
    'sh',
    'dash',
    'zsh',
    'env',
    'exec',
    'command',
    'sudo',
    'nohup',
    'setsid',
    'timeout',
    'nice',
    'stdbuf',
    'xargs',
    'start',
    'start-process',
})
_ENV_ASSIGN_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=\S*')
_SEPARATOR_CHARS = ';|&\n(`\r$'


def _command_position_before(text: str, start: int) -> bool:
    """Whether *start* in *text* is a command position.

    Walks back over whitespace and quotes; a separator (or the start
    of the text) means command position. Otherwise the tokens before
    it decide: env assignments, flags and numbers pass through to the
    command word, which must be an introducer (``bash -c "…"``,
    ``nohup …``, ``timeout 30 …``) for this to stay a command position.
    """
    i = start - 1
    while i >= 0 and text[i] in ' \t\'"':
        i -= 1
    if i < 0 or text[i] in _SEPARATOR_CHARS:
        return True
    # gather up to three tokens before the path
    tokens = []
    for _ in range(3):
        j = i
        while j >= 0 and text[j] not in ' \t\n;|&(`\r$':
            j -= 1
        tokens.append(text[j + 1 : i + 1])
        i = j - 1
        while i >= 0 and text[i] in ' \t\'"':
            i -= 1
        if i < 0 or text[i] in _SEPARATOR_CHARS:
            break
    for token in tokens:
        if not token:
            continue
        if _ENV_ASSIGN_RE.fullmatch(token):
            continue  # VAR=x — still waiting for the command word
        if token.startswith('-') or token.isdigit():
            continue  # a flag or a count — same
        return token.lower() in _COMMAND_INTRODUCERS
    # Only assignments, flags and quotes before the path (or the text
    # start): the path itself is the command — an env-prefix line.
    return True


def wrapper_read_hit(text: str) -> str | None:
    """A wrapper path used anywhere but command position (incident 3).

    Returns the matched path when the occurrence reads or copies the
    wrapper; ``None`` when every occurrence is an execution of it.
    Runs in the ONE normal form: the text and the regex both
    normalised (proof 3 found the flattened-text/raw-regex mismatch).
    """
    if '/bin/' not in text and '\\bin\\' not in text:
        return None
    norm = normalize_path_text(text)
    rx = _wrapper_file_re(norm_home_now())
    for m in rx.finditer(norm):
        if _command_position_before(norm, m.start()):
            continue
        return m.group(0)
    return None


# ── other vibe-seller homes (incident 4) ────────────────────────────
# A test agent read PRODUCTION's ~/.vibe-seller/profiles.json on the
# shared Mac — a different VIBE_HOME than its task's server. Any
# vibe-seller home that is not the task's own is off-limits in full:
# the default-named family (~/.vibe-seller and its -<suffix>
# siblings), and the two files that make a directory LOOK like a vibe
# seller home (data/vibe_seller.db, profiles.json) when they sit in an
# absolute path outside the task's own home. A RELATIVE profiles.json
# (a file in the task's own workspace) stays allowed.
_VIBE_HOME_FILE_RE = re.compile(
    r'(?<![\w.-])(profiles\.json|vibe_seller\.db)(?![\w.-])'
)
_PATH_CHARS = re.compile(r'[a-z0-9_\-./$%~]')


def foreign_home_hit(norm_text: str) -> str | None:
    """A foreign vibe-seller home in ALREADY-NORMALISED *norm_text*.

    Returns the matched text, or ``None``. The task's own home is
    exempt — its finer-grained rules (closed children, wrappers)
    decide.
    """
    home = norm_home_now()
    # the default-named family under the user's home
    family = re.compile(
        re.escape(_HOME_NORM) + r'/\.vibe-seller[\w.-]*(?=$|[/\s\'";|&)])'
    )
    for m in family.finditer(norm_text):
        token = m.group(0)
        if token != home and not token.startswith(home + '/'):
            return token
    # the look-like-a-home files, in absolute paths only
    for m in _VIBE_HOME_FILE_RE.finditer(norm_text):
        i = m.start()
        while i > 0 and _PATH_CHARS.match(norm_text[i - 1]):
            i -= 1
        path = norm_text[i : m.end()]
        if re.match(r'(?:/|[a-z]:/)', path) and not (
            path == home or path.startswith(home + '/')
        ):
            return m.group(0)
    return None


# ── agent-built port forwarders ─────────────────────────────────────
# A native-Windows task agent built its OWN 7777→7797 TCP forwarder to
# get around the MCP port bug — the same class as the 9222 CDP bridge
# and the ssh tunnels. A relay around ANY port walks around the
# architecture, so the listen shapes are denied regardless of port (the
# port-specific rules live in vendor_guard). Residual, stated in the
# PR: a hand-written forwarder in a file the agent wrote is caught by
# task-end REAPING, not necessarily by this deny.

_SOCAT_LISTEN_RE = re.compile(
    COMMAND_PREFIX + r'socat\b[^;&|\n]*[a-z-]*listen[^\n]*'
    r'|socat\b[^;&|\n]*\s-l\b',
    re.IGNORECASE,
)
_NC_LISTEN_RE = re.compile(
    r'\b(?:nc|ncat|netcat)\b[^;&|\n]*\s(?:-l\w*|--listen)\b',
    re.IGNORECASE,
)
_NETSH_PORTPROXY_RE = re.compile(
    r'\bnetsh(?:\.exe)?\s+interface\s+portproxy\b', re.IGNORECASE
)
_FW_RULE_RE = re.compile(r'\bNew-NetFirewallRule\b', re.IGNORECASE)
_PY_BIND_RE = re.compile(
    r'(?:\.bind\s*\(|start_server|create_server|\.listen\s*\()', re.IGNORECASE
)
_PY_CONNECT_RE = re.compile(
    r'(?:\.connect\s*\(|\.connect_ex\s*\(|create_connection)',
    re.IGNORECASE,
)

FORWARDER_DENY = (
    'Blocked: building a network relay or port forwarder (a listener '
    'plus a connection) is not part of any task — it walks around how '
    'the server and the task browser are reached. Use the tools and '
    'endpoints the task already has.'
)


def port_forwarder_hit(cmd: str) -> str | None:
    """An agent-built relay in *cmd* (Bash text or written content):
    socat/nc listen modes, ``netsh interface portproxy``, a firewall
    rule, or a Python snippet that both BINDS and CONNECTS."""
    if _SOCAT_LISTEN_RE.search(cmd):
        return 'socat listen'
    if _NC_LISTEN_RE.search(cmd):
        return 'nc listen'
    if _NETSH_PORTPROXY_RE.search(cmd):
        return 'netsh portproxy'
    if _FW_RULE_RE.search(cmd):
        return 'New-NetFirewallRule'
    if _PY_BIND_RE.search(cmd) and _PY_CONNECT_RE.search(cmd):
        return 'python bind+connect'
    return None
