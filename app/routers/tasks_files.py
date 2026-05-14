"""Task file browser endpoints."""

from datetime import UTC, datetime
import mimetypes
import os
from pathlib import Path
import tempfile
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.auth import get_current_user
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

router = APIRouter(prefix='/api/tasks', tags=['tasks'])

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'

_SKIP_NAMES = {'.claude', '.mcp.json', 'CLAUDE.md'}


def _validate_task_id(task_id: str) -> Path:
    """Resolve task dir and guard against traversal via task_id."""
    task_dir = (_TASKS_DIR / task_id).resolve()
    if not task_dir.is_relative_to(_TASKS_DIR.resolve()):
        raise HTTPException(status_code=400, detail='Invalid task id')
    return task_dir


def _safe_task_file(task_id: str, filename: str) -> Path:
    """Resolve and validate a file path in the task dir.

    Rejects dotfiles, infra files (.claude, .mcp.json, CLAUDE.md),
    and symlinks to match the list/zip endpoints' exclusions.
    """
    task_dir = _validate_task_id(task_id)
    if not task_dir.is_dir():
        raise HTTPException(status_code=404, detail='Task directory not found')
    resolved = (task_dir / filename).resolve()
    if not resolved.is_relative_to(task_dir):
        raise HTTPException(status_code=400, detail='Invalid filename')
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail='File not found')
    # Reject symlinks and files inside excluded dirs/names
    rel_parts = resolved.relative_to(task_dir).parts
    for part in rel_parts:
        if part.startswith('.') or part in _SKIP_NAMES:
            raise HTTPException(status_code=404, detail='File not found')
    if (task_dir / rel_parts[0]).is_symlink():
        raise HTTPException(status_code=404, detail='File not found')
    return resolved


def _walk_task_files(task_dir: Path):
    """Yield ``(absolute_path, relative_posix)`` for user files."""
    for root, dirs, filenames in os.walk(task_dir):
        root_path = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith('.')
            and d not in _SKIP_NAMES
            and not (root_path / d).is_symlink()
        ]
        dirs.sort()
        for fname in sorted(filenames):
            if fname.startswith('.') or fname in _SKIP_NAMES:
                continue
            fpath = root_path / fname
            if fpath.is_symlink():
                continue
            yield fpath, fpath.relative_to(task_dir).as_posix()


@router.get('/{task_id}/files')
async def list_task_files(
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """List agent-generated files in the task workspace."""
    task_dir = _validate_task_id(task_id)
    if not task_dir.is_dir():
        return []

    files = []
    for fpath, rel in _walk_task_files(task_dir):
        try:
            stat = fpath.stat()
        except OSError:
            continue
        mime, _ = mimetypes.guess_type(fpath.name)
        files.append({
            'name': rel,
            'size': stat.st_size,
            'type': mime or 'application/octet-stream',
            'modified_at': datetime.fromtimestamp(
                stat.st_mtime, tz=UTC
            ).isoformat(),
        })
    return files


@router.get('/{task_id}/files-zip')
async def download_task_files_zip(
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """Download all task files as a single ZIP archive."""
    task_dir = _validate_task_id(task_id)
    if not task_dir.is_dir():
        raise HTTPException(status_code=404, detail='Task directory not found')

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fpath, rel in _walk_task_files(task_dir):
                zf.write(fpath, arcname=rel)
    except Exception:
        os.unlink(tmp_path)
        raise

    return FileResponse(
        path=tmp_path,
        filename=f'{task_id[:8]}_files.zip',
        media_type='application/zip',
        background=BackgroundTask(os.unlink, tmp_path),
    )


@router.get('/{task_id}/files/{filename:path}')
async def download_task_file(
    task_id: str,
    filename: str,
    _user: User = Depends(get_current_user),
):
    """Download a file from the task workspace."""
    resolved = _safe_task_file(task_id, filename)
    mime, _ = mimetypes.guess_type(resolved.name)
    return FileResponse(
        path=str(resolved),
        filename=resolved.name,
        media_type=mime or 'application/octet-stream',
    )
