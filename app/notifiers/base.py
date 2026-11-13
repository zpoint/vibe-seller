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

# ── Security settings a platform offers ─────────────
#
# One bot can use several of them at once, which is why a bot's
# ``security_mode`` is a LIST of these names, not a single value.
# Every value below is verified against the platform's own doc
# (``doc_url`` / the URL cited per platform in PLATFORMS).
SECURITY_SIGN = 'sign'
#: The platform signs each request with a shared secret (钉钉 加签,
#: 飞书 签名校验) — the ``secret`` column carries it.
SECURITY_KEYWORD = 'keyword'
#: Messages must contain one of the bot's keywords (钉钉 自定义关键词,
#: 飞书 自定义关键词) — the ``keyword`` column carries one of them.
SECURITY_IP = 'ip'
#: The caller's source IP must be whitelisted (钉钉 IP 地址（段）,
#: 飞书 IP 白名单). Configured on the platform side only: Vibe Seller
#: sends from wherever it runs, so there is nothing to store here —
#: but storing WHICH setting is in use is what tells an IP-secured bot
#: apart from one whose fields were simply left blank.
SECURITY_OPTIONS = (SECURITY_SIGN, SECURITY_KEYWORD, SECURITY_IP)

#: How to name one option in a server-side error.
SECURITY_LABELS = {
    SECURITY_SIGN: 'signing secret (加签 / 签名校验)',
    SECURITY_KEYWORD: 'keyword (自定义关键词)',
    SECURITY_IP: 'IP address (IP 地址段 / IP 白名单)',
}


@dataclass(frozen=True)
class SecurityState:
    """What one bot's security settings resolve to.

    ``error`` is non-empty when the combination is not usable on the
    platform; it names what is missing so the caller can show it
    inline. ``mode`` is the canonical (registry-ordered) comma-joined
    list of the settings in play, or ``None`` when nothing is.
    ``secret`` / ``keyword`` are the values that survive the
    resolution — a setting that is not in play keeps no value, so a
    switch off it cannot leave a stale secret behind.
    """

    mode: str | None = None
    options: tuple[str, ...] = ()
    secret: str | None = None
    keyword: str | None = None
    error: str = ''


@dataclass
class Platform:
    """Everything the server needs to know about one chat platform.

    ``hosts`` is an allowlist of EXACT hostnames — a suffix match would
    let ``oapi.dingtalk.com.evil.com`` pass. ``path_prefix`` is the
    URL path the platform's webhook lives under, so a same-host URL
    pointing somewhere else is rejected too.

    ``security_options`` / ``security_required`` describe the
    platform's OWN security settings for a custom bot. They are the
    single source of truth for what the form offers, what the server
    accepts, and what a row must satisfy — the frontend renders from
    them and never names a platform.
    """

    name: str
    label: str
    hosts: tuple[str, ...]
    path_prefix: str
    supports_file: bool
    max_message: int
    limit_unit: str
    doc_url: str
    #: The security settings this platform offers, in the order its own
    #: UI lists them. Empty means the webhook URL alone is the
    #: credential.
    security_options: tuple[str, ...] = ()
    #: Whether the platform refuses the bot until at least one of them
    #: is configured.
    security_required: bool = False
    #: Per-msgtype overrides of ``max_message`` (Slack caps markdown
    #: lower than plain text).
    limits: dict[str, int] = field(default_factory=dict)
    #: Set by the platform module on import.
    send: SendFn | None = None

    def limit_for(self, msgtype: str) -> int:
        """The size limit that applies to ``msgtype``."""
        return self.limits.get(msgtype, self.max_message)

    # Derived shorthand — kept so no reader has to know the option
    # names, and so the two can never disagree with the registry.

    @property
    def uses_secret(self) -> bool:
        """Whether a signing secret can be stored for this platform."""
        return SECURITY_SIGN in self.security_options

    @property
    def uses_keyword(self) -> bool:
        """Whether keyword security is offered by this platform."""
        return SECURITY_KEYWORD in self.security_options

    def security_error(self) -> str:
        """The 'choose at least one of …' message for this platform."""
        choices = ', '.join(
            SECURITY_LABELS[option] for option in self.security_options
        )
        return (
            f'{self.label} requires at least one security setting '
            f'— choose: {choices}'
        )


