"""Context compaction for history reconstruction.

When a Claude CLI session ends and a new session must start,
chat history is externalized to a JSON file instead of being
stuffed entirely into the prompt.  Only the last few messages
are embedded inline; the agent is instructed to read the full
history file before proceeding.
"""

import logging
import os
from pathlib import Path

from app.browser.manager import atomic_write_json
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

HISTORY_DIR = VIBE_SELLER_DIR / 'task_history'
RECENT_COUNT = 5


def dump_history_file(
    task_id: str,
    messages: list[dict],
) -> Path | None:
    """Write full chat history to a JSON file.

    Returns the file path, or None if *messages* is empty.
    """
    if not messages:
        return None

    path = HISTORY_DIR / f'{task_id}.json'

    entries = []
    for msg in messages:
        entries.append({
            'role': msg.get('role', 'unknown'),
            'content': msg.get('content', ''),
            'seq': msg.get('seq'),
        })

    atomic_write_json(path, entries)
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning('Failed to set permissions on history file %s', path)
    logger.info('Dumped %d history messages to %s', len(entries), path)
    return path


def build_history_prompt(
    messages: list[dict],
    history_file: Path | None,
    recent_count: int = RECENT_COUNT,
) -> str:
    """Build a compact prompt with a file reference + recent messages.

    Returns an empty string when there are no messages.
    """
    if not messages:
        return ''

    parts: list[str] = []

    # Mandatory instruction to read the full history file
    if history_file is not None:
        parts.append(
            'IMPORTANT: This task has prior conversation history '
            f'({len(messages)} messages) saved at {history_file}. '
            'You MUST read this file before proceeding to '
            'understand what was previously discussed and '
            'accomplished.'
        )

    # Inline the most recent messages for immediate context
    recent = messages[-recent_count:]
    if len(messages) > recent_count:
        parts.append(
            f'\nRecent messages (last {len(recent)} of {len(messages)}):'
        )
    else:
        parts.append('\nPrevious conversation:')

    for msg in recent:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        parts.append(f'[{role}]: {content}')

    return '\n\n'.join(parts)
