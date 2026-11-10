import asyncio
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import knowledge_contract
from app.auth import get_current_user
from app.browser.manager import store_slug
from app.browser.wrapper import validate_store_slug
from app.database import get_db
from app.models.schedule import Schedule
from app.models.schedule_constants import StalenessCheck
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.schemas.workspace import (
    DeleteLinesRequest,
    FileResetRequest,
    FileWriteRequest,
    SkillCreateRequest,
    SkillSaveRequest,
    StoreProfileCreateRequest,
)
from app.workspace.catalog_validation import validate_catalog_write
from app.workspace.knowledge_sync import knowledge_sync
from app.workspace.manager import workspace_manager

router = APIRouter(prefix='/api/workspace', tags=['workspace'])


@router.get('/tree')
async def list_tree(_user: User = Depends(get_current_user)):
    await workspace_manager.ensure_init()
    return await workspace_manager.list_tree()


@router.get('/structured')
async def get_structured(_user: User = Depends(get_current_user)):
    """Return workspace data grouped by section for the UI."""
    await workspace_manager.ensure_init()
    return await workspace_manager.get_structured()


@router.get('/file')
async def read_file(
    path: str = Query(...), _user: User = Depends(get_current_user)
):
    try:
        content = await workspace_manager.read_file(path)
        return {'path': path, 'content': content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'File not found: {path}')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Inline rendering is restricted to types that can't script under the
# app origin (NOT text/html, NOT image/svg+xml) — anything else is a
# stored-XSS vector via agent/merchant-written workspace files and is
# served as an attachment instead.
_INLINE_SAFE_TYPES = {
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
}


@router.get('/file/raw')
async def read_file_raw(
    path: str = Query(...), _user: User = Depends(get_current_user)
):
    """Raw bytes with a guessed content type — for binary viewers
    (xlsx/pdf/zip) and text the JSON endpoint can't decode."""
    try:
        file_path = workspace_manager.resolve_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'File not found: {path}')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    media_type, _ = mimetypes.guess_type(file_path.name)
    media_type = media_type or 'application/octet-stream'
    disposition = 'inline' if media_type in _INLINE_SAFE_TYPES else 'attachment'
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=file_path.name,
        content_disposition_type=disposition,
    )


async def _cleanup_write_error(
    db: AsyncSession, task_id: str, path: str, content: str
) -> str | None:
    """Hold a knowledge-cleanup run to deleting lines in its own tier.

    A catalog-sync task whose scope still owes the one-time cleanup
    (app/knowledge_contract.py) may rewrite notes — but only its own
    tier's, and only to remove outdated passages, never to add or
    reword. Any other writer is unaffected.
    """
    task = await db.get(Task, task_id)
    if task is None or not task.schedule_id:
        return None
    schedule = await db.get(Schedule, task.schedule_id)
    if not schedule or schedule.staleness_check != StalenessCheck.CATALOG:
        return None
    if not await knowledge_contract.needs_cleanup(
        db, knowledge_contract.cleanup_scope(task.store_id)
    ):
        return None
    slug = None
    if task.store_id is not None:
        store = await db.get(Store, task.store_id)
        slug = store_slug(store.name, store.id) if store else ''
    err = knowledge_contract.tier_error(path, slug)
    if err:
        return err
    try:
        old = await workspace_manager.read_file(path)
    except (FileNotFoundError, ValueError):
        return (
            'This one-time cleanup only removes passages from existing '
            f'files; {path} does not exist.'
        )
    return knowledge_contract.deletion_only_error(old, content)


