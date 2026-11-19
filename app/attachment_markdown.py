"""How a user's attachments appear inside a transcript user message.

ONE encoding for every user message that carries files: a markdown image
``![name](url)`` per file, after the text. The UI's ``MessageBubble`` /
``TaskStartCard`` parse these out into thumbnails (or a PDF chip), so the
transcript shows what the user sent, never a path.

Two producers share it:

* a chat message (``POST /api/tasks/{id}/messages``) — its promoted
  staged files;
* a task's OPENING message — the files attached when the task was created
  (``task_attachments`` rows, uploaded before the deferred agent start).
  The agent backend persists that message at launch, so it is encoded
  there (:func:`opening_message_content`). Without this the opening bubble
  showed only the text and the pasted image was invisible, although the
  agent itself was told about it in its system prompt.

The agent never sees these URLs as instructions: it gets the absolute
paths separately (``uploaded_files_note`` / the chat ``Attached file:``
lines).
"""

import re

from sqlalchemy import exists, select

from app.database import async_session
from app.models.task_attachment import TaskAttachment
from app.models.task_message import TaskMessage

# ``[`` / ``]`` in a name would end the alt text early and line breaks
# would split the line, leaving the rest as raw text in the bubble.
_ALT_BRACKETS = re.compile(r'[\[\]]')
_WHITESPACE = re.compile(r'\s+')


def attachment_url(attachment_id: str) -> str:
    """The create-time attachment's download URL (``routers/attachments``)."""
    return f'/api/attachments/file/{attachment_id}'


def _alt(name: str) -> str:
    return _WHITESPACE.sub(' ', _ALT_BRACKETS.sub('', name)).strip()


def image_markdown(files: list[tuple[str, str]]) -> str:
    """``![name](url)`` lines for ``(name, url)`` pairs."""
    return '\n'.join(f'![{_alt(name)}]({url})' for name, url in files)


def with_attachments(content: str, files: list[tuple[str, str]]) -> str:
    """``content`` followed by its attachments, in the shared encoding."""
    md = image_markdown(files)
    if not md:
        return content
    return f'{content}\n{md}'.strip() if content else md


async def opening_message_content(task_id: str, prompt: str) -> str:
    """The transcript form of a task's opening user message.

    The prompt plus the files attached at create time — but only when
    this IS the opening message (no user message persisted yet), so a
    later re-persisted prompt does not repeat the images. A retry deletes
    the transcript, so its fresh opening message carries them again.
    """
    async with async_session() as db:
        has_user_msg = await db.scalar(
            select(
                exists().where(
                    TaskMessage.task_id == task_id,
                    TaskMessage.role == 'user',
                )
            )
        )
        if has_user_msg:
            return prompt
        rows = (
            await db.execute(
                select(TaskAttachment)
                .where(TaskAttachment.task_id == task_id)
                .order_by(TaskAttachment.created_at)
            )
        ).scalars()
        files = [(a.file_name, attachment_url(a.id)) for a in rows]
    return with_attachments(prompt, files)
