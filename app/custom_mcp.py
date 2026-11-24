"""Custom MCP servers — external tools an admin connects to every task.

Direction matters, and the two are easy to confuse:

* ``/api/mcp`` (``app/agent_mcp/``) makes Vibe Seller an MCP SERVER:
  an outside agent (Codex, Claude, …) connects IN with a ``vst_``
  token and drives tasks.
* This module makes Vibe Seller's task agents MCP CLIENTS of servers
  an admin added in Settings → Integrations → Custom MCP (e.g. a
  keyword-research service): their tools reach every task as
  ``mcp__<name>__<tool>``.

THE INVARIANT: every server that reaches a launch is one Claude Code
accepts. Claude Code validates ``--mcp-config`` as a whole and EXITS
(``process.exit(1)``) on a single entry that fails its schema — so
one sloppy paste (a vendor's ``{"url": …}`` with no ``type``, which
Cursor-style clients accept) would kill every task, not just skip a
server. :func:`normalize_server` is therefore the ONE gate: it runs
when a server is saved AND again on every launch
(:func:`launch_servers`), so a row written by an older version, or
edited in the DB, is dropped with a log line instead of taking the
agent down.

What normalization refuses, and why:

* names outside ``^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$`` or with ``__``
  (the CLI splits ``mcp__<server>__<tool>`` on it), and the names the
  platform owns (:data:`RESERVED_NAMES`) — a custom ``vibe-seller``
  would replace the task's own tools, a ``playwright`` /
  ``browser-use`` one is blocked by the task launch anyway (store
  browser isolation);
* ``${VAR}`` anywhere — Claude Code expands it from the AGENT's
  environment, which holds the model provider's key: a header
  ``Bearer ${ANTHROPIC_AUTH_TOKEN}`` would ship that key to a third
  party. The admin pastes the real value instead;
* a value that is one of our own masks — copying a server back out
  of the list and re-saving it must not store ``****abcd``;
* ``oauth`` / ``headersHelper`` — a task runs headless, nobody can
  finish a browser sign-in, and a helper is a shell command run on
  every connect. Servers that hand out a token work; the error says
  to use that.

Credentials live in the config (an ``Authorization`` header, an
``env`` key, a token in the URL query). The row keeps it encrypted
(:func:`seal`); the only clear copy on disk is the per-launch,
owner-only file the CLI reads;
every response goes through :func:`app.custom_mcp_view.public_view`.
"""

from collections.abc import Iterable
import json
import logging
import re
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import async_session
from app.models.custom_mcp_server import CustomMcpServer
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')

#: Names the platform owns. Compared case-insensitively.
RESERVED_NAMES = frozenset({
    'vibe-seller',  # the task's own in-task MCP (app/mcp_server.py)
    'ticktick',  # written by the TickTick / Dida365 integration
    'playwright',  # --disallowedTools mcp__playwright__* on every task
    'browser-use',  # --disallowedTools mcp__browser-use__*
    'claude-in-chrome',  # reserved by Claude Code itself
    'computer-use',  # reserved by Claude Code itself
})

# Spellings other MCP clients use for the transport. Streamable HTTP
# is ``http`` to Claude Code.
_TYPE_ALIASES = {
    'http': 'http',
    'streamable-http': 'http',
    'streamable_http': 'http',
    'streamablehttp': 'http',
    'sse': 'sse',
    'stdio': 'stdio',
}

# Fields that mean something to some MCP client but cannot work for a
# headless task — refused with a reason rather than silently dropped.
_UNSUPPORTED_FIELDS = {
    'oauth': 'OAuth sign-in',
    'headersHelper': 'headersHelper',
}

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MAX_CONFIG_BYTES = 16 * 1024
_MASK_FILL = ('****', '••••')


