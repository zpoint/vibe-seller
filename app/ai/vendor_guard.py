"""The vendor private-data guard: deny by WHERE an access goes.

Observed (runaway transcripts, incidents 97db66d2 and ccef25ee): a
task agent stuck at a login page grepped the browser vendor's on-disk
cache for its API key, copied another profile's ``Cookies`` / ``Login
Data`` / ``places.sqlite`` to /tmp, curled the vendor's local API
port, and built its own CDP bridge on 9222. Prompt rules alone did
not hold; this module is the line in code, and it judges by WHERE the
access goes, not which command performs it.

This module owns the VENDOR categories:

1. Vendor private data: the ``private_paths`` / ``local_ports`` every
   registered vendor spec declares (dir names as whole path segments
   in ABSOLUTE paths, path templates with the usual '~' / '$HOME' /
   '%APPDATA%' / '/mnt/c/Users/*' spellings, and ``find -name`` globs).
2. Browser-profile databases by file name (``Cookies``, ``Login
   Data``, ``Web Data``, ``formhistory.sqlite``, ``places.sqlite``,
   ``cookies.sqlite``, ``logins.json``) anywhere in a command or as a
   file tool's basename.
3. Local ports that are not the task's own: every vendor's declared
   local API port, the 9229 Node inspector, and the CDP proxy range —
   in connect contexts (URLs, ``127.0.0.1:<port>``) and in listener /
   bind contexts (an agent building its own CDP route, incident
   97db66d2's ``cdp_bridge.py`` on 9222 — matched in Bash text AND in
   Write/Edit content, where the incident's script carried the port).
   The task's own store proxy port stays reachable — the wrapper's
   helpers need it.

The non-vendor categories (ssh/lateral access, process inspection,
OS-settings mutation, the vibe-seller home rules) live in
:mod:`app.ai.agent_lateral_guard`; the entry point below chains both.

LIMITS (stated plainly, per the user's final call): this is
interception of our own tool calls, not a sandbox. The agent runs as
the same OS user with a full shell, so a DETERMINED agent can still
evade — an unlisted spelling, a script written past the Write check,
an obfuscated interpreter one-liner. What this guard plus the output
tripwire (:mod:`app.ai.output_tripwire`) plus task-end reaping
(:mod:`app.task_reaper`) buy is that the observed behaviours cannot
happen BY ACCIDENT, and that deliberate evasion is loud: the tripwire
stops the task on a leak, and the reaper tears down whatever the agent
left running. No OS-user isolation is attempted.

Server-side code is unaffected: the guard sits only on agent tool
calls (the PreToolUse hook), never on the app's own subprocesses.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re

from app.ai.agent_lateral_guard import (
    FOREIGN_HOME_DENY,
    FORWARDER_DENY,
    HOME_DENY,
    OS_SETTINGS_DENY,
    PROC_DENY,
    SSH_DENY,
    SSH_DIR_DENY,
    WRAPPER_READ_DENY,
    cd_home_relative,
    foreign_home_hit,
    home_bare_re,
    home_deny_re,
    normalize_path_text,
    os_settings_hit,
    port_forwarder_hit,
    process_inspection_hit,
    ssh_dir_hit,
    ssh_verb_hit,
    under_closed_home,
    wrapper_read_hit,
)
from app.browser.vendors import registered_vendor_specs

logger = logging.getLogger(__name__)

_HOME = str(Path.home())

# ── browser-profile databases ───────────────────────────────────────

# DISTINCTIVE names are denied anywhere in a command (word boundary)
# and as any file tool's basename — no legitimate file is called
# ``formhistory.sqlite``. GENERIC names (the Chrome/Firefox profile
# files that are also ordinary words) are denied only as a PATH
# SEGMENT in command text, or as a file-tool basename inside a path
# with profile-dir shape — ``Accept Cookies`` in a browser-use
# heredoc, ``document.cookie`` in a js step and a ``Cookies`` pandas
# column are all frequent, ordinary agent commands and must pass
# (D-C3-8).
_PROFILE_DB_GENERIC = (
    'Cookies',
    'Web Data',
    'Login Data',
    'History',
    'Preferences',
    'Local State',
)
_PROFILE_DB_DISTINCTIVE = (
    'formhistory.sqlite',
    'places.sqlite',
    'cookies.sqlite',
    'logins.json',
    'key4.db',
)
_PROFILE_DB_DISTINCTIVE_RE = re.compile(
    r'(?<![.\w])('
    + '|'.join(re.escape(n) for n in _PROFILE_DB_DISTINCTIVE)
    + r')(?![.\w])',
    re.IGNORECASE,
)
# A generic profile name as a path segment (``/Cookies``, ``\Login
# Data``), never as a bare word.
_PROFILE_DB_SEGMENT_RE = re.compile(
    r'[/\\](?:' + '|'.join(re.escape(n) for n in _PROFILE_DB_GENERIC) + r')'
    r'(?=$|[/\\\s\'";&|)])'
)
# A path with browser-profile-dir shape: a ``User Data`` / ``Default``
# / ``Profile N`` segment, or anything under a declared private path.
_PROFILE_DIR_SHAPE_RE = re.compile(
    r'[/\\](?:User Data|Default|Profile \d+)(?:[/\\]|$)', re.IGNORECASE
)

# ── ports ───────────────────────────────────────────────────────────

# Connect contexts: a port in a URL or a localhost host:port pair.
_URL_PORT_RE = re.compile(
    r'(?:https?|wss?)://[^\s/\'"]*?:(\d{2,5})', re.IGNORECASE
)
_HOSTPORT_RE = re.compile(
    r'(?:127\.0\.0\.1|localhost|\[::1\]):(\d{2,5})', re.IGNORECASE
)
# Listener / bind contexts: an agent starting its own server on a port
# (the cdp_bridge.py shape — matched in Bash text AND in Write/Edit
# content, because the incident's script carried the port only there).
_LISTEN_PORT_RES = (
    re.compile(r'port["\']?\s*[=:]\s*(\d{4,5})', re.IGNORECASE),
    re.compile(r'bind\s*\([^)]*?(\d{4,5})', re.IGNORECASE),
    re.compile(r'http\.server\s+(\d{4,5})', re.IGNORECASE),
    re.compile(r'tcp-listen:(\d{4,5})', re.IGNORECASE),
    re.compile(r'\b(?:nc|ncat|netcat)\b[^\n]*?(\d{4,5})', re.IGNORECASE),
)

# The CDP proxy range the manager allocates from (9222 base + per-store
# offsets, 9322 in demo mode) plus the Node inspector. An agent may
# reach only its OWN task's proxy port (resolved per task from its
# store's browser session / the web port).
_CDP_PORT_RANGE = range(9222, 9401)
_INSPECTOR_PORT = 9229

# ── vendor-spec matchers (rebuilt when the registry changes) ────────


class _VendorMatcher:
    """Compiled private-path / port matchers for every vendor spec."""

    def __init__(self):
        self.dir_names: tuple[tuple[str, str], ...] = ()
        # (vendor key, lowered dir name)
        self.templates: tuple[tuple[str, re.Pattern], ...] = ()
        self.ports: dict[int, str] = {}
        self._build()

    def _build(self) -> None:
        dirs: list[tuple[str, str]] = []
        templates: list[tuple[str, re.Pattern]] = []
        ports: dict[int, str] = {}
        for key, spec in registered_vendor_specs().items():
            for entry in spec.private_paths:
                name = entry.replace('\\', '/').rstrip('/').lower()
                if '/' not in name:
                    dirs.append((key, name))
                    continue
                final = name.rsplit('/', 1)[-1]
                if final and final != '*':
                    dirs.append((key, final))
                templates.extend(
                    (key, rx) for rx in self._template_regexes(name)
                )
            for port in spec.local_ports:
                ports.setdefault(int(port), key)
        self.dir_names = tuple(dirs)
        self.templates = tuple(templates)
        self.ports = ports

    @staticmethod
    def _template_regexes(name: str) -> list[re.Pattern]:
        """Every spelling of a lowered, slash-normalised template."""
        out: list[re.Pattern] = []

        def add(path: str) -> None:
            # '*' matches one segment; the end needs a path boundary so
            # '/app' does not catch '/apple'.
            pattern = re.escape(path).replace(r'\*', r'[^/\\\'"\s]+')
            out.append(
                re.compile(pattern + r'(?=$|[/\s\'";|&)])', re.IGNORECASE)
            )

        add(name)
        rest = name[2:] if name.startswith('~/') else ''
        if rest:
            add(f'{_HOME}/{rest}')
            add(f'$HOME/{rest}')
            add(f'%USERPROFILE%/{rest}')
            for win_root in ('c:/users/*', '/mnt/c/users/*'):
                add(f'{win_root}/{rest}')
                # Windows data dirs also live under APPDATA spellings.
                for env, sub in (
                    ('%APPDATA%', 'appdata/roaming'),
                    ('%LOCALAPPDATA%', 'appdata/local'),
                ):
                    if rest.startswith(sub + '/'):
                        tail = rest[len(sub) + 1 :]
                        add(f'{env}/{tail}')
                        add(f'{win_root}/{sub}/{tail}')
        return out


_MATCHER: _VendorMatcher | None = None
_MATCHER_KEY: tuple | None = None


def vendor_matcher() -> _VendorMatcher:
    """The process-wide matcher, rebuilt when the registry changes."""
    global _MATCHER, _MATCHER_KEY
    specs = registered_vendor_specs()
    key = tuple(sorted((k, id(v)) for k, v in specs.items()))
    if _MATCHER is None or key != _MATCHER_KEY:
        _MATCHER = _VendorMatcher()
        _MATCHER_KEY = key
    return _MATCHER


# ── text helpers ────────────────────────────────────────────────────


def _mask(text: str, paths: tuple[str, ...]) -> str:
    """Take each path (and its '~' spelling) out of *text*.

    What sits under a masked path is legitimately the task's own, so
    the segment matchers must not see its directory names.
    """
    for path in paths:
        if not path:
            continue
        p = str(path)
        spellings = {p, p.replace('\\', '/')}
        if p.startswith(_HOME + os.sep):
            spellings.add('~' + p[len(_HOME) :])
            spellings.add('$HOME' + p[len(_HOME) :])
        for s in spellings:
            if s in text:
                text = text.replace(s, '<workspace>')
    return text


# A token that starts absolutely (POSIX root, home shorthand, Windows
# env spelling, drive letter). Relative paths — ``stores/ziniao/x``,
# where a store may be slugged like a vendor — never match the
# dir-name rules.
_ABS_START_RE = re.compile(r'(?:/|~|\$|%|[A-Za-z]:[\\/])')


# ``find``-style name searches: ``-name Cookies``, ``-iname *ziniao*``.
_FIND_GLOB_RE = re.compile(
    r'(?:-iname|-name)\s*=?\s*["\']?\*?([^*"\';&|]+)\*?["\']?',
    re.IGNORECASE,
)

# Quoted paths may contain spaces (``"/mnt/c/Program Files/ziniao"``);
# blanking the spaces inside quotes keeps them one path token.
_QUOTED_SPAN_RE = re.compile(r'"[^"]*"|\'[^\']*\'')


def _quote_normalize(text: str) -> str:
    out = _QUOTED_SPAN_RE.sub(lambda m: m.group(0).replace(' ', '\x00'), text)
    return out.replace('\\ ', '\\\x00')


def _segment_hit_at(text: str, at: int, max_spaces: int = 3) -> bool:
    """Whether the path segment ending at *at* sits in an absolute
    path token. Walks back through the token's characters (spaces
    inside quotes were blanked first), so a quoted path containing
    spaces is one token, not two — and in OUTPUT text (the tripwire),
    where paths are not quoted at all, crosses up to *max_spaces*
    single spaces when the pieces continue a path
    (``…/mnt/c/Program Files/ziniao``). A relative path
    (``stores/ziniao/x``, a store may be slugged like a vendor) never
    crosses into an absolute start and stays unmatched.
    """
    start = at
    for _ in range(max_spaces + 1):
        run = re.search(r'[^\s]*$', text[:start])
        token = run.group(0) if run else ''
        if _ABS_START_RE.match(token.lstrip('\'"')):
            return True
        # ``--flag=/abs/path``: the assignment ends the token's flag
        # part; what precedes it can still be the flag, not a path.
        if '=' in token and _ABS_START_RE.match(
            token.rsplit('=', 1)[-1].lstrip('\'"')
        ):
            return True
        crossed = run.start() if run else start
        if crossed > 0 and text[crossed - 1] == ' ':
            start = crossed - 1
            continue
        return False
    return False


def dir_name_hits(text: str) -> list[tuple[str, str]]:
    """(vendor key, name) hits: dir names as segments of absolute
    paths, or inside a ``find -name`` glob.

    Searched on BOTH the quote-normalised text (a quoted path's spaces
    blanked, so its token walks back whole) and the raw text — a
    MULTI-WORD dir name (``GuardTest Global``) inside quotes survives
    normalisation only as ``GuardTest\\x00Global``, so the raw pass is
    what catches it.
    """
    m = vendor_matcher()
    hits: list[tuple[str, str]] = []
    for lowered in {
        _quote_normalize(text).lower(),
        text.lower(),
    }:
        for key, name in m.dir_names:
            for mm in re.finditer(
                r'[/\\]' + re.escape(name) + r'(?=$|[/\\\s\'";&|)])',
                lowered,
            ):
                if _segment_hit_at(lowered, mm.start()):
                    hits.append((key, mm.group(0).lstrip('/\\')))
    for gm in _FIND_GLOB_RE.finditer(text):
        pat = gm.group(1).lower().strip()
        for key, name in m.dir_names:
            if (
                pat == name
                or name in pat
                or (
                    # a glob naming a FRAGMENT of the dir (``*adspower*``
                    # for ``adspower_global``); the length floor keeps a
                    # one-letter glob from naming every vendor
                    len(pat) >= 4 and pat in name
                )
            ):
                hits.append((key, pat))
    # A path token ending in ``*`` names a dir by PREFIX
    # (``…/Roaming/GuardTest*`` for ``GuardTest Global``).
    for gm in re.finditer(r"[/\\]([^/\s'\"*]+)\*", _quote_normalize(text)):
        prefix = gm.group(1).lower()
        if len(prefix) < 4:
            continue
        for key, name in m.dir_names:
            if name.startswith(prefix):
                hits.append((key, prefix + '*'))
    return hits


def _vendor_dir_hit(text: str) -> tuple[str, str] | None:
    """The first (vendor key, detail) hit: a dir-name segment, a
    ``find`` glob, or a path template in any spelling."""
    hits = dir_name_hits(text)
    if hits:
        return hits[0]
    lowered = text.lower()
    for key, regex in vendor_matcher().templates:
        mm = regex.search(lowered)
        if mm:
            return key, mm.group(0)
    return None


def _vendor_port_hit(
    text: str,
    own_ports: frozenset[int],
    extra_vendor_ports: frozenset[int] = frozenset(),
) -> tuple[int, str] | None:
    """The first (port, owner) a connect or listener context names."""
    ports = vendor_matcher().ports

    def owner(port: int) -> str | None:
        if port in own_ports:
            return None
        if port == _INSPECTOR_PORT:
            return 'the Node inspector'
        if port in _CDP_PORT_RANGE:
            return 'a CDP proxy port'
        if port in extra_vendor_ports:
            return 'a configured vendor client port'
        return ports.get(port)

    for regex in (_URL_PORT_RE, _HOSTPORT_RE, *_LISTEN_PORT_RES):
        for mm in regex.finditer(text):
            port = int(mm.group(1))
            who = owner(port)
            if who:
                return port, who
    return None


def under_declared_private_path(path: str) -> bool:
    """Whether *path* sits inside any registered vendor's private
    data (used by the generic profile-name basename rule)."""
    return _vendor_dir_hit(path) is not None


def _profile_db_name_hit(cmd: str) -> str | None:
    """A profile-database name in a command (D-C3-8 split).

    Distinctive names anywhere; generic names (Cookies, Login Data, …)
    only as path segments — ``click('Accept Cookies')``,
    ``js('document.cookie')`` and a ``Cookies`` pandas column are
    ordinary agent commands and must pass.
    """
    m = _PROFILE_DB_DISTINCTIVE_RE.search(cmd)
    if m:
        return m.group(0)
    m = _PROFILE_DB_SEGMENT_RE.search(cmd)
    if m:
        return m.group(0).lstrip('/\\')
    for gm in _FIND_GLOB_RE.finditer(cmd):
        pat = gm.group(1).strip()
        for name in (*_PROFILE_DB_DISTINCTIVE, *_PROFILE_DB_GENERIC):
            if pat.lower() == name.lower():
                return name
    return None


# ── deny reasons ────────────────────────────────────────────────────

_VENDOR_PATH_DENY = (
    "Blocked: `{path}` is the {vendor} browser client's private data "
    '(profiles, credentials, logs, local API). A task agent never '
    'reads it: drive the store through the `browser-use` wrapper, and '
    'at a login page ask the user (AskUserQuestion) instead of '
    'searching for credentials.'
)
_PROFILE_DB_DENY = (
    'Blocked: `{name}` is a browser-profile database (cookies / saved '
    'logins). Reading or copying profile databases is never part of a '
    'task; at a login page, ask the user for the account.'
)
_PORT_DENY = (
    "Blocked: port {port} is {who}, not this task's to use. The "
    "task's own browser is reached only through the `browser-use` "
    'wrapper; do not call vendor APIs, CDP endpoints or the inspector '
    'directly, and do not start listeners or bridges on them.'
)


def _log_deny(task_id: str, tool_use_id: str, kind: str, detail: str) -> None:
    logger.warning(
        'Vendor guard: task %s tool_use %s denied (%s): %s',
        task_id[:8] if task_id else '-',
        tool_use_id or '-',
        kind,
        detail,
    )


# ── the entry point ─────────────────────────────────────────────────


def vendor_guard_deny(
    tool_name: str,
    tool_input,
    *,
    task_dir=None,
    own_ports: frozenset[int] = frozenset(),
    extra_vendor_ports: frozenset[int] = frozenset(),
    readable: tuple[str, ...] = (),
    owned_session_names: frozenset[str] = frozenset(),
    task_id: str = '',
    tool_use_id: str = '',
) -> str | None:
    """First guard deny reason for this tool call, else ``None``.

    Chains the vendor categories (this module) with the lateral /
    process / OS-settings / home categories
    (:mod:`app.ai.agent_lateral_guard`). ``task_dir`` masks the task's
    own workspace out of the dir-name matchers; ``own_ports`` are the
    ports this task may reach (its store's CDP proxy);
    ``extra_vendor_ports`` are vendor ports known only at runtime
    (D-C3-5: a configured ziniao account's ``socket_port``);
    ``readable`` is the task's own mailbox files (the one part of
    ``<home>/data`` it may read); ``owned_session_names`` are the
    task's bh-tmp session names that carry no id prefix (the
    ``{slug}-aux`` kind — D-C3-9).
    """
    if not isinstance(tool_input, dict):
        return None
    # Masked out of the command before the home rules run: the task's
    # own workspace, and (the mailbox retry's answer) its own mailbox
    # db files under <home>/data — the one data-dir reads that are
    # legitimate, exactly source_guard's ``readable`` contract.
    masked = tuple(str(p) for p in (task_dir, *readable) if p)
    prefix = task_id[:8] if task_id else ''

    def deny(kind: str, reason: str, detail: str) -> str:
        _log_deny(task_id, tool_use_id, kind, detail)
        return reason

    if tool_name == 'Bash':
        cmd = _mask(str(tool_input.get('command', '')), masked)
        return _deny_bash(
            cmd,
            own_ports,
            extra_vendor_ports,
            prefix,
            owned_session_names,
            deny,
        )
    if tool_name in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        path = tool_input.get('file_path') or tool_input.get('notebook_path')
        if not path:
            return None
        content = str(
            tool_input.get('content') or tool_input.get('new_string') or ''
        )
        return _deny_file_tool(
            str(path),
            tool_name,
            content,
            readable,
            prefix,
            owned_session_names,
            deny,
        )
    if tool_name in ('Grep', 'Glob'):
        path = tool_input.get('path')
        if path:
            return _deny_file_tool(
                str(path),
                tool_name,
                '',
                readable,
                prefix,
                owned_session_names,
                deny,
            )
    return None


def _deny_bash(
    cmd: str,
    own_ports: frozenset[int],
    extra_vendor_ports: frozenset[int],
    task_prefix: str,
    owned_session_names: frozenset[str],
    deny,
) -> str | None:
    if ssh_verb_hit(cmd):
        return deny('remote-access', SSH_DENY, 'ssh-family verb')
    what = port_forwarder_hit(cmd)
    if what:
        return deny('forwarder', FORWARDER_DENY, what)
    hit = _vendor_dir_hit(cmd)
    if hit:
        return deny(
            'vendor-path',
            _VENDOR_PATH_DENY.format(vendor=hit[0], path=hit[1]),
            hit[1],
        )
    name = _profile_db_name_hit(cmd)
    if name:
        return deny('profile-db', _PROFILE_DB_DENY.format(name=name), name)
    hit = _vendor_port_hit(cmd, own_ports, extra_vendor_ports)
    if hit:
        return deny(
            'port',
            _PORT_DENY.format(port=hit[0], who=hit[1]),
            str(hit[0]),
        )
    what = process_inspection_hit(cmd)
    if what:
        return deny('process-inspect', PROC_DENY, what)
    what = os_settings_hit(cmd)
    if what:
        return deny('os-settings', OS_SETTINGS_DENY, what)
    # the home family matches in the ONE normal form — text and
    # spellings normalised the same way (native-Windows proofs)
    norm = cd_home_relative(normalize_path_text(cmd))
    m = home_bare_re().search(norm) or home_deny_re(
        task_prefix, owned_session_names
    ).search(norm)
    if m:
        return deny('home', HOME_DENY.format(path=m.group(0)), m.group(0))
    # incident 4: another vibe-seller install's home (production's
    # profiles.json read from a test task)
    hit = foreign_home_hit(norm)
    if hit:
        return deny('foreign-home', FOREIGN_HOME_DENY.format(path=hit), hit)
    # incident 3: reading a store wrapper (it embeds the task JWT).
    # Position-aware — executing the wrapper is the product path.
    what = wrapper_read_hit(cmd)
    if what:
        return deny(
            'wrapper-read',
            WRAPPER_READ_DENY.format(path=what),
            what,
        )
    m = ssh_dir_hit(cmd)
    if m:
        return deny('ssh-dir', SSH_DIR_DENY.format(path=m), m)
    return None


def _deny_file_tool(
    path: str,
    tool_name: str,
    content: str,
    readable,
    task_prefix: str,
    owned_session_names: frozenset[str],
    deny,
) -> str | None:
    expanded = os.path.expanduser(path)
    # The task's own mailbox files: the source-guard retry hands the
    # agent exactly these db_paths under <home>/data.
    for ok in readable:
        if expanded == os.path.expanduser(str(ok)):
            return None
    m = ssh_dir_hit(path)
    if m:
        return deny('ssh-dir', SSH_DIR_DENY.format(path=m), m)
    hit = _vendor_dir_hit(path)
    if hit:
        return deny(
            'vendor-path',
            _VENDOR_PATH_DENY.format(vendor=hit[0], path=hit[1]),
            hit[1],
        )
    base = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].lower()
    if _PROFILE_DB_DISTINCTIVE_RE.fullmatch(base):
        return deny('profile-db', _PROFILE_DB_DENY.format(name=base), base)
    if base in (n.lower() for n in _PROFILE_DB_GENERIC) and (
        _PROFILE_DIR_SHAPE_RE.search(path) or under_declared_private_path(path)
    ):
        # A GENERIC name is a profile DB only inside a profile-shaped
        # path (``…/Default/Cookies``); a task's own ``Cookies``
        # column export reads normally (D-C3-8).
        return deny('profile-db', _PROFILE_DB_DENY.format(name=base), base)
    what = process_inspection_hit(path)
    if what:
        return deny('process-inspect', PROC_DENY, what)
    norm = normalize_path_text(path)
    if (
        home_bare_re().search(norm)
        or home_deny_re(task_prefix, owned_session_names).search(norm)
        # the RAW path: under_closed_home expands the prefix forms
        # itself (a pre-expanduser'd '~' resolves through the real
        # user home and would miss a swapped home in the seams)
        or under_closed_home(path, task_prefix, owned_session_names)
    ):
        return deny('home', HOME_DENY.format(path=path), path)
    hit = foreign_home_hit(norm)
    if hit:
        return deny('foreign-home', FOREIGN_HOME_DENY.format(path=hit), hit)
    if tool_name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        what = os_settings_hit(path)
        if what:
            return deny('os-settings', OS_SETTINGS_DENY, what)
        if content:
            # A script being written is scanned like a command: the
            # home rules and the wrapper rule apply to its TEXT (the
            # incident-3 agent could otherwise lift the wrapper's JWT
            # or the server DB path into a file and run it). The
            # task's own mailbox paths are masked first — a script
            # that queries them is the legitimate email flow.
            body = _mask(content, tuple(str(p) for p in readable if p))
            norm_body = normalize_path_text(body)
            m = home_bare_re().search(norm_body) or home_deny_re(
                task_prefix, owned_session_names
            ).search(norm_body)
            if m:
                return deny(
                    'home', HOME_DENY.format(path=m.group(0)), m.group(0)
                )
            hit = foreign_home_hit(norm_body)
            if hit:
                return deny(
                    'foreign-home', FOREIGN_HOME_DENY.format(path=hit), hit
                )
            what = wrapper_read_hit(body)
            if what:
                return deny(
                    'wrapper-read',
                    WRAPPER_READ_DENY.format(path=what),
                    what,
                )
            what = port_forwarder_hit(content)
            if what:
                return deny('forwarder', FORWARDER_DENY, what)
            hit = _vendor_port_hit(content, frozenset())
            if hit:
                return deny(
                    'port',
                    _PORT_DENY.format(port=hit[0], who=hit[1]),
                    f'written content binds {hit[0]}',
                )
    return None
