"""Chat-bot (group robot) platform registry.

One concept — a "chat bot" — four platforms:

    WeCom / WeChat Work (企业微信)
    DingTalk (钉钉)
    Feishu / Lark (飞书)
    Slack

The platform is DERIVED from the webhook URL host by the server
(:func:`detect_platform`); it is never accepted from the client. That
makes two whole classes of bug impossible by construction:

  * a webhook secret posted to a host the user did not intend, and
  * a stored bot whose platform disagrees with its own URL.

The per-platform senders live in sibling modules (``wecom.py``,
``dingtalk.py``, ``feishu.py``, ``slack.py``). Each registers its
``send`` callable here when imported, which keeps this module free of
import cycles: :func:`send_message` imports the platform module lazily
if it has not been loaded yet.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib import import_module
import logging
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 30

#: ``send(url, secret, content, msgtype) -> (ok, message)``.
SendFn = Callable[[str, 'str | None', str, str], Awaitable['tuple[bool, str]']]


@dataclass
class Platform:
    """Everything the server needs to know about one chat platform.

    ``hosts`` is an allowlist of EXACT hostnames — a suffix match would
    let ``oapi.dingtalk.com.evil.com`` pass. ``path_prefix`` is the
    URL path the platform's webhook lives under, so a same-host URL
    pointing somewhere else is rejected too.
    """

    name: str
    label: str
    hosts: tuple[str, ...]
    path_prefix: str
    uses_secret: bool
    supports_file: bool
    max_message: int
    limit_unit: str
    doc_url: str
    #: Per-msgtype overrides of ``max_message`` (Slack caps markdown
    #: lower than plain text).
    limits: dict[str, int] = field(default_factory=dict)
    #: Set by the platform module on import.
    send: SendFn | None = None

    def limit_for(self, msgtype: str) -> int:
        """The size limit that applies to ``msgtype``."""
        return self.limits.get(msgtype, self.max_message)


PLATFORMS: dict[str, Platform] = {
    'wecom': Platform(
        name='wecom',
        label='WeCom',
        hosts=('qyapi.weixin.qq.com',),
        path_prefix='/cgi-bin/webhook/',
        uses_secret=False,
        supports_file=True,
        max_message=4096,
        limit_unit='bytes',
        doc_url=('https://developer.work.weixin.qq.com/document/path/91770'),
    ),
    'dingtalk': Platform(
        name='dingtalk',
        label='DingTalk',
        hosts=('oapi.dingtalk.com',),
        path_prefix='/robot/send',
        uses_secret=True,
        supports_file=False,
        max_message=20000,
        limit_unit='bytes',
        doc_url=(
            'https://open.dingtalk.com/document/orgapp/custom-robot-access'
        ),
    ),
    'feishu': Platform(
        name='feishu',
        label='Feishu / Lark',
        hosts=('open.feishu.cn', 'open.larksuite.com'),
        path_prefix='/open-apis/bot/v2/hook/',
        uses_secret=True,
        supports_file=False,
        max_message=20000,
        limit_unit='bytes',
        doc_url=(
            'https://open.feishu.cn/document/client-docs/bot-v2/add-custom-bot'
        ),
    ),
    'slack': Platform(
        name='slack',
        label='Slack',
        hosts=('hooks.slack.com', 'hooks.slack-gov.com'),
        path_prefix='/services/',
        uses_secret=False,
        supports_file=False,
        max_message=40000,
        limit_unit='chars',
        # Block Kit markdown blocks cap at 12 000 characters.
        limits={'markdown': 12000},
        doc_url='https://api.slack.com/messaging/webhooks',
    ),
}


# ── URL detection ───────────────────────────────────


def _is_loopback(host: str) -> bool:
    if host in ('localhost', '::1'):
        return True
    return host.startswith('127.')


def detect_platform(url: str) -> str | None:
    """Return the platform name a webhook URL belongs to, or ``None``.

    A URL is accepted only when ALL of the following hold:

      * the scheme is ``https``, OR plain ``http`` on a LOOPBACK host
        (the offline e2e suite registers a loopback stub that
        impersonates a platform webhook; loopback never leaves the
        machine, so the secret cannot leak through it),
      * the host is an EXACT match for one of the platform's hosts
        (loopback excepted — the platform is read off the path),
      * the path starts with that platform's path prefix,
      * there is no userinfo (``https://host@evil.com/...``) and, for
        https, no non-default port.

    Everything else — a look-alike host, a URL that merely mentions a
    platform host in its query string, a userinfo trick, cleartext to
    a non-loopback host — is rejected, because the webhook URL *is*
    the secret.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in ('https', 'http'):
            return None
        if parts.username or parts.password:
            return None
        host = (parts.hostname or '').lower()
        if not host:
            return None
        port = parts.port
    except ValueError:
        # Malformed URL (e.g. a bad port like ':99999999').
        return None

    path = parts.path or ''
    loopback = _is_loopback(host)

    if scheme == 'http' and not loopback:
        return None
    if scheme == 'https' and not loopback and port not in (None, 443):
        return None

    if loopback:
        # The stub carries the platform in its path.
        for name, plat in PLATFORMS.items():
            if path.startswith(plat.path_prefix):
                return name
        return None

    for name, plat in PLATFORMS.items():
        if host in plat.hosts and path.startswith(plat.path_prefix):
            return name
    return None


def platform_for_url(url: str) -> Platform | None:
    """The :class:`Platform` a webhook URL belongs to, or ``None``."""
    name = detect_platform(url)
    if name is None:
        return None
    return PLATFORMS.get(name)


# ── URL masking ─────────────────────────────────────


def _secret_part(url: str, platform: str) -> str:
    """The secret-bearing fragment of a webhook URL.

    WeCom carries ``?key=``, DingTalk ``?access_token=``; Feishu and
    Slack put the token in the last path segment.
    """
    parts = urlsplit(url)
    query_key = 'key' if platform == 'wecom' else 'access_token'
    if platform in ('wecom', 'dingtalk'):
        values = parse_qs(parts.query).get(query_key) or []
        return values[0] if values else ''
    segments = [s for s in (parts.path or '').split('/') if s]
    return segments[-1] if segments else ''


def mask_webhook_url(url: str, platform: str) -> str:
    """A display-safe rendering of a webhook URL.

    ``https://<host>/...****<last 4 of the secret>``. The raw URL is
    never returned — it embeds the secret.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or '***'
    except ValueError:
        return '***'
    secret = _secret_part(url, platform)
    tail = secret[-4:] if len(secret) >= 4 else ''
    suffix = f'****{tail}' if tail else ''
    return f'https://{host}/...{suffix}'


# ── Shared transport ────────────────────────────────


async def post(
    url: str,
    *,
    label: str,
    json_payload: dict | None = None,
    content: bytes | None = None,
    headers: dict | None = None,
    timeout: int = WEBHOOK_TIMEOUT,
) -> tuple[httpx.Response | None, str]:
    """POST to a webhook. Returns ``(response, error)``.

    ``error`` is non-empty only for transport failures. It NEVER
    contains the webhook URL: httpx exception strings embed the request
    URL, and that URL *is* the secret. The traceback goes to the
    server log (same pattern as the original WeCom sender); the
    message returned to callers stays generic.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=json_payload,
                content=content,
                headers=headers,
            )
        return resp, ''
    except httpx.HTTPError:
        logger.exception('%s webhook HTTP error', label)
        return None, f'Failed to send {label} webhook request.'
    except Exception:
        logger.exception('%s webhook error', label)
        return None, f'Unexpected error while sending {label} webhook.'


