"""Automatic task titles: an instant placeholder, then an AI upgrade.

The create dialog takes one prompt box, like a chat. A task created
without a title gets:

1. ``derive_placeholder`` — the first sentence of the description,
   collapsed to one line and capped at ``TITLE_MAX_LEN`` characters.
   Stored with the row, so the list shows something the moment the
   task exists.
2. ``refresh_title`` — fire-and-forget: a one-shot ``claude -p`` on the
   task's own AI profile (``--model haiku`` resolves to the profile's
   small model, so a third-party provider is used the same way the
   agent uses it) writes a short title, which replaces the placeholder
   only if the row still carries it (compare-and-set), then a
   ``task_title`` SSE event updates open clients.

Any failure keeps the placeholder — a title is cosmetic and must never
hold up or fail a task.
"""

import asyncio
import json
import logging
import re

from sqlalchemy import update

from app.ai.claude_backend_utils import resolve_claude_binary
from app.ai.claude_isolation import isolation_settings, setting_sources_flags
from app.ai.profiles import ProfileManager
from app.config import VIBE_SELLER_DIR
from app.database import async_session
from app.env_options import Options
from app.events.bus import event_bus
from app.models.task import Task

logger = logging.getLogger(__name__)

TITLE_MAX_LEN = 50
GENERATE_TIMEOUT_S = 60
# Enough for the model to see the intent; the tail is detail.
_MAX_INPUT_CHARS = 2000

# First sentence: up to a Latin terminator followed by whitespace, a
# CJK terminator, or a line break. Capture group, not lookbehind.
_FIRST_SENTENCE = re.compile(r'^(.*?(?:[.!?](?=\s)|[。！？]|\n))')

TITLE_PROMPT = """\
You name tasks for an e-commerce operations app. Write a concise \
title for the task the user describes: 3-7 words in English, or \
6-16 characters in Chinese. Use the SAME language as the user. Keep \
concrete identifiers (SKU, store, marketplace) when they are the \
point of the task. No quotes, no trailing punctuation.

Reply with JSON only: {"title": "..."}

Good: {"title": "Lower widget-006 bid to 1.00"}
Good: {"title": "下载10月结算报表"}
Bad (too vague): {"title": "Task"}
Bad (too long): {"title": "Please go to the seller central and download \
all the settlement reports for last month"}"""

# Strong refs: asyncio only weakly references running tasks.
_pending: set[asyncio.Task] = set()


def derive_placeholder(text: str) -> str:
    """First sentence of *text*, one line, at most TITLE_MAX_LEN chars."""
    clean = text.strip().lstrip('#').strip()
    m = _FIRST_SENTENCE.match(clean)
    first = m.group(1) if m else clean
    flat = re.sub(r'\s+', ' ', first).strip()
    if len(flat) > TITLE_MAX_LEN:
        return flat[: TITLE_MAX_LEN - 1] + '…'
    return flat


def is_derived_from(title: str, description: str | None) -> bool:
    """True when *title* is just the opening of *description*.

    A placeholder title carries nothing the description does not, so
    the prompt sends the description alone instead of repeating it.
    """
    if not description:
        return False
    head = title.removesuffix('\u2026').strip()
    flat = re.sub(r'\s+', ' ', description.strip().lstrip('#').strip())
    return bool(head) and flat.startswith(head)


def _parse_title(raw: str) -> str | None:
    """The title out of the model's reply (JSON, or bare text)."""
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`').removeprefix('json').strip()
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1]).get('title')
        except (ValueError, AttributeError):
            value = None
        if isinstance(value, str):
            text = value
    title = re.sub(r'\s+', ' ', text).strip().strip('"\'')
    if not title or len(title) > TITLE_MAX_LEN * 2:
        return None
    return title[:TITLE_MAX_LEN]


async def generate_title(text: str, profile_id: str | None) -> str | None:
    """Ask the profile's small model for a title; None on any failure."""
    if Options.MOCK_CLI.get():
        # Test runs have no model credits; the placeholder stands.
        return None
    env = await asyncio.to_thread(
        ProfileManager.get_env_for_profile, profile_id or 'default'
    )
    settings = isolation_settings(VIBE_SELLER_DIR, env)
    cmd = [
        resolve_claude_binary(),
        '-p',
        '--model',
        'haiku',
        '--output-format',
        'json',
        '--no-session-persistence',
        '--tools',
        '',
        '--strict-mcp-config',
        '--system-prompt',
        TITLE_PROMPT,
        '--settings',
        json.dumps(settings),
        *setting_sources_flags(profile_id),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(VIBE_SELLER_DIR),
            env=env,
        )
    except OSError as e:
        logger.warning('Title generation could not start: %s', e)
        return None
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(text[:_MAX_INPUT_CHARS].encode()),
            GENERATE_TIMEOUT_S,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning('Title generation timed out')
        return None
    try:
        reply = json.loads(out.decode(errors='replace'))
    except ValueError:
        reply = None
    if (
        proc.returncode != 0
        or not isinstance(reply, dict)
        or reply.get('is_error')
    ):
        logger.warning(
            'Title generation failed (rc=%s): %s',
            proc.returncode,
            (err or out).decode(errors='replace')[-300:],
        )
        return None
    return _parse_title(str(reply.get('result') or ''))


async def _refresh(
    task_id: str, placeholder: str, text: str, profile_id: str | None
) -> None:
    title = await generate_title(text, profile_id)
    if not title or title == placeholder:
        return
    async with async_session() as db:
        result = await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.title == placeholder)
            .values(title=title)
        )
        await db.commit()
    if result.rowcount:
        await event_bus.emit('task_title', {'task_id': task_id, 'title': title})


def refresh_title(
    task_id: str, placeholder: str, text: str, profile_id: str | None
) -> None:
    """Schedule the AI title upgrade for a just-created task."""

    async def run() -> None:
        try:
            await _refresh(task_id, placeholder, text, profile_id)
        except Exception:
            logger.exception('Title refresh failed for %s', task_id)

    t = asyncio.get_running_loop().create_task(run())
    _pending.add(t)
    t.add_done_callback(_pending.discard)
