"""API endpoints for the workspace AI assistant."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.profiles import DEFAULT_PROFILE_ID
from app.ai.workspace_assistant import ws_assistant_manager
from app.auth import get_current_user
from app.browser.manager import store_slug
from app.database import async_session
from app.models.store import Store
from app.models.user import User
from app.prompts import WORKSPACE_ASSISTANT_PROMPT

router = APIRouter(
    prefix='/api/workspace/assistant', tags=['workspace-assistant']
)


class MessageRequest(BaseModel):
    content: str
    profile_id: str | None = None


async def _build_system_prompt() -> str:
    """Build system prompt with current store list."""
    try:
        async with async_session() as db:
            result = await db.execute(select(Store))
            stores = result.scalars().all()
            if stores:
                lines = []
                for s in stores:
                    slug = store_slug(s.name, s.id)
                    backend = s.browser_backend or 'chrome'
                    lines.append(
                        f'- **{s.name}** (slug: `{slug}`, backend: {backend})'
                    )
                stores_list = '\n'.join(lines)
            else:
                stores_list = '_No stores configured yet._'
    except Exception:
        stores_list = '_Unable to load stores._'

    return WORKSPACE_ASSISTANT_PROMPT.replace('{stores_list}', stores_list)


@router.post('/message')
async def send_message(
    body: MessageRequest,
    _user: User = Depends(get_current_user),
):
    """Send a message to the workspace assistant."""
    if not body.content.strip():
        raise HTTPException(status_code=400, detail='Message cannot be empty')

    profile_id = body.profile_id or DEFAULT_PROFILE_ID
    system_prompt = await _build_system_prompt()

    started = await ws_assistant_manager.send_or_start(
        user_id=_user.id,
        message=body.content,
        profile_id=profile_id,
        system_prompt=system_prompt,
    )

    return {'ok': started, 'user_id': _user.id}


@router.post('/stop')
async def stop_assistant(
    _user: User = Depends(get_current_user),
):
    """Stop the workspace assistant for the current user."""
    stopped = await ws_assistant_manager.stop(_user.id)
    return {'ok': stopped}


@router.get('/status')
async def get_status(
    _user: User = Depends(get_current_user),
):
    """Check if the workspace assistant is running."""
    running = ws_assistant_manager.is_running(_user.id)
    return {'running': running}
