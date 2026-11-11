"""Task file browser endpoints."""

import asyncio
from datetime import UTC, datetime
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.ai.stop_gates.ad_rules import resolve_rules
from app.auth import get_current_user
from app.browser.manager import store_slug
from app.database import get_db
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.task_states import TaskStatus
from app.text_utils import SafeStr
from app.uploads import ALLOWED_UPLOAD_EXTS, reject_disallowed_type, save_upload
from app.workspace.manager import VIBE_SELLER_DIR

router = APIRouter(prefix='/api/tasks', tags=['tasks'])

logger = logging.getLogger(__name__)

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


def _contained_result_file(raw: str, task_root: Path) -> Path | None:
    """The real file inside ``task_root`` that ``raw`` names, else None.

    One layer of matching quotes is stripped (agents hand over the path
    as the MCP arg showed it, ``'"./AD_AUDIT.md"'``), then both
    interpretations are tried and whichever lands on a real file inside
    ``task_root`` wins:

    * absolute path (the agent built it from cwd — e.g. a path echoed
      back by browser-use or the ``Write`` tool) — resolve as-is;
    * relative path (``./AD_AUDIT.md``, ``AD_AUDIT.md``) — join to
      ``task_root``.

    The earlier implementation only handled the relative case: it
    stripped a leading ``/`` from an absolute path and joined the
    remainder onto ``task_root``, producing a nonexistent nested path.
    An absolute path that *did* point at the report therefore fell
    through and the literal path string was stored as the result — so
    the UI showed a path instead of the rendered report.

    Shared by both pointer modes; it never judges what the string MEANS.
    """
    if not (isinstance(raw, str) and 0 < len(raw) <= 512 and '\n' not in raw):
        return None
    # Agents sometimes pass the path wrapped in literal quotes (the MCP
    # arg shows up as '"./AD_AUDIT.md"'). Strip one layer of matching
    # quotes + whitespace FIRST so the shape check sees the real value.
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1].strip()
    if Path(raw).is_absolute():
        candidate = Path(raw)
    else:
        # Strip a single leading `./`. NOT `lstrip('./')` — that treats
        # the argument as a *set* of characters and would eat things
        # like `.claude/...` down to `claude/...`.
        rel = raw[2:] if raw.startswith('./') else raw
        candidate = task_root / rel
    try:
        target = candidate.resolve()
        # `is_relative_to` is the canonical containment check — same as
        # the task-file endpoints above.
        if target.is_file() and target.is_relative_to(task_root):
            return target
    except (OSError, ValueError):
        return None
    return None


def resolve_declared_result_path(raw: str, task_root: Path) -> Path | None:
    """The file the agent DECLARED as its deliverable (``result_file``).

    The explicit half of the ``set_task_result`` contract: the agent says
    "my result is this file", so nothing has to be inferred from the
    string's shape and any name the workspace actually holds is valid —
    ``report``, ``out/summary``, ``./report.md`` alike. The same
    containment rules as the legacy pointer mode apply (a real file
    inside the task workspace, never a symlink escape); only the shape
    pre-check is skipped, because a declaration is not a guess.
    """
    return _contained_result_file(raw, task_root)


_DOC_EXTS = ('.md', '.txt', '.html', '.csv', '.tsv', '.json')


