"""A task's own mailboxes: what the in-task email tools call.

``vibe_seller_email_info``, ``vibe_seller_send_email`` and
``vibe_seller_sync_email_now`` (``app/mcp_server.py``) land here. Scope
is resolved on the server, from the task, never from an id the agent
passes:
- a store task reads, sends from and syncs only its own store's
  mailboxes;
- a task with no store sees every mailbox.

What each mailbox can do is ``app.email.access``'s decision. A refusal
says why, and what the user changes where, so an agent stops instead of
hunting for another way.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.email.access import NO_MAILBOX, NO_STORE_MAILBOX, mailboxes_for
from app.email.info import mailbox_info, resolve_store
from app.email.sender import SendNotAllowedError, send_email
from app.models.email_account import EmailAccount
from app.models.task import Task
from app.models.user import User
from app.scheduler.email_sync import sync_now
from app.utils.crypto import decrypt_password

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


class _SendRequest(BaseModel):
    account_email: str
    to: str
    subject: str
    body: str
    body_html: str | None = None


class _SyncRequest(BaseModel):
    account_email: str


async def _task(db: AsyncSession, task_id: str) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Task not found')
    return task


async def _scope(db: AsyncSession, task: Task, store: str | None) -> str | None:
    """The store whose mailboxes this task may see (None: every one)."""
    if task.store_id:
        if store and (await resolve_store(db, store)).id != task.store_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "A store task uses only its own store's mailboxes; call "
                    'without store_id to get them.'
                ),
            )
        return task.store_id
    return (await resolve_store(db, store)).id if store else None


async def _own_mailbox(
    db: AsyncSession, task: Task, address: str
) -> EmailAccount:
    """The task's mailbox at *address*; 404 naming the ones it has."""
    mailboxes = await mailboxes_for(db, task.store_id)
    wanted = address.strip().lower()
    match = next((m for m in mailboxes if m.email.lower() == wanted), None)
    if match is None:
        if not mailboxes:
            detail = NO_STORE_MAILBOX if task.store_id else NO_MAILBOX
        else:
            whose = "this store's" if task.store_id else "Vibe Seller's"
            detail = (
                f'{address} is not one of {whose} mailboxes, so nothing was '
                'done. Use one of: '
                + ', '.join(m.email for m in mailboxes)
                + '.'
            )
        raise HTTPException(status_code=404, detail=detail)
    return await db.get(EmailAccount, match.account_id)


@router.get('/{task_id}/mailboxes')
async def get_task_mailboxes(
    task_id: str,
    store_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """The task's mailboxes, what each can do, and how to read them.

    ``store_id`` (id or slug) narrows a no-store task to one store; a
    store task may only name its own.
    """
    task = await _task(db, task_id)
    return await mailbox_info(db, await _scope(db, task, store_id))


@router.post('/{task_id}/mailboxes/send')
async def send_from_task_mailbox(
    task_id: str,
    data: _SendRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send from one of the task's mailboxes (403 when it cannot send)."""
    task = await _task(db, task_id)
    account = await _own_mailbox(db, task, data.account_email)
    try:
        result = await send_email(
            account=account,
            password=decrypt_password(account.encrypted_password),
            to=data.to,
            subject=data.subject,
            body=data.body,
            body_html=data.body_html,
        )
    except SendNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not result.get('ok'):
        raise HTTPException(
            status_code=502, detail=result.get('error', 'Send failed')
        )
    return result


@router.post('/{task_id}/mailboxes/sync')
async def sync_task_mailbox(
    task_id: str,
    data: _SyncRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Sync one of the task's mailboxes now (30 s cooldown)."""
    task = await _task(db, task_id)
    account = await _own_mailbox(db, task, data.account_email)
    try:
        return await sync_now(account)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f'Sync failed: {exc}. Tell the user: they fix the '
                "mailbox's password or server in Settings → Email."
            ),
        ) from exc
