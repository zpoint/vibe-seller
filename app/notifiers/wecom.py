"""WeChat Work (企业微信) group bot webhook sender.

Supports the simplest bot type: post a JSON payload to the webhook URL.
Reference: https://developer.work.weixin.qq.com/document/path/91770
"""

import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 30
# WeCom group-bot file limit: ≤ 20 MB, > 5 bytes (per the upload_media
# docs). We reject early so a too-big file fails with a clear message
# instead of an opaque WeCom errcode.
MAX_FILE_BYTES = 20 * 1024 * 1024
MIN_FILE_BYTES = 6
UPLOAD_TIMEOUT = 120


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


def webhook_key(webhook_url: str) -> str:
    """Extract the `key=` query param from a group-bot webhook URL.

    The same key drives both the `send` and `upload_media` endpoints,
    so we derive the upload URL from the configured send URL rather
    than asking the user to store a second secret.
    """
    return (parse_qs(urlsplit(webhook_url).query).get('key') or [''])[0]


async def send_file_webhook(
    webhook_url: str,
    file_path: str,
) -> tuple[bool, str]:
    """Upload a local file and post it to a WeCom group bot.

    Two steps (per WeCom docs): POST the bytes to `upload_media` to get
    a `media_id` (valid 3 days), then send a `msgtype=file` message
    referencing it. Returns (ok, message); on failure message holds the
    error. Never echoes the webhook URL (it embeds the secret key).
    """
    key = webhook_key(webhook_url)
    if not key:
        return False, 'Webhook URL is missing its key= parameter.'

    p = Path(file_path)
    try:
        size = p.stat().st_size
    except OSError:
        return False, f'File not found: {p.name}'
    if not p.is_file():
        return False, f'Not a regular file: {p.name}'
    if size < MIN_FILE_BYTES:
        return False, f'File too small for WeCom (<{MIN_FILE_BYTES} bytes).'
    if size > MAX_FILE_BYTES:
        mb = size / 1024 / 1024
        return False, f'File too large for WeCom: {mb:.1f} MB > 20 MB.'

    upload_url = (
        'https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media'
        f'?key={key}&type=file'
    )
    send_url = f'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}'
    filename = p.name

    try:
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
            with open(p, 'rb') as fh:
                # Field name MUST be `media`; filename MUST be present.
                up = await client.post(
                    upload_url,
                    files={'media': (filename, fh, 'application/octet-stream')},
                )
            up.raise_for_status()
            up_data = up.json()
            if up_data.get('errcode') != 0:
                err = (
                    up_data.get('errmsg') or f'errcode={up_data.get("errcode")}'
                )
                logger.error('WeCom upload_media error: %s', err)
                return False, f'Upload failed: {err}'
            media_id = up_data.get('media_id')
            if not media_id:
                return False, 'Upload succeeded but returned no media_id.'

            resp = await client.post(
                send_url,
                json={'msgtype': 'file', 'file': {'media_id': media_id}},
                headers={'Content-Type': 'application/json'},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        logger.exception('WeCom file send HTTP error')
        return False, 'Failed to send file to WeCom.'
    except OSError:
        logger.exception('WeCom file send read error')
        # Return only the basename — the other branches do the same;
        # never echo the absolute server path back to the caller.
        return False, f'Could not read file: {p.name}'
    except Exception:
        logger.exception('WeCom file send error')
        return False, 'Unexpected error while sending file.'

    if data.get('errcode') == 0:
        return True, ''
    err = data.get('errmsg') or f'errcode={data.get("errcode")}'
    logger.error('WeCom file send API error: %s', err)
    return False, err