def resolve_workspace_result_path(raw: str, task_root: Path) -> Path | None:
    """LEGACY pointer mode: ``raw`` names an existing workspace file.

    Older agents and skills pass the report's PATH as ``result`` (see the
    tool description and ``review-collect``/``amazon-ads`` quickrefs),
    so a short path-like string that lands on a real file inside the task
    workspace is still read — this keeps them working until they move to
    ``result_file``. What it must NEVER do is refuse a value that does
    not resolve: whether a result is a pointer is the agent's to state,
    not the server's to guess from file extensions. A string that is not
    a workspace path, or names nothing that exists, is the result text.

    Returns None for anything that is not a short single-line path-like
    string or does not land on a real file inside ``task_root``. The
    shape pre-check below only decides whether to ATTEMPT a read.
    """
    if not (isinstance(raw, str) and 0 < len(raw) <= 512 and '\n' not in raw):
        return None
    probe = raw.strip()
    if len(probe) >= 2 and probe[0] == probe[-1] and probe[0] in ('"', "'"):
        probe = probe[1:-1].strip()
    # A pointer carries a path separator / ``./`` prefix, OR is a bare
    # document filename (e.g. ``AD_AUDIT_2026-06-10.md``). The
    # bare-filename case is what the ``./``-less calls in the skill docs
    # rely on. Anything else is direct content.
    if not (
        '/' in probe
        or '\\' in probe
        or probe.startswith('./')
        or probe.lower().endswith(_DOC_EXTS)
    ):
        return None
    return _contained_result_file(raw, task_root)


