import asyncio
from datetime import UTC, datetime
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import get_current_user
from app.browser.manager import browser_manager
from app.browser.ziniao_startup import (
    ZiniaoAccountConflictError,
    ZiniaoStartError,
    ZiniaoStartingError,
    ensure_ziniao_running,
)
from app.browser.ziniao_utils import is_ziniao_installed_mac, try_connect_ziniao
from app.database import get_db
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.platform import IS_MAC
from app.schemas.ziniao_account import (
    ZiniaoAccountCreate,
    ZiniaoAccountResponse,
    ZiniaoAccountUpdate,
    ZiniaoBrowserProfile,
)
from app.telemetry_events import BrowserFailureReason, TelemetryEvent
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/ziniao-accounts', tags=['ziniao-accounts'])

# How long one store-setup request waits on a Ziniao startup before
# answering 503 "starting" (the page then asks again). Well inside any
# proxy idle limit and the page's own request deadline.
_SETUP_WAIT_S = 45.0


async def _failure_status(e: ZiniaoStartError) -> str:
    """The structured status the store-setup page renders."""
    if e.reason == BrowserFailureReason.WSL_NOT_RUNNING:
        return 'not_running'  # the page offers the .bat on WSL
    if (
        IS_MAC
        and e.reason == BrowserFailureReason.LAUNCH_FAILED
        and not await asyncio.to_thread(is_ziniao_installed_mac)
    ):
        return 'not_installed'
    return 'start_failed'


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

    Launches Ziniao in WebDriver mode first when it is not answering
    (killing a normal-mode or leftover client) — no prompt.
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

    # Bring Ziniao up — killed and relaunched in WebDriver mode when it is
    # not answering; the user is never asked. The page polls: past
    # _SETUP_WAIT_S this answers 503 "starting" and the next request
    # joins the same startup. A click is a user retry, so a recent
    # startup failure does not hold it back (retry_failed).
    try:
        browser_manager.refuse_other_ziniao_account(
            account.id, f'Ziniao account "{account.name}"'
        )
        await db.commit()  # don't hold a pooled connection while waiting
        await ensure_ziniao_running(
            account.socket_port,
            client_path,
            user_info,
            wait_s=_SETUP_WAIT_S,
            live=browser_manager.has_live_ziniao(),
            retry_failed=True,
        )
    except ZiniaoStartingError as e:
        raise HTTPException(
            status_code=503,
            detail=json.dumps({
                'status': 'starting',
                'elapsed_s': round(e.elapsed_s),
            }),
        ) from e
    except ZiniaoAccountConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=json.dumps(
                {'status': 'account_conflict', 'message': str(e)},
                ensure_ascii=False,
            ),
        ) from e
    except ZiniaoStartError as e:
        logger.error('Ziniao start failed: %s', e)
        raise HTTPException(
            status_code=502,
            detail=json.dumps(
                {'status': await _failure_status(e), 'message': str(e)},
                ensure_ascii=False,
            ),
        ) from e

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
    # Default: surface Ziniao's own error message ("err" field)
    # verbatim. The statusCode is not 1:1 with a single failure mode —
    # Ziniao reuses -10003 for both "BOSS account hasn't enabled
    # WebDriver" and "wrong company name", and other codes have similar
    # overlap. So whatever Ziniao says, pass it through — EXCEPT for a
    # few signatures below (new-device login check) that we deliberately
    # map to structured statuses so the UI can show a localized,
    # actionable hint instead of a raw Chinese string. Keep those
    # mappings when refactoring.
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
