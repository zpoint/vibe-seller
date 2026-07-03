from datetime import UTC, datetime
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import get_current_user
from app.browser.ziniao_utils import (
    ZiniaoNormalModeError,
    ensure_ziniao_running,
    get_ziniao_status,
    kill_and_relaunch_ziniao,
    try_connect_ziniao,
)
from app.database import get_db
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.schemas.ziniao_account import (
    ZiniaoAccountCreate,
    ZiniaoAccountResponse,
    ZiniaoAccountUpdate,
    ZiniaoBrowserProfile,
)
from app.telemetry_events import TelemetryEvent
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/ziniao-accounts', tags=['ziniao-accounts'])


def _decrypt_or_400(encrypted: str) -> str:
    """Decrypt a stored password; raise structured 400 on failure.

    Fernet decrypts will fail when the row was sealed with a JWT_SECRET
    other than the one this install resolves (e.g. DB copied between
    machines whose ~/.vibe-seller/data/jwt_secret was auto-generated
    independently). The user fix is to re-enter the password, so we
    surface that as a structured ziniao status the Sidebar can render
    next to an Edit button — not a bare 500.
    """
    try:
        return decrypt_password(encrypted)
    except Exception:
        logger.exception('Failed to decrypt ziniao account password')
        raise HTTPException(
            status_code=400,
            detail=json.dumps({'status': 'credentials_error'}),
        )


@router.get('', response_model=list[ZiniaoAccountResponse])
async def list_accounts(
    db: AsyncSession = Depends(get_db), _user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(ZiniaoAccount).order_by(ZiniaoAccount.created_at.desc())
    )
    return result.scalars().all()


