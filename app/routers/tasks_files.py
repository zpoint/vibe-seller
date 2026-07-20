"""Task file browser endpoints."""

import asyncio
from datetime import UTC, datetime
import logging
import mimetypes
import os
from pathlib import Path
import tempfile
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.ai.claude_backend_manager import agent_manager
from app.ai.skill_review import skills_requiring_review
from app.ai.stop_gates import (
    record_attempt,
    recorded_skills,
    report_reviewer,
)
from app.ai.stop_gates.ad_rules import resolve_rules
from app.auth import get_current_user
from app.browser.manager import store_slug
from app.models.store import Store
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

router = APIRouter(prefix='/api/tasks', tags=['tasks'])

logger = logging.getLogger(__name__)

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'


def apply_report_reviewer_gate(task_id, task_root, final_result):
    """Reviewer sign-off for review-declaring skills at ``set_task_result``.

    The live session's per-file verdict-authorship map (who wrote each
    review file this turn) is read from ``agent_manager``; an accepting
    verdict then counts only when the reviewer subagent itself wrote
    the file.

    The active reviewer is enforced here (not only in the Stop hook) so a
    backend that finishes via this endpoint can't complete with the
    reviewer never spawned. Fires when the task bound EITHER an ads skill
    OR any skill declaring a ``review:`` block in its SKILL.md (Phase 2:
    listing / reports / invoice / fbn / review-collect). The reviewer
    itself decides whether there was real work to verify or nothing to
    review (it signs off fast on a lookup); the server never pre-judges.

    Returns ``(deny_reason, final_result)``. A non-None ``deny_reason``
    means the caller should 400. On a bounded stall the reviewer fails
    open: ``deny_reason`` is None but ``final_result`` is banner-marked
    UNVERIFIED — never a silent "done".
    """
    skills = recorded_skills(task_id)
    needs_review = bool(skills & report_reviewer.AD_SKILLS) or bool(
        skills_requiring_review(skills, task_root)
    )
    if not needs_review:
        return None, final_result
    session = agent_manager.get_session(task_id)
    deny = report_reviewer.reviewer_verdict(
        task_root,
        review_writers=getattr(session, '_review_file_writers', None),
    )
    if not deny:
        return None, final_result
    attempt = record_attempt(task_id, 'ads_report_reviewer')
    if attempt <= report_reviewer.REVIEWER_STALL_CAP:
        return deny, final_result
    logger.warning(
        'Reviewer stalled for task %s after %d denials — accepting result '
        'as UNVERIFIED. Last reason: %s',
        task_id,
        attempt,
        deny[:200],
    )
    return None, report_reviewer.partial_banner() + final_result


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


def resolve_workspace_result_path(raw: str, task_root: Path) -> Path | None:
    """Return the file ``raw`` points to inside ``task_root``, else None.

    File-pointer mode for ``set_task_result``: the agent composes a long
    report with the built-in ``Write`` tool (streamed, fast) and passes
    only its path to ``set_task_result`` — avoiding the multi-minute
    stalls some providers hit when packing a 25KB result into a single
    MCP tool input. This resolves that path so the *content* is stored
    (and rendered inline by the UI) rather than the literal path string.

    Only short, single-line strings that look like a path are
    considered: either slash-bearing / ``./``-prefixed, OR a bare
    document filename (e.g. ``AD_AUDIT_2026-06-10.md``) — the
    bare-filename case is intentional and must stay consistent with
    ``looks_like_result_path`` (see the shape pre-check below). Any
    other value is direct content and returns None. Both interpretations
    are tried and whichever lands on a real file inside ``task_root``
    wins:

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
    """
    if not (isinstance(raw, str) and 0 < len(raw) <= 512 and '\n' not in raw):
        return None
    # Agents sometimes pass the path wrapped in literal quotes (the MCP
    # arg shows up as '"./AD_AUDIT.md"'). Strip one layer of matching
    # quotes + whitespace FIRST so the shape check sees the real value.
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1].strip()
    # Shape pre-check: a pointer carries a path separator / ``./`` prefix,
    # OR is a bare document filename (e.g. ``AD_AUDIT_2026-06-10.md``).
    # The bare-filename case MUST be accepted to stay consistent with
    # ``looks_like_result_path`` (which flags a bare ``*.md`` as a
    # pointer). Without it, a bare filename that DOES exist in task_root
    # resolved to None here yet was treated as a dangling pointer there,
    # so ``set_task_result`` 400'd a perfectly valid report. Anything
    # else is direct content.
    if not (
        '/' in raw
        or '\\' in raw
        or raw.startswith('./')
        or raw.lower().endswith(_DOC_EXTS)
    ):
        return None
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


async def resolve_store_rules(db, store_id: str | None) -> dict | None:
    """Effective ad-rule thresholds for a task's store, or None.

    Defaults from ``ad_rules.DEFAULT_RULES`` overlaid with any override
    lines found in ``stores/<slug>/notes.md`` (e.g. ``scale_roas: 6``).
    Used by ``set_task_result`` so the completeness reviewer and the
    folded-in bid-rule gates honor per-store tuning.
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


_DOC_EXTS = ('.md', '.txt', '.html', '.csv', '.tsv', '.json')


def looks_like_result_path(raw: str) -> bool:
    """True when ``raw`` is unmistakably a file POINTER, not content.

    Used by ``set_task_result`` to REJECT a dangling pointer (path-like
    value whose file doesn't exist) instead of falling through to
    direct-content mode. Without this, a malformed pointer (e.g. the
    path wrapped in quotes before quote-stripping existed, or a typo'd
    filename) sails through every content gate vacuously — a 26-char
    string has no ad sections to check — and the task "completes" with
    a useless literal path as its result.

    Deliberately stricter than the resolver's shape pre-check so real
    one-line content containing a slash (e.g. "ACOS 30%/ROAS 3.33") is
    never rejected: a pointer has no spaces and either carries a known
    document extension or starts with ``./`` / ``/``.
    """
    if not (isinstance(raw, str) and 0 < len(raw) <= 512 and '\n' not in raw):
        return False
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    if not s or ' ' in s:
        return False
    return s.lower().endswith(_DOC_EXTS) or s.startswith(('./', '/'))


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
