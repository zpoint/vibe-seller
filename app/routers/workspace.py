import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.models.user import User
from app.schemas.workspace import (
    FileResetRequest,
    FileWriteRequest,
    SkillCreateRequest,
    SkillSaveRequest,
    StoreProfileCreateRequest,
)
from app.workspace.catalog_validation import reject_wrong_stubs
from app.workspace.knowledge_sync import knowledge_sync
from app.workspace.manager import workspace_manager
from app.workspace.skills_sync import skills_sync

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


@router.put('/file')
async def write_file(
    path: str = Query(...),
    body: FileWriteRequest = ...,
    _user: User = Depends(get_current_user),
):
    try:
        # Enforce the catalog stub contract at the write boundary: a
        # CATALOG.md that marks a substantive file "Empty/stub" is
        # rejected so the sync agent must summarize it (catalog-first
        # navigation depends on real summaries). See catalog_validation.
        if path.endswith('CATALOG.md'):
            reject_wrong_stubs(body.content, workspace_manager.resolve_file)
        await workspace_manager.write_file(path, body.content)
        return {'path': path, 'status': 'ok'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

    Built-in slugs are rejected by the manager (they are read-only).
    """
    try:
        return await workspace_manager.save_skill(
            slug, body.skill_md, body.files or None
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


@router.post('/knowledge/sync')
async def sync_knowledge(_user: User = Depends(get_current_user)):
    """Fetch knowledge updates from remote GitHub."""
    return await knowledge_sync.fetch_remote()


@router.get('/knowledge/sync-status')
async def knowledge_sync_status(_user: User = Depends(get_current_user)):
    """Get knowledge sync status between project and workspace."""
    return knowledge_sync.get_sync_status()


@router.get('/knowledge/sync-meta')
async def knowledge_sync_meta(_user: User = Depends(get_current_user)):
    """Get sync metadata (last sync time, commit, errors)."""
    return knowledge_sync.get_sync_meta()


@router.post('/skills/sync')
async def sync_skills(_user: User = Depends(get_current_user)):
    """Sync built-in skills to local workspace + fetch remote."""
    result = await skills_sync.fetch()
    remote = await skills_sync.fetch_remote()
    return {**result, 'remote': remote}


@router.get('/skills/sync-meta')
async def skills_sync_meta(_user: User = Depends(get_current_user)):
    """Get skills sync metadata."""
    return skills_sync.get_sync_meta()