PLATFORMS: dict[str, Platform] = {
    # WeCom 群机器人 (doc_url): the webhook ``?key=`` IS the credential.
    # The doc lists no keyword / signature / IP settings for a group
    # bot, so the form shows no security section at all.
    'wecom': Platform(
        name='wecom',
        label='WeCom',
        hosts=('qyapi.weixin.qq.com',),
        path_prefix='/cgi-bin/webhook/',
        supports_file=True,
        max_message=4096,
        limit_unit='bytes',
        # 消息推送配置说明: text content ≤ 2048 bytes, markdown
        # content ≤ 4096. One number for both would let an oversized
        # text through the pre-check and hand the user WeCom's own
        # rejection instead of ours.
        limits={'text': 2048},
        doc_url=('https://developer.work.weixin.qq.com/document/path/91770'),
    ),
    # DingTalk 自定义机器人. 安全设置 offers 自定义关键词 / 加签 /
    # IP 地址（段） — open.dingtalk.com/document/robots/
    # customize-robot-security-settings ("我们提供三种保护措施…
    # 设置自定义关键词、加签（使用签名加密）和 IP 地址（段）"), and the
    # robot is only created once 安全设置 has at least one of them
    # (创建自定义机器人: "安全设置（至少选择一种）"); a message that
    # satisfies none comes back as errcode 310000. So REQUIRED.
    'dingtalk': Platform(
        name='dingtalk',
        label='DingTalk',
        hosts=('oapi.dingtalk.com',),
        path_prefix='/robot/send',
        security_options=(SECURITY_SIGN, SECURITY_KEYWORD, SECURITY_IP),
        security_required=True,
        supports_file=False,
        max_message=20000,
        limit_unit='bytes',
        doc_url=(
            'https://open.dingtalk.com/document/orgapp/custom-robot-access'
        ),
    ),
    # Feishu 自定义机器人. The same guide
    # (open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
    # offers 自定义关键词 / IP 白名单 / 签名校验 and says "In the same
    # custom bot, you can set one or more methods" plus "We strongly
    # recommend adding security settings" — recommended, never
    # required: a bot with none of them still sends.
    'feishu': Platform(
        name='feishu',
        label='Feishu / Lark',
        hosts=('open.feishu.cn', 'open.larksuite.com'),
        path_prefix='/open-apis/bot/v2/hook/',
        security_options=(SECURITY_SIGN, SECURITY_KEYWORD, SECURITY_IP),
        security_required=False,
        supports_file=False,
        max_message=20000,
        limit_unit='bytes',
        doc_url=(
            'https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot'
        ),
    ),
    # Slack incoming webhooks (doc_url): the URL's token is the
    # credential; no keyword / signature / IP settings exist for a
    # webhook, so no security section.
    'slack': Platform(
        name='slack',
        label='Slack',
        hosts=('hooks.slack.com', 'hooks.slack-gov.com'),
        path_prefix='/services/',
        supports_file=False,
        max_message=40000,
        limit_unit='chars',
        # Block Kit markdown blocks cap at 12 000 characters.
        limits={'markdown': 12000},
        doc_url='https://api.slack.com/messaging/webhooks',
    ),
}


# ── URL detection ───────────────────────────────────


def loopback_test_enabled() -> bool:
    """Whether the loopback test exception is on for this process.

    OFF in production: a webhook secret may only ever go to a real
    platform host over https. The offline e2e suite runs loopback
    stubs (a platform webhook; a fake GitHub), so the CI jobs (and a
    local e2e run) start the server with
    ``VIBE_ALLOW_LOOPBACK_WEBHOOKS=1``. Read from the environment at
    CALL time (not import time) so tests can monkeypatch it. Other
    server-initiated fetches (``app.skill_fetch``) honour the SAME
    flag — one switch means "this is a test box" everywhere.
    """
    return os.environ.get('VIBE_ALLOW_LOOPBACK_WEBHOOKS', '') in (
        '1',
        'true',
    )


def is_loopback_host(host: str) -> bool:
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
    (see :func:`loopback_test_enabled`); the platform is then read off the
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

    if is_loopback_host(host):
        if not loopback_test_enabled():
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


# ── Security settings ───────────────────────────────


