"""WeChat Work (企业微信) group bot webhook sender.

Supports the simplest bot type: post a JSON payload to the webhook URL.
Reference: https://developer.work.weixin.qq.com/document/path/91770
"""

import logging

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 30


async def send_webhook(
    webhook_url: str,
    content: str,
    msgtype: str = 'text',
) -> tuple[bool, str]:
    """Post a message to a WeCom group bot webhook.

    Returns (ok, message). On success, message is empty; on
    failure, it contains the API error or exception text.
    """
    if msgtype == 'markdown':
        payload = {
            'msgtype': 'markdown',
            'markdown': {'content': content},
        }
    else:
        payload = {
            'msgtype': 'text',
            'text': {'content': content},
        }

    # NOTE: the webhook URL embeds a secret `key=...`. httpx
    # exceptions (e.g. HTTPStatusError) include the request URL in
    # their string form, so we never return the raw exception to
    # callers — those strings flow to the frontend via /test.
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        logger.exception('WeCom webhook HTTP error')
        return False, 'Failed to send webhook request.'
    except Exception:
        logger.exception('WeCom webhook error')
        return False, 'Unexpected error while sending webhook.'

    if data.get('errcode') == 0:
        return True, ''
    # errmsg comes from WeCom and does not contain our webhook URL
    err = data.get('errmsg') or f'errcode={data.get("errcode")}'
    logger.error('WeCom webhook API error: %s', err)
    return False, err
