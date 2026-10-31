"""Slack incoming-webhook sender.

Reference: https://api.slack.com/messaging/webhooks

Slack has no signing secret — the webhook URL *is* the credential, and
Slack auto-revokes a URL that leaks. Two dialect quirks:

  * ``msgtype=text`` renders a small subset of markup, so ``&``, ``<``
    and ``>`` must be escaped for the message to show literally.
  * Slack's ``text`` field is "mrkdwn", NOT standard markdown —
    ``**bold**``, ``# heading`` and ``[t](u)`` all render literally.
    Standard markdown therefore goes into a Block Kit ``markdown``
    block, which does accept it.

The webhook answers with the PLAIN TEXT ``ok`` — not JSON — and 4xx
errors carry a plain-text reason such as ``invalid_payload``.
"""

import logging
import re

from app.notifiers import base

logger = logging.getLogger(__name__)

#: How long the top-level notification text may be.
MAX_SUMMARY = 200


def escape_text(content: str) -> str:
    """Escape a plain-text message so Slack shows it literally."""
    return (
        (content or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def to_mrkdwn(content: str) -> str:
    """Convert standard markdown to Slack's mrkdwn dialect.

    Only used as a fallback when the Block Kit ``markdown`` block is
    rejected by the webhook.
    """
    text = content or ''
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'^#{1,6}\s*(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    return text


def summarize(content: str) -> str:
    """A short plain-text summary for the notification / a11y text."""
    for line in (content or '').splitlines():
        stripped = line.strip().lstrip('#').strip()
        if stripped:
            return stripped[:MAX_SUMMARY]
    return (content or '')[:MAX_SUMMARY]


def build_payload(content: str, msgtype: str = 'text') -> dict:
    """The JSON body for a Slack incoming webhook."""
    if msgtype == 'markdown':
        return {
            # Slack wants a non-empty top-level text alongside the
            # blocks — it is the notification and fallback rendering.
            'text': escape_text(summarize(content)),
            'blocks': [{'type': 'markdown', 'text': content}],
        }
    return {'text': escape_text(content)}


async def send_message(
    webhook_url: str,
    secret: str | None,
    content: str,
    msgtype: str = 'text',
) -> tuple[bool, str]:
    """Post a message to a Slack incoming webhook."""
    platform = base.PLATFORMS['slack']
    too_big = base.size_error(platform, content, msgtype)
    if too_big:
        return False, too_big

    resp, err = await base.post(
        webhook_url,
        label='Slack',
        json_payload=build_payload(content, msgtype),
        headers={'Content-Type': 'application/json'},
    )
    if resp is None:
        return False, err
    return await _interpret(resp, webhook_url, content, msgtype)


async def _interpret(
    resp,
    webhook_url: str,
    content: str,
    msgtype: str,
) -> tuple[bool, str]:
    """Turn a Slack webhook response into (ok, message).

    Slack answers 200 with the plain text ``ok``. A 4xx carries a
    plain-text reason (``invalid_payload``, ``no_text``,
    ``channel_not_found``, ...) — safe to show, it never contains the
    webhook URL.
    """
    body = (resp.text or '').strip()
    if resp.status_code == 200:
        if body == 'ok':
            return True, ''
        return False, body or 'Slack did not accept the message.'
    if body == 'invalid_blocks' and msgtype == 'markdown':
        # The Block Kit markdown block was rejected — fall back to
        # converting standard markdown into Slack's mrkdwn dialect.
        logger.warning('Slack rejected the markdown block; retrying as text')
        return await _send_mrkdwn(webhook_url, content)
    return False, body or f'Slack webhook returned HTTP {resp.status_code}.'


async def _send_mrkdwn(webhook_url: str, content: str) -> tuple[bool, str]:
    """Retry a rejected markdown message as converted mrkdwn text."""
    retry, err = await base.post(
        webhook_url,
        label='Slack',
        json_payload={'text': to_mrkdwn(content)},
        headers={'Content-Type': 'application/json'},
    )
    if retry is None:
        return False, err
    body = (retry.text or '').strip()
    if retry.status_code == 200 and body == 'ok':
        return True, ''
    return False, body or 'Slack did not accept the message.'


base.PLATFORMS['slack'].send = send_message
