"""Feishu / Lark (飞书) custom-bot webhook sender.

Reference:
https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

Feishu's 签名校验 (signature check) looks nothing like DingTalk's 加签:

  * the timestamp is in SECONDS, not milliseconds;
  * the HMAC KEY is ``"<timestamp>\\n<secret>"`` and the MESSAGE is
    empty;
  * the base64 signature goes into the JSON BODY as a top-level
    ``sign`` field, not into the query string.

Markdown is delivered as an interactive card (card JSON 2.0) because
Feishu's plain-text bot message has no markdown dialect.
"""

import base64
import hashlib
import hmac
import json
import logging
import time

from app.notifiers import base

logger = logging.getLogger(__name__)


def sign(secret: str, timestamp: str) -> str:
    """Feishu's 签名校验 signature for ``timestamp`` (seconds).

    The HMAC key is ``"<timestamp>\\n<secret>"`` and the message is
    empty; the digest is base64'd and NOT URL-encoded. This is
    deliberately different from DingTalk's 加签.
    """
    string_to_sign = f'{timestamp}\n{secret}'
    digest = hmac.new(
        string_to_sign.encode('utf-8'),
        b'',
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode('utf-8')


def build_payload(
    content: str,
    msgtype: str = 'text',
    secret: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """The JSON body for a Feishu custom bot.

    ``timestamp`` and ``sign`` are TOP-LEVEL body fields, alongside
    ``msg_type``.
    """
    if msgtype == 'markdown':
        payload: dict = {
            'msg_type': 'interactive',
            'card': {
                'schema': '2.0',
                'body': {'elements': [{'tag': 'markdown', 'content': content}]},
            },
        }
    else:
        payload = {
            'msg_type': 'text',
            'content': {'text': content},
        }
    if secret:
        ts = timestamp if timestamp is not None else str(int(time.time()))
        payload['timestamp'] = ts
        payload['sign'] = sign(secret, ts)
    return payload


async def send_message(
    webhook_url: str,
    secret: str | None,
    content: str,
    msgtype: str = 'text',
) -> tuple[bool, str]:
    """Post a message to a Feishu / Lark custom bot."""
    platform = base.PLATFORMS['feishu']
    payload = build_payload(content, msgtype, secret)
    # Feishu caps the whole BODY at 20 KB, so measure what we send.
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    too_big = base.size_error(platform, content, msgtype, measured=len(body))
    if too_big:
        return False, too_big

    resp, err = await base.post(
        webhook_url,
        label='Feishu',
        json_payload=payload,
        headers={'Content-Type': 'application/json'},
    )
    if resp is None:
        return False, err
    if resp.status_code >= 400:
        # Feishu answers some failures (sign mismatch 19021, keyword
        # 19024, IP 19022) with an HTTP 4xx whose JSON body carries the
        # real code/msg — surface that instead of a bare status line.
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get('msg') or body.get('StatusMessage')
        if err:
            logger.error('Feishu webhook API error: %s', err)
            return False, err
        logger.error('Feishu webhook HTTP status %s', resp.status_code)
        return False, f'Feishu webhook returned HTTP {resp.status_code}.'
    try:
        data = resp.json()
    except ValueError:
        return False, 'Feishu returned a non-JSON response.'

    # Newer responses use `code`; older ones use `StatusCode`.
    code = data.get('code')
    if code is None:
        code = data.get('StatusCode')
    if code == 0:
        return True, ''
    err = data.get('msg') or data.get('StatusMessage') or f'code={code}'
    logger.error('Feishu webhook API error: %s', err)
    return False, err


base.PLATFORMS['feishu'].send = send_message
