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
import ipaddress
import logging
import os
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
    #: Whether the platform offers keyword security (DingTalk
    #: 自定义关键词, Feishu 关键词) that the server must satisfy.
    uses_keyword: bool = False
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
        uses_keyword=True,
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
        uses_keyword=True,
        supports_file=False,
        max_message=20000,
        limit_unit='bytes',
        doc_url=(
            'https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot'
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


def _loopback_allowed() -> bool:
    """Whether loopback test webhooks are accepted at all.

    OFF in production: a webhook secret may only ever go to a real
    platform host over https. The offline e2e suite runs a loopback
    stub that impersonates a platform webhook, so the CI jobs (and a
    local e2e run) start the server with
    ``VIBE_ALLOW_LOOPBACK_WEBHOOKS=1``. Read from the environment at
    CALL time (not import time) so tests can monkeypatch it.
    """
    return os.environ.get('VIBE_ALLOW_LOOPBACK_WEBHOOKS', '') in (
        '1',
        'true',
    )


def _is_loopback(host: str) -> bool:
    """Strict loopback check.

    The host must BE a loopback address (``ipaddress``, which covers
    all of 127.0.0.0/8 and ``::1``) or the exact name ``localhost``.
    NEVER a string prefix: ``127.evil.com`` and ``127.0.0.1.nip.io``
    both start with ``127.`` and both resolve to arbitrary internet
    hosts — accepting them as "loopback" would post the webhook secret
    in cleartext to an attacker-chosen machine.
    """
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def detect_platform(url: str) -> str | None:
    """Return the platform name a webhook URL belongs to, or ``None``.

    A URL is accepted only when ALL of the following hold:

      * the scheme is ``https`` and the host is an EXACT match for one
        of the platform's hosts, with that platform's path prefix —
        the only place a webhook secret is ever allowed to go;
      * there is no userinfo (``https://host@evil.com/...``) and no
        non-default port.

    The single exception is a LOOPBACK host (``ipaddress`` loopback or
    ``localhost``), accepted only — plain ``http`` included — while
    the server process runs with ``VIBE_ALLOW_LOOPBACK_WEBHOOKS=1``
    (see :func:`_loopback_allowed`); the platform is then read off the
    path. Loopback never leaves the machine, so the stub the offline
    e2e suite runs can receive a "secret" without any real one going
    anywhere.

    Everything else — a look-alike host, a URL that merely mentions a
    platform host in its query string, a userinfo trick, cleartext,
    loopback-without-the-flag — is rejected, because the webhook URL
    *is* the secret.
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

    if _is_loopback(host):
        if not _loopback_allowed():
            return None
        # The stub carries the platform in its path.
        for name, plat in PLATFORMS.items():
            if path.startswith(plat.path_prefix):
                return name
        return None

    if scheme != 'https':
        return None
    if port not in (None, 443):
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

    The measurement follows ``platform.limit_unit`` — 'chars' counts
    CHARACTERS (Slack's markdown-block cap is 12,000 characters), 'bytes'
    counts UTF-8 bytes (WeCom/DingTalk/Feishu). Measuring Slack's
    content in bytes refused a Chinese markdown message at ~4,000
    characters instead of 12,000 (a Chinese character is 3 bytes).

    ``measured`` lets a platform check the size of what it will
    actually send (Feishu counts the serialized body, not the text).
    An empty string means the message fits.
    """
    limit = platform.limit_for(msgtype)
    size = measured if measured is not None else _measure(platform, content)
    if size <= limit:
        return ''
    return (
        f'{platform.label} limit is {limit} {platform.limit_unit}; '
        f'message is {size} — split it into several messages.'
    )


def _measure(platform: Platform, content: str) -> int:
    """The size of ``content`` in the platform's own unit.

    Raises ValueError for a unit the checker cannot measure: a
    platform declaring an unknown unit must fail loudly rather than
    silently be measured in the wrong one.
    """
    if platform.limit_unit == 'chars':
        return len(content)
    if platform.limit_unit == 'bytes':
        return len(content.encode('utf-8'))
    raise ValueError(
        f'Unknown limit unit {platform.limit_unit!r} for platform '
        f'{platform.name!r} — expected "chars" or "bytes"'
    )


# ── Keyword security ────────────────────────────────


def ensure_keyword(content: str, keyword: str | None) -> str:
    """Guarantee ``keyword`` appears in ``content`` (DingTalk 自定义关键词
    / Feishu 关键词 security).

    A keyword-secured bot rejects every message that does not contain
    one of its configured keywords (DingTalk errcode 310000, Feishu
    19024), so the SERVER appends the keyword when the user's content
    doesn't already carry it — the agent never has to know it exists.
    Appended at the END after a blank line, so the real content still
    leads the notification/preview (and DingTalk's markdown title,
    derived from the first line, is unaffected). Returns ``content``
    unchanged when the keyword is already present or not set.
    """
    if not keyword:
        return content
    if keyword in content:
        return content
    return content.rstrip() + '\n\n' + keyword


# ── Dispatch ────────────────────────────────────────


async def send_message(
    platform: str,
    url: str,
    secret: str | None,
    content: str,
    msgtype: str = 'text',
    keyword: str | None = None,
) -> tuple[bool, str]:
    """Send ``content`` through the ``platform`` webhook.

    Keyword security is applied HERE — the single send path /test and
    /send both use — and BEFORE the platform senders run their size
    pre-checks, so the limit always accounts for the appended keyword.
    """
    content = ensure_keyword(content, keyword)
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