class McpConfigError(ValueError):
    """A pasted/stored server config Claude Code would not accept.

    *code* names the problem for the panel, which says it in the user's
    language (``customMcp.configErrors.<code>``); the English message is
    the fallback and what the log and tests read. *params* fill the
    translated sentence (e.g. the offending name).
    """

    def __init__(self, message: str, code: str = 'invalid', **params):
        super().__init__(message)
        self.code = code
        self.params = params


# ── validation ──────────────────────────────────────────────────


def is_key(name: str) -> bool:
    """Whether *name* can be a server key as typed (tool names are
    built from it: ``mcp__<key>__<tool>``)."""
    return bool(NAME_RE.match(name)) and '__' not in name


def display_label(name: str | None) -> str | None:
    """The name a person typed, kept for display — any script.

    ``None`` when nothing was typed. Too long or with control
    characters is a :class:`McpConfigError`.
    """
    if name is None or not name.strip():
        return None
    label = name.strip()
    if len(label) > 40 or any(ord(c) < 32 for c in label):
        raise McpConfigError(
            'The name is too long or has control characters', 'invalid_name'
        )
    return label


def check_name(name: object) -> str:
    """The server name, or :class:`McpConfigError` saying what is wrong."""
    if not isinstance(name, str) or not name.strip():
        raise McpConfigError('The server needs a name', 'name_missing')
    name = name.strip()
    if not NAME_RE.match(name) or '__' in name:
        raise McpConfigError(
            f'Invalid server name "{name}": use letters, digits, "-" '
            'and "_" (up to 48 characters, no "__")',
            'invalid_name',
        )
    if name.lower() in RESERVED_NAMES:
        raise McpConfigError(
            f'"{name}" is a name Vibe Seller uses itself — pick another',
            'reserved_name',
            name=name,
        )
    return name


def _check_value(where: str, value: object) -> str:
    """One string value of a config: a str, no ``${…}``, not a mask."""
    if not isinstance(value, str):
        raise McpConfigError(f'{where} must be text')
    if '${' in value:
        raise McpConfigError(
            f'{where} uses a ${{…}} variable — paste the real value '
            "instead (variables would be filled from the agent's own "
            'environment)',
            'variable',
        )
    # Both mask shapes the server emits (app.agent_mcp.redact.is_mask)
    # contain one of these fills — and so does any value copied out
    # of a masked list. Not imported: app.agent_mcp pulls in the task
    # launch this module is part of.
    if any(fill in value for fill in _MASK_FILL):
        raise McpConfigError(
            f'{where} looks like a masked value — paste the real one '
            "from the service's own setup page",
            'masked',
        )
    if '\r' in value or '\n' in value:
        raise McpConfigError(f'{where} must be a single line')
    return value


def _check_header_value(where: str, value: str) -> str:
    """An HTTP header can only carry Latin-1 text. A vendor's sample
    config pasted unedited (``Bearer 请改成您的 MCP Token``) is the usual
    way to get anything else — and httpx / the CLI cannot send it."""
    try:
        value.encode('latin-1')
    except UnicodeEncodeError:
        raise McpConfigError(
            f'{where} has characters a request header cannot carry — is '
            "the sample text still there? Paste the service's real token",
            'placeholder',
        ) from None
    return value


def _check_str_map(where: str, raw: object, *, header: bool) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise McpConfigError(f'{where} must be an object of name: value')
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise McpConfigError(f'{where} has an empty name')
        if header and not _HEADER_NAME_RE.match(key):
            raise McpConfigError(f'Invalid header name "{key}"')
        out[key] = _check_value(f'{where}.{key}', value)
        if header:
            _check_header_value(f'{where}.{key}', out[key])
    return out


def _check_url(raw: object) -> str:
    url = _check_value('url', raw).strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise McpConfigError(
            f'"{url}" is not an http(s) address of an MCP server', 'bad_url'
        )
    return url


