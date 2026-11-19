"""Yuehulian store enumeration + team-login credentials.

Yuehulian is one GUI login per machine. The user configures that login's
team credentials once (``POST /credentials``); the backend then auto-logs
the client in with them (``ensure_logged_in``) so an expired session never
strands a task. ``GET /stores`` lists the stores that login can see via the
inspector/DOM, returning the same structured status the ziniao browsers
endpoint uses so the UI shows an actionable hint (launch client / log in /
unsupported platform) rather than a bare empty list.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser.adapters import yuehulian_creds
from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.inject.errors import ClientNotRunning, LoginRequired
from app.browser.ziniao_utils import get_platform
from app.database import get_db
from app.models.store import Store
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/yuehulian', tags=['yuehulian'])

_SUPPORTED_PLATFORMS = frozenset({'windows', 'wsl'})


class YuehulianCredentials(BaseModel):
    team_name: str
    subaccount: str
    password: str


@router.get('/credentials')
async def get_yuehulian_credentials(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Whether team-login is configured + the non-secret fields (never the
    password) so the Settings form can show/prefill them."""
    return await yuehulian_creds.credentials_status(db)


async def _yuehulian_store_count(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count(Store.id)).where(
                Store.browser_backend == 'yuehulian'
            )
        )
    ).scalar() or 0


@router.post('/credentials')
async def save_yuehulian_credentials(
    data: YuehulianCredentials,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Store the one machine login's team credentials (password encrypted).

    悦互联 supports **one account per machine**: every bound yuehulian store
    belongs to the single logged-in account, so switching accounts would
    orphan them. Editing the current account (e.g. a new password) is fine,
    but changing to a *different* team/sub-account while yuehulian stores
    exist is refused with a structured ``one_account`` status — the UI tells
    the user to delete all yuehulian stores first, then add again. This is
    the whole of the "account conflict" story: with only one account ever,
    there is no mixed-account runtime case to adjudicate.
    """
    if not (
        data.team_name.strip() and data.subaccount.strip() and data.password
    ):
        raise HTTPException(
            status_code=400,
            detail='team_name, subaccount and password are all required',
        )
    status = await yuehulian_creds.credentials_status(db)
    changing_account = status['configured'] and (
        status['team_name'] != data.team_name.strip()
        or status['subaccount'] != data.subaccount.strip()
    )
    if changing_account and await _yuehulian_store_count(db) > 0:
        raise HTTPException(
            status_code=400,
            detail=json.dumps({'status': 'one_account'}),
        )
    await yuehulian_creds.save_credentials(
        db, data.team_name, data.subaccount, data.password
    )
    return {'ok': True}


@router.delete('/credentials')
async def delete_yuehulian_credentials(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Clear the machine's team-login (the [×] button).

    Leaves any bound stores in place — they report ``login_required`` until
    a login is configured again. Clearing the login is how the user frees
    the single account slot to add a different one (after deleting the old
    account's stores)."""
    await yuehulian_creds.clear_credentials(db)
    return {'ok': True}


@router.get('/stores')
async def list_yuehulian_stores(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Stores visible to the logged-in 悦互联 client (dropdown source).

    Auto-logs the client in with the stored team credentials if it is
    logged out; if there are none (or the login does not take) it reports
    ``login_required`` so the UI prompts to configure/fix credentials.
    """
    platform = get_platform()
    if platform not in _SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=json.dumps({
                'status': 'unsupported_platform',
                'platform': platform,
            }),
        )
    cfg: dict = {}
    await yuehulian_creds.inject_yuehulian_config(cfg, db)
    client = RealYuehulianClient(cfg)
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
    try:
        await client.ensure_logged_in()
    except LoginRequired as e:
        raise HTTPException(
            status_code=502,
            detail=json.dumps({'status': 'login_required'}),
        ) from e
    stores = await client.list_stores()
    return [
        {
            'name': s.name,
            'platform': s.platform,
            'remote_env': s.remote_env,
        }
        for s in stores
    ]
