"""Yuehulian store enumeration for the Store-form dropdown.

Yuehulian has no local account record — the user logs into the GUI client
manually (one login identity per machine). This endpoint lists the stores
visible to that login via the inspector/DOM, returning the same kind of
structured status the ziniao browsers endpoint uses so the UI can show an
actionable hint (launch client / log in / unsupported platform) instead
of a bare empty list.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.inject.errors import ClientNotRunning
from app.browser.ziniao_utils import get_platform
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/yuehulian', tags=['yuehulian'])

_SUPPORTED_PLATFORMS = frozenset({'windows', 'wsl'})


@router.get('/stores')
async def list_yuehulian_stores(_user: User = Depends(get_current_user)):
    """Stores visible to the logged-in 悦互联 client (dropdown source)."""
    platform = get_platform()
    if platform not in _SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=json.dumps({
                'status': 'unsupported_platform',
                'platform': platform,
            }),
        )
    client = RealYuehulianClient({})
    try:
        await client.ensure_running()
    except ClientNotRunning as e:
        raise HTTPException(
            status_code=502,
            detail=json.dumps({
                'status': 'client_not_running',
                'message': str(e),
            }),
        ) from e
    if not await client.is_logged_in():
        raise HTTPException(
            status_code=502,
            detail=json.dumps({'status': 'login_required'}),
        )
    stores = await client.list_stores()
    return [
        {
            'name': s.name,
            'platform': s.platform,
            'remote_env': s.remote_env,
        }
        for s in stores
    ]