def _infer_type(raw: dict) -> str:
    declared = raw.get('type', raw.get('transport'))
    if declared is not None:
        if not isinstance(declared, str):
            raise McpConfigError('"type" must be text')
        key = declared.strip().lower().replace(' ', '')
        if key not in _TYPE_ALIASES:
            raise McpConfigError(
                f'Unsupported server type "{declared}" — use http, sse or '
                'stdio',
                'unsupported',
            )
        return _TYPE_ALIASES[key]
    if 'command' in raw:
        return 'stdio'
    url = raw.get('url', raw.get('serverUrl'))
    if isinstance(url, str):
        # Clients like Cursor accept a bare {"url": …} and sniff the
        # transport; Claude Code requires it spelled out. A path that
        # ends in /sse is the legacy SSE convention, everything else
        # is Streamable HTTP.
        path = urlsplit(url.strip()).path.rstrip('/')
        return 'sse' if path.endswith('/sse') else 'http'
    raise McpConfigError(
        'A server needs either a "url" (remote server) or a "command" '
        '(local program)',
        'bad_config',
    )


def normalize_server(name: object, raw: object) -> dict:
    """*raw* as the server object Claude Code accepts — or an error.

    Idempotent: a normalized config normalizes to itself, which is
    what lets :func:`launch_servers` re-run it on every launch.
    """
    check_name(name)
    if not isinstance(raw, dict):
        raise McpConfigError('A server config must be a JSON object')
    for field, label in _UNSUPPORTED_FIELDS.items():
        if raw.get(field):
            raise McpConfigError(
                f'{label} is not supported: tasks run unattended, so '
                'nobody can complete it. Use the token / API-key setup '
                'the service offers instead',
                'oauth' if field == 'oauth' else 'unsupported',
            )
    kind = _infer_type(raw)
    if kind == 'stdio':
        command = raw.get('command')
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError('"command" must be a program name')
        args = raw.get('args', [])
        if not isinstance(args, list):
            raise McpConfigError('"args" must be a list')
        out: dict = {
            'type': 'stdio',
            'command': _check_value('command', command).strip(),
            'args': [_check_value(f'args[{i}]', a) for i, a in enumerate(args)],
        }
        env = _check_str_map('env', raw.get('env'), header=False)
        if env:
            out['env'] = env
    else:
        out = {
            'type': kind,
            'url': _check_url(raw.get('url', raw.get('serverUrl'))),
        }
        headers = _check_str_map('headers', raw.get('headers'), header=True)
        if headers:
            out['headers'] = headers
    if len(json.dumps(out)) > _MAX_CONFIG_BYTES:
        raise McpConfigError('The server config is too large')
    return out


def _load_json(text: str) -> object:
    """JSON from what a user pasted — tolerating text around it.

    Vendors hand out "copy this to your AI tool" blurbs: a sentence,
    then the JSON. Take the outermost ``{…}`` when the whole paste is
    not JSON.
    """
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            raise McpConfigError(
                'Paste the JSON config (or the server URL) the service '
                'gives you',
                'bad_json',
            ) from None
        try:
            return json.loads(text[start : end + 1])
        except ValueError as e:
            raise McpConfigError(
                f'The pasted JSON is invalid: {e}', 'bad_json'
            ) from e


# Host labels that say "this is an MCP/API endpoint", not whose it is.
_GENERIC_LABELS = frozenset({'mcp', 'api', 'www', 'server', 'servers', 'app'})
# Second-level labels of country domains (example.com.cn, example.co.uk).
_SECOND_LEVEL = frozenset({'com', 'co', 'net', 'org', 'gov', 'edu', 'ac'})


def _slug(text: str) -> str:
    """*text* as a server name: lowercase, ``[a-z0-9_-]``, no ``__``."""
    slug = re.sub(r'[^a-z0-9_-]+', '-', text.lower())
    slug = re.sub(r'_{2,}', '_', re.sub(r'-{2,}', '-', slug))
    return slug.strip('-_')[:48].strip('-_')