# ── Size guard ──────────────────────────────────────


def size_error(
    platform: Platform,
    content: str,
    msgtype: str = 'text',
    measured: int | None = None,
) -> str:
    """The error message when ``content`` is over the platform limit.

    ``measured`` lets a platform check the size of what it will
    actually send (Feishu counts the serialized body, not the text).
    An empty string means the message fits.
    """
    limit = platform.limit_for(msgtype)
    size = measured if measured is not None else len(content.encode('utf-8'))
    if size <= limit:
        return ''
    return (
        f'{platform.label} limit is {limit} {platform.limit_unit}; '
        f'message is {size} — split it into several messages.'
    )


# ── Dispatch ────────────────────────────────────────


async def send_message(
    platform: str,
    url: str,
    secret: str | None,
    content: str,
    msgtype: str = 'text',
) -> tuple[bool, str]:
    """Send ``content`` through the ``platform`` webhook."""
    plat = PLATFORMS.get(platform)
    if plat is None:
        return False, f'Unknown chat bot platform: {platform}'
    if plat.send is None:
        # The platform module registers itself on import; load it now.
        import_module(f'app.notifiers.{platform}')
        plat = PLATFORMS.get(platform)
    if plat is None or plat.send is None:
        return False, f'Unknown chat bot platform: {platform}'
    return await plat.send(url, secret, content, msgtype)