def split_security_mode(mode: str | None) -> tuple[str, ...]:
    """The option names a stored/sent ``security_mode`` lists.

    Tolerant of whitespace and repeats; an unknown name is kept so the
    caller can name it back in its error rather than silently dropping
    a setting the user meant to turn on.
    """
    if not mode:
        return ()
    seen: list[str] = []
    for part in mode.split(','):
        name = part.strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def join_security_mode(
    platform: Platform, options: tuple[str, ...] | list[str] | set[str]
) -> str | None:
    """The canonical ``security_mode`` string for ``options``.

    Ordered by the platform's own ``security_options``, so the same set
    always stores as the same string (and a re-save does not look like
    a change).
    """
    chosen = set(options)
    ordered = [o for o in platform.security_options if o in chosen]
    # An option the platform does not offer cannot be ordered; keep it
    # visible rather than dropping it, so a bad value never vanishes.
    ordered += [o for o in sorted(chosen) if o not in ordered]
    return ','.join(ordered) if ordered else None


def resolve_security(
    platform: Platform,
    mode: str | None,
    secret: str | None,
    keyword: str | None,
    *,
    authoritative: bool = False,
) -> SecurityState:
    """The one place a bot's security settings are decided.

    The platform registry decides WHAT exists (``security_options``)
    and whether the platform insists on one (``security_required``);
    this turns a caller's (mode, secret, keyword) into the state that
    gets stored, or an error naming what is missing.

    * ``authoritative`` — the caller NAMED its settings (the form
      always does, ``app/routers/chat_bots.py``). The named set is what
      gets stored, so switching a bot off 加签 clears the stored
      secret instead of leaving a value no request signs with.
    * ``authoritative=False`` — the caller only touched the fields
      (an API client, a legacy row). Nothing is dropped: the settings
      in play are the named ones PLUS the ones a filled field implies.

    A value filled for a setting the PLATFORM does not offer is refused
    either way — WeCom and Slack have nothing to switch to.
    """
    clean_secret = (secret or '').strip() or None
    clean_keyword = (keyword or '').strip() or None
    declared = split_security_mode(mode)

    if declared and not platform.security_options:
        return SecurityState(
            error=(
                f'{platform.label} webhooks have no security settings — '
                'the webhook URL alone is the credential'
            )
        )
    unknown = [o for o in declared if o not in platform.security_options]
    if unknown:
        offered = ', '.join(platform.security_options) or 'none'
        return SecurityState(
            error=(
                f'Unknown security setting {", ".join(unknown)} for '
                f'{platform.label} — this platform offers: {offered}'
            )
        )
    if clean_secret and not platform.uses_secret:
        return SecurityState(
            error=f'{platform.label} webhooks do not use a signing secret'
        )
    if clean_keyword and not platform.uses_keyword:
        return SecurityState(
            error=f'{platform.label} webhooks do not use keyword security'
        )

    options = list(declared)
    if not authoritative:
        if clean_secret and SECURITY_SIGN not in options:
            options.append(SECURITY_SIGN)
        if clean_keyword and SECURITY_KEYWORD not in options:
            options.append(SECURITY_KEYWORD)

    if SECURITY_SIGN in options and not clean_secret:
        return SecurityState(
            error=(
                f'{platform.label} needs the signing secret for '
                f'{SECURITY_LABELS[SECURITY_SIGN]} — paste it, or choose '
                'another security setting'
            )
        )
    if SECURITY_KEYWORD in options and not clean_keyword:
        return SecurityState(
            error=(
                f'{platform.label} needs the keyword for '
                f'{SECURITY_LABELS[SECURITY_KEYWORD]} — enter one of the '
                'keywords set on the bot, or choose another security '
                'setting'
            )
        )
    if not options and platform.security_required:
        return SecurityState(error=platform.security_error())

    # A setting that is not in play carries no value: keeping the field
    # would store a secret the bot never signs with (and hand it back to
    # the edit form as if it were live).
    keep_secret = clean_secret if SECURITY_SIGN in options else None
    keep_keyword = clean_keyword if SECURITY_KEYWORD in options else None
    return SecurityState(
        mode=join_security_mode(platform, options),
        options=tuple(options),
        secret=keep_secret,
        keyword=keep_keyword,
    )


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