def derive_name(raw: dict) -> str:
    """A server name for a paste that carries none.

    A remote server is named after its domain — ``https://mcp.xydc.com/
    mcp`` → ``xydc`` (endpoint labels like ``mcp.`` / ``api.`` and the
    TLD dropped) — and a local one after the package it runs
    (``npx -y @scope/some-server`` → ``some-server``). A name the
    platform owns gets ``-mcp`` appended; the result still goes
    through :func:`check_name`.
    """
    url = raw.get('url', raw.get('serverUrl'))
    name = ''
    if isinstance(url, str):
        host = (urlsplit(url.strip()).hostname or '').lower()
        labels = [p for p in host.split('.') if p]
        if re.fullmatch(r'[\d.]+', host) or len(labels) < 2:
            name = host
        else:
            labels = labels[:-1]  # the TLD
            if len(labels) > 1 and labels[-1] in _SECOND_LEVEL:
                labels = labels[:-1]
            meaningful = [p for p in labels if p not in _GENERIC_LABELS]
            name = meaningful[-1] if meaningful else labels[-1]
    elif isinstance(raw.get('command'), str):
        args = raw.get('args') if isinstance(raw.get('args'), list) else []
        package = next(
            (a for a in args if isinstance(a, str) and not a.startswith('-')),
            raw['command'],
        )
        name = re.sub(r'@[^/]*$', '', package.rstrip('/').split('/')[-1])
        name = name or raw['command']
    name = _slug(name)
    if not name:
        raise McpConfigError('Give this server a name', 'name_missing')
    if name in RESERVED_NAMES:
        name = f'{name}-mcp'
    return name


def _auto_named(servers: dict) -> dict:
    """The paste's servers under the names the user did not type.

    A remote server is named after its URL's domain — the rule the
    Settings panel promises ("leave it empty → named after the
    domain"), whatever key the vendor's JSON used. A local command has
    no domain and keeps its config key. Two servers that would share a
    name get ``-2``, ``-3``.
    """
    out: dict = {}
    for key, raw in servers.items():
        remote = isinstance(raw, dict) and isinstance(
            raw.get('url', raw.get('serverUrl')), str
        )
        base = derive_name(raw) if remote else key
        candidate, n = base, 1
        while candidate in out:
            n += 1
            candidate = f'{base}-{n}'
        out[candidate] = raw
    return out


