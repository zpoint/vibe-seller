"""URLs with their credentials masked — for display AND for logs.

A URL often IS a credential: a chat-bot webhook carries its key in
the query (``?key=``) or path, and MCP vendors hand out
``https://…/mcp?mcp_token=…``. Two places must never show those parts:

* the UI — :func:`mask_url` keeps scheme, host and the readable path,
  so a list can still say WHICH service a row is;
* the server log — httpx logs every request URL at INFO
  (``HTTP Request: POST https://…?key=…``). :class:`RedactUrlFilter`
  sits on the ``httpx`` logger (installed in ``app.main``) and masks
  the query values and userinfo of every URL argument before any
  handler formats them; the path stays, since ids a reader traces live
  there.

No app imports: the logging setup installs this before anything else
loads.
"""

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# A path segment this long with letters AND digits is treated as a
# key (``https://host/<key>/mcp``, a Feishu hook token).
_KEYLIKE_SEGMENT = re.compile(r'^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-]{16,}$')


def mask_url(url: str, *, path: bool = True) -> str:
    """*url* with its credentials masked, still readable.

    Query values and a ``user:password@`` go; with *path* (the
    display default) key-like path segments go too. Scheme, host and
    the readable path stay.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return '****'
    host = parts.hostname or ''
    if port:
        host = f'{host}:{port}'
    if parts.username or parts.password:
        host = f'****@{host}'
    shown_path = '/'.join(
        '****' if path and _KEYLIKE_SEGMENT.match(seg) else seg
        for seg in parts.path.split('/')
    )
    query = urlencode(
        [(k, '****') for k, _ in parse_qsl(parts.query, True)], safe='*'
    )
    return urlunsplit((parts.scheme, host, shown_path, query, ''))


def _masked(arg: object) -> object:
    # httpx passes an ``httpx.URL``; compare by its text so this
    # module needs no httpx import.
    text = str(arg) if type(arg).__name__ == 'URL' else arg
    if isinstance(text, str) and text.startswith(('http://', 'https://')):
        # Query and userinfo only: a log line must keep the path —
        # task / store ids live there and are what a reader traces.
        return mask_url(text, path=False)
    return arg


class RedactUrlFilter(logging.Filter):
    """Mask every URL argument of a log record (never drops one)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_masked(a) for a in record.args)
        return True


def install_log_redaction() -> None:
    """Put :class:`RedactUrlFilter` on the loggers that print URLs."""
    for name in ('httpx',):
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactUrlFilter) for f in target.filters):
            target.addFilter(RedactUrlFilter())
