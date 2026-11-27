import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser import aux_browser
from app.browser.launch_guards import BrowserBusyError
from app.browser.manager import browser_manager
from app.browser.vendors import vendor_spec
from app.browser.win_app_locator import locate
from app.browser.ziniao_launcher import build_launcher_bat
from app.database import get_db
from app.models.app_settings import AppSettings
from app.models.store import Store
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=['browser'])


@router.get('/api/ziniao/launcher')
async def download_ziniao_launcher():
    """Download the Ziniao WebDriver launcher batch file.

    Generated at download time (``app/browser/ziniao_launcher.py``)
    with the exe path the shared locator found on this machine, so a
    double-click launches a custom install dir — a Chinese-named
    folder on any drive — exactly like a default one. When nothing is
    located, the script keeps the conventional-path fallback list.
    """
    # Ziniao's install descriptor comes from the vendor registry (the
    # spec carries it); read at request time so importing this module
    # never triggers a plugin load. Without a descriptor the launcher
    # keeps its conventional-path fallback list.
    spec = vendor_spec('ziniao')
    located = (
        await asyncio.to_thread(locate, spec.win_app)
        if spec is not None and spec.win_app is not None
        else None
    )
    if located:
        logger.info(
            'ziniao launcher baked with located exe: %s', located.exe_path
        )
    bat = build_launcher_bat(located.exe_path if located else None)
    return Response(
        content=bat,
        media_type='application/octet-stream',
        headers={
            'Content-Disposition': 'attachment; filename="ziniao_webdriver.bat"'
        },
    )


@router.post('/api/browser/web/start')
async def start_web_browser(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start (or reuse) the store-less orchestrator web browser.

    The web wrapper (``bin/_web/browser-use``) calls this lazily on
    first use, mirroring ``POST /api/stores/{id}/browser/start`` for
    store browsers.
    """
    try:
        port = await browser_manager.start_web_session(db)
    except BrowserBusyError as e:
        # Busy is not broken — see the same branch in stores.py.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to start web browser: {e}',
        ) from e
    # Typed fields so the web wrapper can follow a reallocated proxy
    # port (PR C1 — same contract as the store start route).
    return {
        'ok': True,
        'proxy_port': port,
        'instance_id': browser_manager.instance_id,
    }


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