def parse_pasted(payload: object, name: str | None = None) -> dict:
    """``{name: normalized config}`` from what a user pasted.

    Accepts the shapes MCP vendors publish:

    * ``{"mcpServers": {"<name>": {…}}}`` (Claude / Cursor / most);
    * ``{"servers": {"<name>": {…}}}`` (VS Code);
    * ``{"<name>": {…}}`` — the inner map on its own;
    * one bare server ``{"url": …}`` / ``{"command": …}``, or just a
      server URL.

    Naming: *name*, when given for a paste holding exactly one server,
    is used — the one field the user types themselves wins. Without it
    a remote server is named after its URL's domain
    (``https://mcp.xydc.com/mcp`` → ``xydc``, :func:`derive_name`)
    whatever key the JSON used; a local command keeps its key.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith(('http://', 'https://')) and ' ' not in text:
            payload = {'url': text}
        else:
            payload = _load_json(text)
    if not isinstance(payload, dict) or not payload:
        raise McpConfigError('The pasted config is empty', 'bad_json')
    servers: dict
    if isinstance(payload.get('mcpServers'), dict):
        servers = payload['mcpServers']
    elif isinstance(payload.get('servers'), dict):
        servers = payload['servers']
    elif any(k in payload for k in ('url', 'serverUrl', 'command', 'type')):
        servers = {name or derive_name(payload): payload}
    elif all(isinstance(v, dict) for v in payload.values()):
        servers = payload
    else:
        raise McpConfigError(
            'Unrecognized config — expected {"mcpServers": {...}}', 'bad_json'
        )
    if not servers:
        raise McpConfigError(
            'The pasted config has no servers in it', 'bad_json'
        )
    if name and len(servers) == 1 and is_key(name):
        servers = {name: next(iter(servers.values()))}
    else:
        # No name, or one in another script (「西柚MCP」) — that stays the
        # display label (:func:`display_label`); the key comes from the
        # domain.
        servers = _auto_named(servers)
    out = {}
    for raw_name, raw in servers.items():
        clean = check_name(raw_name)
        try:
            out[clean] = normalize_server(clean, raw)
        except McpConfigError as e:
            raise McpConfigError(
                f'{clean}: {e}', e.code, **{'name': clean, **e.params}
            ) from e
    return out


# ── launch ──────────────────────────────────────────────────────


def seal(config: dict) -> str:
    """The stored form of a normalized server config (encrypted)."""
    return encrypt_password(json.dumps(config))


def unseal(stored: str) -> dict:
    """The server config a row holds — :class:`McpConfigError` when it
    cannot be read back (a changed key, a hand-edited row)."""
    try:
        config = json.loads(decrypt_password(stored))
    except (InvalidToken, ValueError) as e:
        raise McpConfigError('The stored config cannot be read') from e
    if not isinstance(config, dict):
        raise McpConfigError('The stored config is not an object')
    return config


def launch_servers(rows: Iterable[tuple[str, str]]) -> dict[str, dict]:
    """``{name: config}`` for a launch from stored ``(name, sealed)`` rows.

    Re-normalizes every row (see the module invariant): a row that no
    longer passes is skipped with a warning, so it costs that server,
    never the task.
    """
    out: dict[str, dict] = {}
    for name, stored in rows:
        try:
            out[name] = normalize_server(name, unseal(stored))
        except McpConfigError as e:
            logger.warning('Skipping custom MCP server %r: %s', name, e)
    return out


async def load_launch_servers() -> dict[str, dict]:
    """The ENABLED custom servers, ready for a task's ``--mcp-config``.

    The same invariant as for one bad row, one level up: these servers
    are an add-on, so a read that fails costs the launch its custom
    servers (with a warning), never the launch itself.
    """
    try:
        async with async_session() as db:
            rows = await db.execute(
                select(CustomMcpServer.name, CustomMcpServer.config_enc)
                .where(CustomMcpServer.enabled.is_(True))
                .order_by(CustomMcpServer.name)
            )
            stored = rows.all()
    except SQLAlchemyError as e:
        logger.warning('Custom MCP servers not loaded: %s', e)
        return {}
    return launch_servers(stored)


async def load_prompt_servers() -> dict[str, str | None]:
    """``{key: label}`` for the system-prompt line — exactly the servers
    a launch hands the CLI (enabled AND still valid)."""
    try:
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(
                        CustomMcpServer.name,
                        CustomMcpServer.config_enc,
                        CustomMcpServer.label,
                    ).where(CustomMcpServer.enabled.is_(True))
                )
            ).all()
    except SQLAlchemyError as e:
        logger.warning('Custom MCP servers not loaded: %s', e)
        return {}
    valid = launch_servers((name, enc) for name, enc, _ in rows)
    return {name: label for name, _, label in rows if name in valid}


def prompt_note(servers: dict[str, str | None]) -> str | None:
    """The one line a task's system context gets about custom servers.

    *servers* maps each key to the name people call it (or ``None``).
    Names only — the CLI already hands the model each tool's own
    description; this line tells it the tools are there to be used, and
    which key a user's 「用西柚MCP…」 means.
    """
    if not servers:
        return None

    def entry(key: str) -> str:
        label = servers[key]
        called = f'"{label}", ' if label and label != key else ''
        return f'`{key}` ({called}tools `mcp__{key}__*`)'

    listed = ', '.join(entry(k) for k in sorted(servers))
    return (
        f'Custom MCP servers (added by an admin in Settings → '
        f'Integrations): {listed}. Use their tools when the task needs '
        'what they provide.'
    )
