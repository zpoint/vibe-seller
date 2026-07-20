import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser import aux_browser
from app.browser.manager import browser_manager
from app.config import BASE_DIR
from app.database import get_db
from app.models.app_settings import AppSettings
from app.models.store import Store
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=['browser'])


@router.get('/api/ziniao/launcher')
async def download_ziniao_launcher():
    """Download the Ziniao WebDriver launcher batch file."""
    bat_path = BASE_DIR / 'ziniao_webdriver.bat'
    if not bat_path.exists():
        raise HTTPException(status_code=404, detail='Launcher script not found')
    return FileResponse(
        path=str(bat_path),
        filename='ziniao_webdriver.bat',
        media_type='application/octet-stream',
    )


@router.post('/api/browser/web/start')
async def start_web_browser(
    force: bool = Query(
        False,
        description=(
            'Accepted for parity with the store browser-start route. '
            'The web browser has no Ziniao force-restart path, so this '
            'is currently a no-op; wrappers pass force=1.'
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start (or reuse) the store-less orchestrator web browser.

    The web wrapper (``bin/_web/browser-use``) calls this lazily on
    first use, mirroring ``POST /api/stores/{id}/browser/start`` for
    store browsers.
    """
    try:
        await browser_manager.start_web_session(db)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to start web browser: {e}',
        ) from e
    return {'ok': True}


@router.post('/api/stores/{store_id}/browser/aux/start')
async def start_aux_browser(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Lazily start (or return) a store's AUX browser.

    The aux browser is the store's independent, LOGIN-LESS Chromium for
    sites the main (Ziniao) browser restricts — see
    ``app/browser/aux_browser.py``. The store wrapper's
    ``--session {slug}-aux`` branch calls this and attaches its daemon
    to the returned ``ws`` endpoint (this store's own aux proxy), so an
    aux session can never ambiently attach to another store's browser.
    """
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    if os.environ.get('CI') == 'true':
        headless = True
    else:
        row = await db.get(AppSettings, 'browser_headless')
        headless = bool(row and row.value == 'true')
    try:
        return await aux_browser.start_aux(store, headless)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to start aux browser: {e}',
        ) from e