@router.put('/file')
async def write_file(
    path: str = Query(...),
    body: FileWriteRequest = ...,
    task_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        if task_id and not path.endswith('CATALOG.md'):
            err = await _cleanup_write_error(db, task_id, path, body.content)
            if err:
                raise ValueError(err)
        # Enforce the catalog contract at the write boundary: a tier
        # CATALOG.md that drops a file, breaks the table format,
        # carries another store's rows, or stubs a substantive file is
        # rejected naming the rows to fix. See catalog_validation.
        if path.endswith('CATALOG.md'):
            await asyncio.to_thread(
                validate_catalog_write,
                path,
                body.content,
                root=workspace_manager.root,
                resolve_file=workspace_manager.resolve_file,
            )
        await workspace_manager.write_file(path, body.content)
        return {'path': path, 'status': 'ok'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/file/delete-lines')
async def delete_lines(
    path: str = Query(...),
    body: DeleteLinesRequest = ...,
    task_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Remove whole lines from a file without the caller rewriting it.

    A note cleanup only drops passages; making the model retype a long
    file to do that slips (see app/mcp_workspace_tool_schemas.py). The
    server deletes the named 1-based inclusive ranges itself, and only
    from the file as the caller read it.
    """
    try:
        old = await workspace_manager.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'Not found: {path}')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    lines = old.split('\n')
    if old.endswith('\n'):
        lines = lines[:-1]
    if body.line_count != len(lines):
        raise HTTPException(
            status_code=409,
            detail=(
                f'{path} has {len(lines)} lines now, not {body.line_count}; '
                're-read it and give the ranges again.'
            ),
        )
    drop: set[int] = set()
    for first, last in body.ranges:
        if not 1 <= first <= last <= len(lines):
            raise HTTPException(
                status_code=400,
                detail=(
                    f'range [{first}, {last}] is outside lines 1-{len(lines)}'
                ),
            )
        drop.update(range(first, last + 1))
    kept = [ln for i, ln in enumerate(lines, 1) if i not in drop]
    new = '\n'.join(kept) + ('\n' if kept and old.endswith('\n') else '')
    if task_id:
        err = await _cleanup_write_error(db, task_id, path, new)
        if err:
            raise HTTPException(status_code=400, detail=err)
    await workspace_manager.write_file(path, new)
    return {'path': path, 'status': 'ok', 'deleted_lines': len(drop)}


@router.delete('/file')
async def delete_file(
    path: str = Query(...), _user: User = Depends(get_current_user)
):
    try:
        await workspace_manager.delete_file(path)
        return {'path': path, 'status': 'deleted'}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'Not found: {path}')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/skills')
async def list_skills(_user: User = Depends(get_current_user)):
    """List skills as {slug, name, description, source, updatable}.

    Used by the save-skill flow to decide whether to extend an
    existing user-space skill or create a new one.
    """
    await workspace_manager.ensure_init()
    return await workspace_manager.list_skills()


@router.put('/skills/{slug}')
async def save_skill(
    slug: str,
    body: SkillSaveRequest,
    _user: User = Depends(get_current_user),
):
    """Create or extend a user-space skill (upsert).

    One call writes the whole skill — SKILL.md, text ``files``,
    base64 ``files_bin``, and the ``executables`` bit list.
    ``replace=True`` installs the tree wholesale (import semantics)
    and refuses to clobber an existing user skill without
    ``overwrite=True``; the default merges, keeping files the call
    does not carry. Built-in slugs are rejected by the manager (they
    are read-only).
    """
    try:
        return await workspace_manager.save_skill(
            slug,
            body.skill_md,
            body.files or None,
            body.files_bin or None,
            body.executables or None,
            body.origin_url,
            body.replace,
            body.overwrite,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/skill')
async def create_skill(
    body: SkillCreateRequest, _user: User = Depends(get_current_user)
):
    if body.name.startswith('_'):
        raise HTTPException(
            status_code=400,
            detail=f'Skill names starting with _ are reserved: {body.name}',
        )
    try:
        rel_path = await workspace_manager.create_skill(
            body.name,
            body.description,
            origin_url=body.origin_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'path': rel_path, 'status': 'created'}


@router.delete('/skills/{slug}')
async def delete_skill(
    slug: str,
    _user: User = Depends(get_current_user),
):
    if slug.startswith('_'):
        raise HTTPException(
            status_code=400,
            detail='Cannot delete built-in skills',
        )
    try:
        await workspace_manager.delete_skill(slug)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f'Skill not found: {slug}',
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'slug': slug, 'status': 'deleted'}


@router.post('/store-profile')
async def create_store_profile(
    body: StoreProfileCreateRequest, _user: User = Depends(get_current_user)
):
    try:
        validate_store_slug(body.slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    rel_path = await workspace_manager.create_store_profile(
        body.slug,
        body.name,
        body.platform,
        body.country,
        body.backend,
    )
    return {'path': rel_path, 'status': 'created'}


@router.get('/file/history')
async def file_history(
    path: str = Query(...),
    max_count: int = Query(50),
    _user: User = Depends(get_current_user),
):
    """Get git commit history for a workspace file."""
    try:
        history = await workspace_manager.file_history(path, max_count)
        return {'path': path, 'commits': history}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'File not found: {path}')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/file/version')
async def file_version(
    path: str = Query(...),
    commit: str = Query(...),
    _user: User = Depends(get_current_user),
):
    """Get file content at a specific git commit."""
    try:
        content = await workspace_manager.file_at_commit(path, commit)
        return {'path': path, 'commit': commit, 'content': content}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/file/reset')
async def reset_file(
    body: FileResetRequest,
    _user: User = Depends(get_current_user),
):
    """Reset a file to a specific git commit version."""
    try:
        await workspace_manager.reset_file_to_commit(body.path, body.commit)
        return {'path': body.path, 'commit': body.commit, 'status': 'ok'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/knowledge/sync-status')
async def knowledge_sync_status(_user: User = Depends(get_current_user)):
    """Compare the packaged knowledge tree with the workspace copy.

    Content status, not schedule status: "is what is on disk what this
    release ships?". WHEN a sync last ran, and what upstream version it
    holds, is GET /api/sync/status.
    """
    return knowledge_sync.get_sync_status()
