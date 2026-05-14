from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.screenshot import Screenshot
from app.models.user import User

router = APIRouter(prefix='/api/screenshots', tags=['screenshots'])


@router.get('/{screenshot_id}')
async def get_screenshot(
    screenshot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    sc = await db.get(Screenshot, screenshot_id)
    if not sc:
        raise HTTPException(status_code=404, detail='Screenshot not found')
    path = Path(sc.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail='Screenshot file not found')
    return FileResponse(str(path), media_type='image/png')