async def resolve_store_rules(db, store_id: str | None) -> dict | None:
    """Effective ad-rule thresholds for a task's store, or None.

    Defaults from ``ad_rules.DEFAULT_RULES`` overlaid with any override
    lines found in ``stores/<slug>/notes.md`` (e.g. ``scale_roas: 6``).
    Used by ``set_task_result`` so the ad gates honour per-store
    tuning.
    """
    if not store_id:
        return None
    store = await db.get(Store, store_id)
    if not store:
        return None
    slug = store_slug(store.name, store.id)
    notes_path = VIBE_SELLER_DIR / 'stores' / slug / 'notes.md'
    try:
        notes_text = await asyncio.to_thread(
            notes_path.read_text, encoding='utf-8'
        )
    except OSError:
        notes_text = None
    return resolve_rules(notes_text)


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
def list_task_files(
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """List agent-generated files in the task workspace.

    A plain ``def`` route: the work is a directory walk, which FastAPI
    runs in the threadpool instead of blocking the event loop (every
    SSE stream with it).
    """
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
def download_task_files_zip(
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """Download all task files as a single ZIP archive.

    A plain ``def`` route: zipping the workspace is blocking I/O, run
    in the threadpool so the event loop keeps serving while a large
    task workspace is packed.
    """
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


# Chat attachments stage OUTSIDE the task workspace so the agent's cwd
# (``tasks/<id>/``) never sees them via ``ls``/``find`` before Send. The
# invariant: a user attachment becomes agent-visible ONLY when the user
# sends the message that carries it. ``promote_staged_attachments`` (call
# it from the /messages endpoint) is the single moment that happens.
_CHAT_STAGING_DIR = VIBE_SELLER_DIR / 'chat_staging'
_STAGING_ID_RE = re.compile(r'^[0-9a-f]{32}$')
# Staged-but-never-sent files are swept on the next upload for the task.
_STAGING_TTL_SECONDS = 24 * 3600


class StagedAttachment(BaseModel):
    """A chat file uploaded to staging, not yet visible to the agent."""

    id: str
    filename: str
    content_type: str
    # Preview URL for the send-bar chip / transcript thumbnail. NOT an
    # absolute path — the path is withheld from the client by design.
    url: str


def _normalized_upload_name(file: UploadFile) -> str:
    """ASCII-safe, cross-platform filename for a stored upload.

    Strips everything outside ``[A-Za-z0-9._-]`` — which also removes
    every character Windows forbids in a filename (``<>:"/\\|?*`` and
    control chars) and non-ASCII (e.g. Chinese) names that break the
    serve URL and browser-use path handling. So a Chinese-named upload
    still lands as a valid ASCII path on macOS, Linux, and Windows.
    """
    raw = Path(file.filename or 'upload').name
    stem = re.sub(r'[^A-Za-z0-9._-]', '_', Path(raw).stem) or 'upload'
    ext = Path(raw).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        ext = mimetypes.guess_extension(file.content_type or '') or '.bin'
    return f'{stem}{ext}'


def _staging_task_dir(task_id: str) -> Path:
    """Resolve a task's staging dir, guarding traversal via task_id."""
    base = _CHAT_STAGING_DIR.resolve()
    d = (_CHAT_STAGING_DIR / task_id).resolve()
    if not d.is_relative_to(base):
        raise HTTPException(status_code=400, detail='Invalid task id')
    return d


def _staging_item_dir(task_id: str, staged_id: str) -> Path:
    """Resolve one staged item's dir; reject ids that aren't hex uuids."""
    if not _STAGING_ID_RE.match(staged_id):
        raise HTTPException(status_code=400, detail='Invalid attachment id')
    base = _staging_task_dir(task_id)
    d = (base / staged_id).resolve()
    if not d.is_relative_to(base):
        raise HTTPException(status_code=400, detail='Invalid attachment id')
    return d


def _sweep_stale_staging(task_staging: Path) -> None:
    """Best-effort removal of staged items older than the TTL."""
    if not task_staging.is_dir():
        return
    cutoff = datetime.now(UTC).timestamp() - _STAGING_TTL_SECONDS
    for child in task_staging.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


def _staged_file(item_dir: Path) -> Path | None:
    """The single stored file inside a staged item dir, if present."""
    if not item_dir.is_dir():
        return None
    files = [p for p in item_dir.iterdir() if p.is_file()]
    return files[0] if files else None


def promote_staged_attachments(
    task_id: str, staged_ids: list[str]
) -> list[dict]:
    """Move staged chat files into the task ``uploads/`` (the agent's cwd).

    Called at SEND time only — this is the ONE moment a chat attachment
    becomes visible to the agent. Returns one dict per promoted file with
    ``abs_path`` (for the agent to Read — images are vision input),
    ``url`` (for the UI thumbnail) and ``filename``. Unknown, malformed,
    or already-consumed ids are skipped rather than erroring, so a stale
    client id can't fail an otherwise-valid send.
    """
    task_dir = _validate_task_id(task_id)
    out_dir = task_dir / 'uploads'
    promoted: list[dict] = []
    for sid in staged_ids:
        try:
            item = _staging_item_dir(task_id, sid)
        except HTTPException:
            continue
        src = _staged_file(item)
        if src is None:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / src.name
        if dest.exists():
            dest = out_dir / f'{src.stem}-{uuid.uuid4().hex[:6]}{src.suffix}'
        shutil.move(str(src), str(dest))
        shutil.rmtree(item, ignore_errors=True)
        promoted.append({
            'abs_path': str(dest),
            'filename': dest.name,
            'url': f'/api/tasks/{task_id}/files/uploads/{dest.name}',
        })
    return promoted


@router.post('/{task_id}/staged')
async def stage_task_file(
    task_id: str,
    file: UploadFile,
    _user: User = Depends(get_current_user),
) -> StagedAttachment:
    """Stage a chat attachment WITHOUT exposing it to the agent yet.

    The file lands under ``chat_staging/<task_id>/<id>/`` — a sibling of
    the task workspace, never in the agent's cwd — so a running agent
    can't pick it up by scanning its directory. It only reaches the agent
    when the user sends a message referencing it (see
    ``promote_staged_attachments``). Returns a preview URL, never a path.
    """
    reject_disallowed_type(file)
    base = _staging_task_dir(task_id)
    base.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_sweep_stale_staging, base)
    staged_id = uuid.uuid4().hex
    item = base / staged_id
    item.mkdir()
    name = _normalized_upload_name(file)
    try:
        await save_upload(file, item / name)
    except BaseException:
        # A refused upload must not leave an empty staging dir behind
        # for _sweep_stale_staging to trip over later.
        await asyncio.to_thread(shutil.rmtree, item, ignore_errors=True)
        raise
    return StagedAttachment(
        id=staged_id,
        filename=name,
        content_type=file.content_type or 'application/octet-stream',
        url=f'/api/tasks/{task_id}/staged/{staged_id}',
    )


@router.get('/{task_id}/staged/{staged_id}')
async def get_staged_file(
    task_id: str,
    staged_id: str,
    _user: User = Depends(get_current_user),
):
    """Serve a staged attachment for the send-bar chip / thumbnail."""
    resolved = _staged_file(_staging_item_dir(task_id, staged_id))
    if resolved is None:
        raise HTTPException(status_code=404, detail='Attachment not found')
    mime, _ = mimetypes.guess_type(resolved.name)
    return FileResponse(
        path=str(resolved),
        filename=resolved.name,
        media_type=mime or 'application/octet-stream',
    )


@router.delete('/{task_id}/staged/{staged_id}')
def delete_staged_file(
    task_id: str,
    staged_id: str,
    _user: User = Depends(get_current_user),
):
    """Discard a staged attachment (the send-bar chip's ✕ button).

    A plain ``def`` route: one directory removal, run in the
    threadpool.
    """
    shutil.rmtree(_staging_item_dir(task_id, staged_id), ignore_errors=True)
    return {'ok': True}


def record_agent_error(task, error_text: str) -> dict | None:
    """Apply the set_task_error contract to *task* (mutates, no commit).

    ``set_task_error`` means UNRECOVERABLE failure. A task that already
    produced a valid result this turn is not that — under gate pressure
    agents were observed using the error channel as an "explain my
    remaining caveats so I may end" escape hatch, and a non-empty
    ``task.error`` deterministically flips the task FAILED at cleanup,
    hiding a complete deliverable behind a failure badge.

    With a non-empty ``task.result``: the text is appended to the
    result as a caveat block, ``task.error`` stays untouched, and a
    payload is returned for the endpoint to send back so the agent
    knows not to retry the error channel. Without a result: classic
    behavior (``task.error`` set, returns None).
    """
    if task.result and task.result.strip():
        caveat = error_text.strip()
        block = '\n\n> ⚠️ **Agent-reported caveats**\n> ' + caveat.replace(
            '\n', '\n> '
        )
        task.result = f'{task.result}{block}'
        # ``result`` is a DERIVED view (see app/task_outcome.py) and is
        # rebuilt from ``accepted_result`` at finalize — append there
        # too, or the caveat is silently dropped when the outcome is
        # next resolved.
        if getattr(task, 'accepted_result', None):
            task.accepted_result = f'{task.accepted_result}{block}'
        return {
            'status': task.status,
            'recorded_as': 'caveat',
            'note': (
                'A valid result already exists for this turn, so this '
                'was appended to it as a caveat — the task will NOT be '
                'marked failed. set_task_error is only for tasks with '
                'no usable deliverable.'
            ),
        }
    task.error = error_text
    task.error_category = task.error_category or 'agent_reported'
    return None


class SetTaskErrorRequest(BaseModel):
    error: SafeStr


@router.post('/{task_id}/error')
async def set_task_error(
    task_id: str,
    body: SetTaskErrorRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Record an unrecoverable error (`vibe_seller_set_task_error`).

    Saves `task.error` (+category) without transitioning status —
    cleanup marks the task FAILED on a non-empty error. When a valid
    result already exists this turn, the text is recorded as a CAVEAT
    on the result instead (see ``record_agent_error``) and the task
    completes normally.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status not in {TaskStatus.RUNNING, TaskStatus.DESIGNING}:
        raise HTTPException(
            status_code=400,
            detail=(f'Cannot set error on task in status {task.status}'),
        )
    # Caveat-vs-error contract lives in ``record_agent_error`` (see
    # tasks_files): with a valid result already on the task, the text
    # becomes a caveat on the result and the task will NOT fail.
    payload = record_agent_error(task, body.error)
    task.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    if payload is not None:
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': task.status, 'error': None},
        )
        return {'ok': True, 'task_id': task_id, **payload}
    # Emit status=task.status (not FAILED) so the UI doesn't
    # flip the badge prematurely. The FAILED transition lands
    # later via auto_run_task cleanup and re-emits task_update.
    await event_bus.emit(
        'task_update',
        {
            'task_id': task_id,
            'status': task.status,
            'error': task.error,
        },
    )
    return {'ok': True, 'task_id': task_id, 'status': task.status}
