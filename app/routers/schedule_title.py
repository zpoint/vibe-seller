"""Schedule titles from the one-box dialogs (see app/task_title.py).

The schedule create and edit dialogs are one prompt box, like the
task dialog: a blank title is derived from the prompt at once and
upgraded by the AI in the background. An explicit title (old REST
clients, MCP ``create_schedule``) is kept as sent.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import resolve_schedule_profile
from app.models.schedule import Schedule
from app.task_title import (
    derive_placeholder,
    needs_ai_title,
    refresh_schedule_title,
)


def title_and_prompt(
    title: str | None, description: str | None
) -> tuple[str, str | None, bool]:
    """(title, description, derived?) for a schedule being saved.

    One of them is needed; a blank title is derived from the
    description.
    """
    title = (title or '').strip()
    description = (description or '').strip() or None
    if not title and not description:
        raise HTTPException(
            status_code=422, detail='title or description is required'
        )
    if title:
        return title, description, False
    return derive_placeholder(description), description, True


def rederive_blank_title(update_data: dict, schedule: Schedule) -> bool:
    """Re-derive a blank title in a PUT body; True when it did.

    A blank title is what the edit dialog sends after a prompt
    change; omitting ``title`` keeps it (old clients, MCP).
    """
    if 'title' not in update_data or (update_data['title'] or '').strip():
        return False
    title, description, _ = title_and_prompt(
        None, update_data.get('description', schedule.description)
    )
    update_data['title'] = title
    if 'description' in update_data:
        update_data['description'] = description
    return True


async def upgrade_title(schedule: Schedule, db: AsyncSession) -> None:
    """Start the AI title on the profile the schedule's fires use."""
    if needs_ai_title(schedule.title, schedule.description):
        refresh_schedule_title(
            schedule.id,
            schedule.title,
            schedule.description,
            await resolve_schedule_profile(schedule, db),
        )
