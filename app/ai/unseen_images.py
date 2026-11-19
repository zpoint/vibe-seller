"""Images the task's model cannot see — told to the user before it answers.

A user who attaches a picture to a task running on a text-only model
(per the model catalog, :func:`app.ai.profiles.model_sees_images`) used
to learn that only from the agent's reply, if at all: the agent was told
to "Read them directly", the Read was refused by ``image_guards``, and
the run carried on without the picture. The backend knows before the
model is called, so it says so itself:

* a ``notice`` transcript message (:data:`NOTICE_ROLE`) right after the
  user's message — at task launch for create-time attachments, at send
  time for chat attachments — that the UI renders as a localized banner;
* the agent is told the same in place of "read them", so it does not
  spend a turn on a refused Read.

An unknown model (not in the catalog, or no ``vision`` label) restricts
nothing — the same rule ``image_guards`` follows.
"""

import json

from app.ai.claude_backend_utils import get_next_seq
from app.ai.image_guards import IMAGE_EXT_RE
from app.ai.profiles import (
    DEFAULT_PROFILE_ID,
    ProfileManager,
    model_sees_images,
)
from app.database import async_session
from app.events.bus import event_bus
from app.models.task_message import TaskMessage

# Transcript role of a backend-authored notice. The content is JSON
# (``{"kind": ..., ...}``) so the UI words it in the user's language.
NOTICE_ROLE = 'notice'
IMAGES_UNSEEN = 'images_unseen'


def profile_model(profile_id: str | None) -> str | None:
    """The model a profile runs on (its ``ANTHROPIC_MODEL``), if set.

    Reads ``profiles.json`` — call it off the event loop from async code.
    """
    try:
        env = ProfileManager.get_env_for_profile(
            profile_id or DEFAULT_PROFILE_ID
        )
    except (OSError, ValueError):
        return None
    return env.get('ANTHROPIC_MODEL')


def unseen_images(model: str | None, names: list[str]) -> list[str]:
    """The image files among ``names`` that ``model`` cannot see."""
    if model_sees_images(model) is not False:
        return []
    return [n for n in names if IMAGE_EXT_RE.search(n)]


def images_notice(model: str | None, names: list[str]) -> str | None:
    """A ``notice`` message body for the unseen images, or None."""
    unseen = unseen_images(model, names)
    if not unseen:
        return None
    return json.dumps(
        {'kind': IMAGES_UNSEEN, 'model': model, 'files': unseen},
        ensure_ascii=False,
    )


def agent_note(model: str | None, paths: list[str]) -> str:
    """What the agent is told about attached images it cannot see."""
    if not paths:
        return ''
    listing = '\n'.join(f'- {p}' for p in paths)
    return (
        f"The user also attached image(s) that this task's model "
        f'({model}) cannot see — the user has already been told so, '
        f'with a notice under their message:\n{listing}\n'
        'Do NOT Read them (the Read is refused, and anything you said '
        'about them would be invented). Work from the text of the '
        'request; if the task needs what is in the image, say so '
        'plainly and ask the user to switch to a model that can see '
        'images, or to describe it in text.'
    )


def attached_file_line(path: str, model: str | None) -> str:
    """The chat message's line telling the agent about one attachment."""
    if unseen_images(model, [path]):
        return (
            f'Attached image (this model cannot see images; the user has '
            f'been told — do not Read it): {path}'
        )
    return f'Attached file: {path}'


def chat_attachment_lines(
    profile_id: str | None, attachments: list[dict]
) -> tuple[str, str | None]:
    """``(agent lines, notice)`` for a chat message's promoted files.

    Blocking (reads the profile) — call it with ``asyncio.to_thread``.
    """
    if not attachments:
        return '', None
    model = profile_model(profile_id)
    lines = '\n'.join(
        attached_file_line(a['abs_path'], model) for a in attachments
    )
    return lines, images_notice(model, [a['filename'] for a in attachments])


async def record_notice(task_id: str, notice: str) -> None:
    """Persist a ``notice`` as the task's next message and push it live."""
    async with async_session() as db:
        seq = await get_next_seq(db, task_id)
        db.add(
            TaskMessage(
                task_id=task_id, role=NOTICE_ROLE, content=notice, seq=seq
            )
        )
        await db.commit()
    await event_bus.emit(
        'task_message',
        {'task_id': task_id, 'role': NOTICE_ROLE, 'content': notice},
    )
