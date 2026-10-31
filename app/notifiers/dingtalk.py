"""DingTalk (钉钉) custom-robot webhook sender.

Reference: https://open.dingtalk.com/document/orgapp/custom-robot-access

Two things make DingTalk different from WeCom:

  * an optional 加签 (signing) secret, and
  * markdown messages need a ``title`` — it is what the notification
    and the chat session list show, and DingTalk rejects the message
    without it.
"""

import base64
import hashlib
import hmac
import logging
import time
from urllib.parse import quote_plus

from app.notifiers import base

logger = logging.getLogger(__name__)

#: Fallback title when the content has no usable first line.
DEFAULT_TITLE = 'Vibe Seller'
#: DingTalk rejects titles longer than this.
MAX_TITLE = 64


def sign(secret: str, timestamp: str) -> str:
    """DingTalk's 加签 signature for ``timestamp`` (milliseconds).

    The HMAC key is the secret and the message is ``"<ts>\\n<secret>"``;
    the digest is base64'd and then URL-encoded. Note this is NOT the
    same algorithm as Feishu's — see :mod:`app.notifiers.feishu`.
    """
    string_to_sign = f'{timestamp}\n{secret}'
    digest = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    return quote_plus(base64.b64encode(digest).decode('utf-8'))


def derive_title(content: str) -> str:
    """The notification title for a markdown message.

    DingTalk shows this in the notification and the session list, so it
    has to be short and single-line: the first non-empty line of the
    content with any leading ``#`` heading marks and spaces stripped.
    """
    for line in (content or '').splitlines():
        stripped = line.strip().lstrip('#').strip()
        if stripped:
            return stripped[:MAX_TITLE]
    return DEFAULT_TITLE


def signed_url(
    webhook_url: str,
    secret: str | None,
    timestamp: str | None = None,
) -> str:
    """The webhook URL with the ``timestamp``/``sign`` query appended.

    Both go in the QUERY STRING (not the body) for DingTalk.
    """
    if not secret:
        return webhook_url
    ts = timestamp if timestamp is not None else str(round(time.time() * 1000))
    return f'{webhook_url}&timestamp={ts}&sign={sign(secret, ts)}'


async def send_message(
    webhook_url: str,
    secret: str | None,
    content: str,
    msgtype: str = 'text',
) -> tuple[bool, str]:
    """Post a message to a DingTalk custom robot."""
    platform = base.PLATFORMS['dingtalk']
    too_big = base.size_error(platform, content, msgtype)
    if too_big:
        return False, too_big

    if msgtype == 'markdown':
        payload = {
            'msgtype': 'markdown',
            'markdown': {
                'title': derive_title(content),
                'text': content,
            },
        }
    else:
        payload = {
            'msgtype': 'text',
            'text': {'content': content},
        }

    resp, err = await base.post(
        signed_url(webhook_url, secret),
        label='DingTalk',
        json_payload=payload,
        headers={'Content-Type': 'application/json'},
    )
    if resp is None:
        return False, err
    if resp.status_code >= 400:
        # A proxy or gateway can answer 4xx with a JSON body carrying
        # DingTalk's own error — prefer the platform's text when the
        # body parses, else the generic status line.
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get('errmsg') or body.get('msg')
        if err:
            logger.error('DingTalk webhook API error: %s', err)
            return False, err
        logger.error('DingTalk webhook HTTP status %s', resp.status_code)
        return False, f'DingTalk webhook returned HTTP {resp.status_code}.'
    try:
        data = resp.json()
    except ValueError:
        return False, 'DingTalk returned a non-JSON response.'

    if data.get('errcode') == 0:
        return True, ''
    # errmsg is DingTalk's own text (e.g. "sign not match", "keyword
    # not in content"); it never contains our webhook URL.
    err = data.get('errmsg') or f'errcode={data.get("errcode")}'
    logger.error('DingTalk webhook API error: %s', err)
    return False, err


base.PLATFORMS['dingtalk'].send = send_message
