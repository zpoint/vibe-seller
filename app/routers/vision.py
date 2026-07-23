"""Vision image-generation endpoints.

Three routes:
  - GET  /api/vision/config           — is a key set? (masked; any user)
  - PUT  /api/vision/config           — set the kie.ai key (admin only)
  - POST /api/tasks/{id}/image/generate — MCP-tool entry; the confirm gate
  - POST /api/tasks/{id}/image/confirm  — user's approve/edit/cancel

The generate route is the confirmation gate AND the generator. It fails
immediately with 400 if no key is configured (so the agent's tool call
errors out at once), otherwise it emits an ``image_request`` SSE event
and blocks on a per-request future until the user confirms (with a
possibly edited prompt/model) or cancels. Only on confirm does it call
kie.ai, save the PNG into the task workspace, emit an ``image_generated``
event, and return the saved path to the agent.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from pathlib import Path
import re
import socket
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
import httpx

from app import vision
from app.ai.claude_backend_utils import get_next_seq
from app.auth import get_current_user
from app.database import async_session
from app.events.bus import event_bus
from app.models.task_message import TaskMessage
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

router = APIRouter(prefix='/api', tags=['vision'])

logger = logging.getLogger(__name__)

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'
_GEN_SUBDIR = 'generated_images'
_REF_SUBDIR = 'generated_images/refs'
_MAX_UPLOAD = 15 * 1024 * 1024  # 15 MB


def _safe_name(name: str) -> str:
    """Sanitise an agent-supplied output filename to a .png basename."""
    base = Path(name or '').name or 'image.png'
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base)
    if not base.lower().endswith('.png'):
        base = base + '.png'
    return base


# ─────────────────────────── config ───────────────────────────


@router.get('/vision/config')
async def get_vision_config(current_user: User = Depends(get_current_user)):
    key = vision.get_kie_api_key()
    return {
        'kie_api_key_set': bool(key),
        'kie_api_key_masked': vision.mask_key(key),
        'models': vision.catalog_public(),
        'default_model': vision.DEFAULT_MODEL,
        # Surfaced so e2e tests can refuse to run against a server that
        # would hit the real image API (they must be offline-only).
        'fake': vision.is_fake(),
    }


@router.put('/vision/config')
async def put_vision_config(
    body: dict,
    current_user: User = Depends(get_current_user),
):
    if current_user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    vision.save_vision_config(body.get('kie_api_key', ''))
    key = vision.get_kie_api_key()
    return {
        'kie_api_key_set': bool(key),
        'kie_api_key_masked': vision.mask_key(key),
    }


# ─────────────────────── generate (MCP) ───────────────────────


@router.post('/tasks/{task_id}/image/generate')
async def generate_task_image(
    task_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
):
    """Confirm-gated image generation. Called by the MCP tool.

    Fails at once if no kie.ai key is configured. Otherwise emits an
    ``image_request`` event, waits for the user to confirm/edit/cancel,
    then generates + saves the image and returns its workspace path.
    """
    if not vision.get_kie_api_key() and not vision.is_fake():
        raise HTTPException(
            status_code=400,
            detail=(
                'kie.ai API key not configured. Ask the user to set it in '
                'Settings → AI → Vision before generating images.'
            ),
        )

    task_dir = (_TASKS_DIR / task_id).resolve()
    if not task_dir.is_relative_to(_TASKS_DIR.resolve()):
        raise HTTPException(status_code=400, detail='Invalid task id')

    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        raise HTTPException(status_code=400, detail='prompt is required')
    model = body.get('model') or vision.DEFAULT_MODEL
    if model not in vision.model_ids():
        model = vision.DEFAULT_MODEL
    reference_images = body.get('reference_images') or []
    output_name = _safe_name(body.get('output_name') or 'image.png')
    kind = body.get('kind') or ''  # optional label e.g. "main"/"infographic"

    # Single-pending-per-task: a new request kills any older live card
    # for this task (agent retry, task retry) so the UI never shows two
    # actionable confirms at once.
    superseded = vision.supersede_pending(task_id)
    if superseded:
        await event_bus.emit(
            'image_request_expired',
            {'task_id': task_id, 'request_id': superseded},
        )

    request_id = uuid.uuid4().hex
    fut = vision.create_confirm(request_id, task_id)
    await event_bus.emit(
        'image_request',
        {
            'task_id': task_id,
            'request_id': request_id,
            'prompt': prompt,
            'model': model,
            'models': vision.catalog_public(),
            'reference_images': reference_images,
            'output_name': output_name,
            'kind': kind,
        },
    )

    # NO timeout — mirror AskUserQuestion: the tool call simply waits
    # for the user, however long they take.
    try:
        decision = await fut
    finally:
        vision.discard_confirm(request_id, task_id)

    if decision.get('action') == 'superseded':
        return {
            'status': 'superseded',
            'message': (
                'This request was replaced by a newer image request. '
                'Do NOT retry it — the newer request is the live one.'
            ),
        }
    if decision.get('action') != 'confirm':
        return {
            'status': 'cancelled',
            'message': 'User cancelled this image generation.',
        }

    # User-edited values win over the agent's proposal.
    final_prompt = (decision.get('prompt') or prompt).strip()
    final_model = decision.get('model') or model
    if final_model not in vision.model_ids():
        final_model = model
    # References the user added in the confirm card (uploaded files →
    # workspace-relative paths, or pasted URLs) are appended.
    added = [r for r in (decision.get('added_references') or []) if r]
    final_refs = list(reference_images) + added

    # Signal generation-in-progress. The kie.ai call below can take a
    # minute or more; without this the UI would show only the generic
    # "agent is working" spinner. The frontend flips the confirm card into
    # a "generating…" state on this event and clears it on image_generated.
    await event_bus.emit(
        'image_generating',
        {
            'task_id': task_id,
            'request_id': request_id,
            'model': final_model,
            'kind': kind,
        },
    )

    try:
        png = await vision.generate_image(
            prompt=final_prompt,
            model=final_model,
            reference_images=final_refs,
            task_dir=task_dir,
        )
    except Exception as e:  # noqa: BLE001 — relay any kie.ai failure
        logger.warning('Image generation failed for task %s: %s', task_id, e)
        raise HTTPException(status_code=502, detail=f'Generation failed: {e}')

    out_dir = task_dir / _GEN_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / output_name
    if out_path.exists():
        stem = out_path.stem
        out_path = out_dir / f'{stem}-{request_id[:6]}.png'
    out_path.write_bytes(png)

    rel_path = f'{_GEN_SUBDIR}/{out_path.name}'
    file_url = f'/api/tasks/{task_id}/files/{rel_path}'
    await event_bus.emit(
        'image_generated',
        {
            'task_id': task_id,
            'request_id': request_id,
            'path': rel_path,
            'url': file_url,
            'prompt': final_prompt,
            'model': final_model,
            'kind': kind,
        },
    )
    # Persist the generated image so it re-renders inline on reload.
    # image_generated is otherwise a live-only SSE event, and the
    # conversation is rebuilt from persisted messages
    # (buildConversationItems) — without this record the image vanishes on
    # navigation and only the agent's text path survives.
    async with async_session() as db:
        seq = await get_next_seq(db, task_id)
        db.add(
            TaskMessage(
                task_id=task_id,
                role='generated_image',
                content=json.dumps({
                    'path': rel_path,
                    'url': file_url,
                    'prompt': final_prompt,
                    'model': final_model,
                    'kind': kind,
                }),
                seq=seq,
            )
        )
        await db.commit()
    return {
        'status': 'ok',
        'path': rel_path,
        'url': file_url,
        'prompt': final_prompt,
        'model': final_model,
    }


# ─────────────────────── confirm (user) ───────────────────────


@router.post('/tasks/{task_id}/image/confirm')
async def confirm_task_image(
    task_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
):
    """Resolve a pending image request (confirm/edit/cancel)."""
    request_id = body.get('request_id')
    action = body.get('action')
    if not request_id or action not in ('confirm', 'cancel'):
        raise HTTPException(
            status_code=400, detail='request_id and action required'
        )
    payload = {
        'action': action,
        'prompt': body.get('prompt'),
        'model': body.get('model'),
        'added_references': body.get('added_references') or [],
    }
    if not vision.resolve_confirm(request_id, payload):
        raise HTTPException(
            status_code=404,
            detail='No pending image request (expired or already handled)',
        )
    return {'ok': True}


# ─────────────────── upload a reference image ─────────────────


@router.post('/tasks/{task_id}/image/upload-reference')
async def upload_reference(
    task_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Save a user-supplied reference image into the task workspace.

    Returns the workspace-relative ``path`` (to pass back on confirm as
    an added reference) and the ``url`` that serves it for preview.
    """
    task_dir = (_TASKS_DIR / task_id).resolve()
    if not task_dir.is_relative_to(_TASKS_DIR.resolve()):
        raise HTTPException(status_code=400, detail='Invalid task id')
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail='Empty file')
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail='File too large (max 15MB)')

    name = _safe_name(file.filename or 'ref.png')
    # _safe_name forces .png; keep the real extension for non-png images.
    orig_ext = Path(file.filename or '').suffix.lower()
    if orig_ext in ('.jpg', '.jpeg', '.webp', '.gif'):
        name = re.sub(r'\.png$', orig_ext, name)
    out_dir = task_dir / _REF_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / name
    if out_path.exists():
        out_path = (
            out_dir / f'{out_path.stem}-{uuid.uuid4().hex[:6]}{out_path.suffix}'
        )
    out_path.write_bytes(data)

    rel_path = f'{_REF_SUBDIR}/{out_path.name}'
    return {
        'path': rel_path,
        'url': f'/api/tasks/{task_id}/files/{rel_path}',
    }


# ──────────── preview proxy for remote reference images ───────


def _is_public_host(host: str) -> bool:
    """Reject loopback/private/link-local hosts (basic SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False
    return True


@router.get('/vision/ref-proxy')
async def ref_proxy(url: str, current_user: User = Depends(get_current_user)):
    """Fetch a remote reference image server-side for preview.

    The confirm card can't always load supplier CDNs cross-origin (1688
    images fail in the browser but fetch fine server-side). Proxying
    makes previews render same-origin. Does not affect generation —
    the model fetches originals itself.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise HTTPException(status_code=400, detail='Invalid url')
    if not _is_public_host(parsed.hostname):
        raise HTTPException(status_code=400, detail='Host not allowed')
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            resp = await c.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail='Fetch failed')
    ctype = resp.headers.get('content-type', '')
    if resp.status_code != 200 or not ctype.startswith('image/'):
        raise HTTPException(status_code=502, detail='Not an image')
    if len(resp.content) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail='Image too large')
    return Response(
        content=resp.content,
        media_type=ctype,
        headers={'Cache-Control': 'private, max-age=300'},
    )
