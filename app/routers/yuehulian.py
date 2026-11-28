"""Yuehulian compat routes — thin aliases over the generic vendor surface.

The live routes are the registry-driven family in
:mod:`app.routers.browser_vendors`
(``/api/browser-vendors/yuehulian/...``); the frontend uses those. These
``/api/yuehulian/*`` aliases exist because released things still call
them — the workflow suite pins their contract
(``tests/workflow/test_wf_yuehulian_stores.py``, which also patches this
module's ``RealYuehulianClient`` / ``get_platform`` seams) and the docs
name the paths. Remove this module when those migrate to the generic
routes; nothing else may grow a per-vendor router.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser import vendor_creds
from app.browser.adapters import yuehulian_creds
from app.browser.adapters.yuehulian import YUEHULIAN_VENDOR
from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.ziniao_utils import get_platform
from app.database import get_db
from app.models.user import User
from app.routers.browser_vendors import list_store_refs, vendor_store_count

router = APIRouter(prefix='/api/yuehulian', tags=['yuehulian'])


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


@router.post('/credentials')
async def save_yuehulian_credentials(
    data: YuehulianCredentials,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Store the one machine login's team credentials (password encrypted).

    悦互联 = **one account per machine**. The create-store [+] button posts
    here and OVERWRITES the single stored login — but only while NO yuehulian
    store is bound: a bound store belongs to the current account, so changing
    the account would orphan it. With stores present the save is refused
    (structured ``one_account``); the user deletes all yuehulian stores
    first, then adds. This is the whole account-conflict story — one account
    ever, so there is no mixed-account runtime case to adjudicate.
    """
    if not (
        data.team_name.strip() and data.subaccount.strip() and data.password
    ):
        raise HTTPException(
            status_code=400,
            detail='team_name, subaccount and password are all required',
        )
    async with vendor_creds.vendor_account_lock('yuehulian'):
        if await vendor_store_count(db, 'yuehulian') > 0:
            raise HTTPException(
                status_code=400,
                detail=json.dumps({'status': 'one_account'}),
            )
        await yuehulian_creds.save_credentials(
            db, data.team_name, data.subaccount, data.password
        )
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
    cfg: dict = {}
    await yuehulian_creds.inject_yuehulian_config(cfg, db)
    client = RealYuehulianClient(cfg)
    refs = await list_store_refs(YUEHULIAN_VENDOR, client, get_platform())
    return [
        {
            'name': s.name,
            'platform': s.platform,
            'remote_env': s.remote_env,
        }
        for s in refs
    ]
