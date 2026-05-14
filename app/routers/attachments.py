from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import ATTACHMENTS_DIR
from app.database import get_db
from app.models.task import Task
from app.models.task_attachment import TaskAttachment
from app.models.user import User

router = APIRouter(prefix='/api/attachments', tags=['attachments'])

ALLOWED_TYPES = {
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'application/pdf',
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post('/{task_id}')
async def upload_attachment(
    task_id: str,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f'File type not allowed: {file.content_type}',
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail='File too large (max 10MB)')

    attachment_id = str(uuid.uuid4())
    ext = Path(file.filename or 'file').suffix or '.bin'
    file_path = ATTACHMENTS_DIR / f'{attachment_id}{ext}'
    file_path.write_bytes(content)

    attachment = TaskAttachment(
        id=attachment_id,
        task_id=task_id,
        file_name=file.filename or 'untitled',
        file_path=str(file_path),
        file_type=file.content_type or 'application/octet-stream',
        file_size=len(content),
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)

    return {
        'id': attachment.id,
        'task_id': attachment.task_id,
        'file_name': attachment.file_name,
        'file_type': attachment.file_type,
        'file_size': attachment.file_size,
        'created_at': attachment.created_at,
    }


@router.get('/{task_id}')
async def list_attachments(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TaskAttachment).where(TaskAttachment.task_id == task_id)
    )
    attachments = result.scalars().all()
    return [
        {
            'id': a.id,
            'task_id': a.task_id,
            'file_name': a.file_name,
            'file_type': a.file_type,
            'file_size': a.file_size,
            'created_at': a.created_at,
        }
        for a in attachments
    ]


@router.get('/file/{attachment_id}')
async def download_attachment(
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    attachment = await db.get(TaskAttachment, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail='Attachment not found')

    file_path = Path(attachment.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found on disk')

    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.file_type,
    )
