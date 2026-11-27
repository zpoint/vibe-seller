"""Settings → Integrations: workplace CLIs (WeCom / Feishu / DingTalk /
Google Workspace).

Reading status is open to any signed-in user; everything that changes
the machine — download, login, enable, disable — is admin only, and so
is the running login's view: its QR / link signs this server's CLI in
as whoever scans it, for the whole team.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import telemetry
from app.auth import get_current_user, require_admin
from app.cli_integrations import attention, download, manager
from app.cli_integrations.base import CliIntegration
from app.cli_integrations.login import login_manager
from app.cli_integrations.registry import INTEGRATIONS
from app.models.user import User
from app.telemetry_events import TelemetryEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/integrations/cli', tags=['integrations'])


def _integ(key: str) -> CliIntegration:
    try:
        return manager.get(key)
    except KeyError:
        raise HTTPException(404, f'unknown integration {key!r}') from None


def _fail(e: Exception, what: str) -> HTTPException:
    """``detail = {code, message}``: the page shows its own sentence for
    *code* (what to click next) and keeps *message* folded away."""
    if isinstance(e, manager.IntegrationError):
        return HTTPException(400, {'code': e.code, 'message': str(e)})
    if isinstance(e, download.DownloadError):
        return HTTPException(
            502, {'code': 'download_failed', 'message': f'{what}: {e}'}
        )
    logger.exception('%s failed', what)
    return HTTPException(500, {'code': 'unexpected', 'message': f'{what}: {e}'})


@router.get('')
async def list_integrations(_user: User = Depends(get_current_user)):
    """Every platform's card. A running login's link/QR is NOT here
    (admins read it from ``GET /{key}/login``)."""
    cards = await asyncio.gather(
        *(manager.status(i) for i in INTEGRATIONS.values())
    )
    for card in cards:
        if card['login'] is not None:
            card['login'] = {
                'state': card['login']['state'],
                'step': card['login']['step'],
                'steps': card['login']['steps'],
            }
    return {'integrations': cards}


@router.get('/attention')
async def needs_signing_in_again(user: User = Depends(get_current_user)):
    """Enabled platforms whose authorization expired — for the bar at the top of the app. Admins only: nobody
    else can sign a platform in again."""
    if user.role != 'admin':
        return {'items': []}
    return {'items': await attention.attention()}


@router.get('/{key}/expiry')
async def expiry(key: str, _user: User = Depends(get_current_user)):
    """Whether the sign-in expired, checked now with read-only calls
    (``expired: null``: nothing to check, or no answer told)."""
    return {'expired': await attention.expired(_integ(key))}


@router.post('/{key}/install')
async def install(key: str, _admin: User = Depends(require_admin)):
    """Download (or update to) the current release."""
    integ = _integ(key)
    try:
        manifest = await manager.install(integ)
    except Exception as e:
        raise _fail(e, f'{integ.title} install') from e
    telemetry.send(
        TelemetryEvent.INTEGRATION_CLI_INSTALLED, {'platform': integ.key}
    )
    return {'ok': True, 'version': manifest['version']}


class SetupRequest(BaseModel):
    # The text of what the person provided (Google: the downloaded
    # sign-in key file).
    payload: str


@router.post('/{key}/setup')
async def setup(
    key: str, body: SetupRequest, _admin: User = Depends(require_admin)
):
    """Store the one-time setup a platform's sign-in needs."""
    integ = _integ(key)
    try:
        return await manager.setup(integ, body.payload)
    except Exception as e:
        raise _fail(e, f'{integ.title} setup') from e


class LoginRequest(BaseModel):
    enable_after: bool = False


@router.post('/{key}/login')
async def start_login(
    key: str,
    body: LoginRequest | None = None,
    _admin: User = Depends(require_admin),
):
    integ = _integ(key)
    try:
        view = await manager.start_login(
            integ, enable_after=bool(body and body.enable_after)
        )
    except Exception as e:
        raise _fail(e, f'{integ.title} login') from e
    telemetry.send(
        TelemetryEvent.INTEGRATION_CLI_LOGIN_STARTED, {'platform': integ.key}
    )
    return view.public()


@router.get('/{key}/login')
async def login_view(key: str, _admin: User = Depends(require_admin)):
    integ = _integ(key)
    session = login_manager.get(integ.key)
    if session is None:
        raise HTTPException(404, 'no login in progress')
    return session.view.public()


@router.get('/{key}/login/qr')
async def login_qr(key: str, _admin: User = Depends(require_admin)):
    integ = _integ(key)
    session = login_manager.get(integ.key)
    path = session.qr_path() if session is not None else None
    if path is None:
        raise HTTPException(404, 'no QR code yet')
    return FileResponse(
        path, media_type='image/png', headers={'Cache-Control': 'no-store'}
    )


class PasteBackRequest(BaseModel):
    address: str


@router.post('/{key}/login/paste')
async def login_paste_back(
    key: str, body: PasteBackRequest, _admin: User = Depends(require_admin)
):
    """The address the browser ended on, when that browser is not on
    this computer (``LoginStep.paste_back``)."""
    integ = _integ(key)
    try:
        await manager.paste_back(integ, body.address)
    except Exception as e:
        raise _fail(e, f'{integ.title} sign-in') from e
    return {'ok': True}


@router.delete('/{key}/login')
async def cancel_login(key: str, _admin: User = Depends(require_admin)):
    integ = _integ(key)
    return {'cancelled': await login_manager.cancel(integ.key)}


@router.post('/{key}/enable')
async def enable(key: str, _admin: User = Depends(require_admin)):
    integ = _integ(key)
    try:
        result = await manager.enable(integ)
    except Exception as e:
        raise _fail(e, f'{integ.title} enable') from e
    telemetry.send(
        TelemetryEvent.INTEGRATION_CLI_ENABLED, {'platform': integ.key}
    )
    return {'ok': True, **result}


@router.post('/{key}/disable')
async def disable(key: str, _admin: User = Depends(require_admin)):
    integ = _integ(key)
    result = await manager.disable(integ)
    telemetry.send(
        TelemetryEvent.INTEGRATION_CLI_DISABLED, {'platform': integ.key}
    )
    return {'ok': True, **result}