@router.post('', response_model=ZiniaoAccountResponse)
async def create_account(
    data: ZiniaoAccountCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    account = ZiniaoAccount(
        name=data.name,
        company=data.company,
        username=data.username,
        encrypted_password=encrypt_password(data.password),
        socket_port=data.socket_port,
        client_path=data.client_path,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    total = (
        await db.execute(select(func.count(ZiniaoAccount.id)))
    ).scalar() or 0
    telemetry.send(
        TelemetryEvent.ZINIAO_ACCOUNT_ADDED,
        {'account_count_after_bucket': telemetry.count_bucket(total)},
    )
    return account


@router.put('/{account_id}', response_model=ZiniaoAccountResponse)
async def update_account(
    account_id: str,
    data: ZiniaoAccountUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    account = await db.get(ZiniaoAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Ziniao account not found')

    update_data = data.model_dump(exclude_unset=True)
    # Encrypt password separately; ignore an explicit-None or empty-string
    # password (treat as "no change") so the frontend can omit it on edits
    # that don't touch the password.
    if 'password' in update_data:
        raw_pw = update_data.pop('password')
        if raw_pw:
            account.encrypted_password = encrypt_password(raw_pw)

    for field, value in update_data.items():
        setattr(account, field, value)
    account.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(account)
    return account


@router.delete('/{account_id}')
async def delete_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    account = await db.get(ZiniaoAccount, account_id)
    if account:
        await db.delete(account)
        await db.commit()
        telemetry.send(TelemetryEvent.ZINIAO_ACCOUNT_REMOVED, {})
    return {'ok': True}


@router.get('/{account_id}/browsers', response_model=list[ZiniaoBrowserProfile])
async def list_browsers(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Fetch browser profiles from Ziniao API using account credentials.
    Auto-launches Ziniao client if not running.
    """
    account = await db.get(ZiniaoAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Ziniao account not found')

    user_info = {
        'company': account.company,
        'username': account.username,
        'password': _decrypt_or_400(account.encrypted_password),
    }
    client_path = account.client_path or 'ziniao'

    # Ensure ziniao is running, launch if needed
    try:
        await ensure_ziniao_running(account.socket_port, client_path, user_info)
    except ZiniaoNormalModeError:
        # Structured status for frontend (Mac normal mode)
        status = await get_ziniao_status(account.socket_port, user_info)
        raise HTTPException(
            status_code=502,
            detail=json.dumps(status),
        )
    except RuntimeError as e:
        logger.error('Ziniao ensure_running failed: %s', e)
        # Try structured status; fall back to raw message
        try:
            status = await get_ziniao_status(account.socket_port, user_info)
            raise HTTPException(
                status_code=502,
                detail=json.dumps(status),
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail=str(e))

    # Fetch browser list
    data = {
        'action': 'getBrowserList',
        'requestId': str(uuid.uuid4()),
        **user_info,
    }

    # Use try_connect_ziniao (not raw send_http): on WSL the cached
    # ziniao_host can be the gateway IP, but the first probe in
    # ensure_ziniao_running may have succeeded via 127.0.0.1. The
    # try_connect_ziniao helper does the same loopback-first fallback
    # so we don't fall off the path that just worked.
    result, _host = await try_connect_ziniao(
        account.socket_port, data, timeout=120
    )
    if not result:
        raise HTTPException(
            status_code=502,
            detail='No response from Ziniao client',
        )

    status_code = str(result.get('statusCode', ''))
    # Surface Ziniao's own error message ("err" field) verbatim. The
    # statusCode is not 1:1 with a single failure mode — Ziniao reuses
    # -10003 for both "BOSS account hasn't enabled WebDriver" and
    # "wrong company name", and other codes have similar overlap. So
    # whatever Ziniao says, pass it through.
    if status_code != '0':
        err_msg = (result.get('err') or '').strip()
        if '新终端登录' in err_msg:
            # Ziniao's new-device security check. Not a WebDriver /
            # credentials problem — the user must approve the device by
            # logging into Ziniao manually once. Surface a structured
            # status so the UI shows a localized, actionable hint.
            raise HTTPException(
                status_code=502,
                detail=json.dumps({'status': 'new_terminal_login'}),
            )
        if status_code == '-10003':
            # Keep the structured "no_permission" status so the UI can
            # still show the "enable WebDriver" link, but include the
            # actual Chinese message so the user sees the real reason.
            raise HTTPException(
                status_code=502,
                detail=json.dumps(
                    {
                        'status': 'no_permission',
                        'message': err_msg,
                    },
                    ensure_ascii=False,
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=(
                f'Ziniao API error: {err_msg}'
                if err_msg
                else f'Ziniao API error (statusCode={status_code})'
            ),
        )

    # Ziniao API uses "browserList", some versions use "data"
    browsers = result.get('browserList', result.get('data', []))
    return [
        ZiniaoBrowserProfile(
            browser_name=b.get('browserName', b.get('name', 'Unknown')),
            browser_oauth=str(b.get('browserOauth', b.get('oauth', ''))),
        )
        for b in browsers
    ]


@router.post('/{account_id}/restart')
async def restart_ziniao(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kill Ziniao and relaunch in WebDriver mode.

    Mac / native Windows: kills and relaunches automatically.
    WSL: kills only (user must relaunch via .bat script).

    Safe because this is only callable from the Force Restart /
    Force Kill button, which appears only after the user tried
    closing Ziniao manually and Refresh still detected normal
    mode (so no active CDP sessions exist).
    """
    account = await db.get(ZiniaoAccount, account_id)
    if not account:
        raise HTTPException(
            status_code=404,
            detail='Ziniao account not found',
        )

    user_info = {
        'company': account.company,
        'username': account.username,
        'password': _decrypt_or_400(account.encrypted_password),
    }
    client_path = account.client_path or 'ziniao'

    # Safety check: only allow restart when in normal mode.
    # Prevents killing an active WebDriver session.
    status = await get_ziniao_status(account.socket_port, user_info)
    if status['status'] != 'running_normal':
        raise HTTPException(
            status_code=409,
            detail=(
                f'Cannot restart: Ziniao is in '
                f'"{status["status"]}" state, not '
                f'"running_normal"'
            ),
        )

    try:
        await kill_and_relaunch_ziniao(
            account.socket_port, client_path, user_info
        )
        return {'ok': True}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
