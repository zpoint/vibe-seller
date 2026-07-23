"""Vision image-generation config + kie.ai client + confirm registry.

Three concerns live here, deliberately together because they are small
and only ever used as a unit by ``app/routers/vision.py``:

1. **Config** — the kie.ai API key. Stored in ``~/.vibe-seller/vision.json``
   (mirrors ``profiles.json``: a secret, so NOT the DB), read back masked.
2. **kie.ai client** — create a generation job, poll it, download the
   result. A ``VISION_FAKE`` env switch short-circuits the network so
   e2e/CI can exercise the whole confirm→save→display path for free and
   deterministically.
3. **Confirm registry** — an in-process ``request_id -> asyncio.Future``
   map. The generate endpoint (called by the MCP tool) parks on the
   future after emitting an ``image_request`` SSE event; the confirm
   endpoint (called by the user's browser) resolves it with the possibly
   edited prompt/model. Both endpoints run in the one backend process,
   so a module-level dict is the whole mechanism.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx

from app.config import VIBE_SELLER_DIR

VISION_CONFIG_PATH = VIBE_SELLER_DIR / 'vision.json'

# kie.ai endpoints (verified live 2026-07).
_KIE_BASE = 'https://api.kie.ai'
_KIE_UPLOAD_BASE = 'https://kieai.redpandaai.co'

# Models we expose in the confirm dropdown. Keys are the values the
# frontend/agent pass; values are the exact kie.ai model identifiers.
# Kept small on purpose — a quality lane and a fast/cheap lane.
MODELS: dict[str, str] = {
    'nano-banana-pro': 'nano-banana-pro',
    'nano-banana-2': 'nano-banana-2',
}
DEFAULT_MODEL = 'nano-banana-pro'


# ─────────────────────────── config ───────────────────────────


def load_vision_config() -> dict:
    """Return the raw config dict (``{}`` if unset/unreadable)."""
    try:
        return json.loads(VISION_CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_vision_config(kie_api_key: str) -> None:
    """Persist the kie.ai key (0600). Empty string clears it."""
    cfg = load_vision_config()
    cfg['kie_api_key'] = (kie_api_key or '').strip()
    VISION_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    try:
        VISION_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def get_kie_api_key() -> str | None:
    """The configured kie.ai key, or None. Env var wins (for CI)."""
    env = os.environ.get('KIE_API_KEY', '').strip()
    if env:
        return env
    key = (load_vision_config().get('kie_api_key') or '').strip()
    return key or None


def mask_key(key: str | None) -> str:
    """Last-4 mask for display; never returns the full secret."""
    if not key:
        return ''
    if len(key) <= 4:
        return '••••'
    return '••••' + key[-4:]


def is_fake() -> bool:
    """True when the network should be skipped (e2e/CI)."""
    return os.environ.get('VISION_FAKE', '').lower() in ('1', 'true', 'yes')


# ─────────────────────── confirm registry ─────────────────────
#
# Confirms NEVER time out — like AskUserQuestion, the tool call simply
# waits for the user. The one invariant is single-pending-per-task: a
# new request for the same task supersedes (resolves) the old one, so
# the UI can never show two live cards for one task.

_pending_confirms: dict[str, asyncio.Future] = {}
_pending_by_task: dict[str, str] = {}  # task_id -> request_id
# request_id -> card payload (prompt/model/models/reference_images/…), so
# the confirm card can be re-served when a client opens the task AFTER the
# image_request SSE already fired (the tool blocks on the future until the
# user confirms; without this the card is unrecoverable on reconnect).
_pending_payload: dict[str, dict] = {}


def create_confirm(
    request_id: str, task_id: str, payload: dict | None = None
) -> asyncio.Future:
    """Register a pending confirmation and return its future.

    ``payload`` is the card data (prompt/model/models/reference_images/
    output_name/kind) kept so ``get_pending_request`` can re-serve the
    card on reconnect. Returns the request_id of a superseded prior
    request via ``supersede_pending`` — call that first (it needs the
    event loop running) if you want to notify the frontend.
    """
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_confirms[request_id] = fut
    _pending_by_task[task_id] = request_id
    _pending_payload[request_id] = payload or {}
    return fut


def get_pending_request(task_id: str) -> dict | None:
    """The still-pending confirm card for ``task_id`` (payload + id), or
    None. Lets a client that connected after the ``image_request`` event
    recover the card instead of the task blocking forever."""
    req_id = _pending_by_task.get(task_id)
    if not req_id:
        return None
    fut = _pending_confirms.get(req_id)
    if fut is None or fut.done():
        return None
    return {'request_id': req_id, **_pending_payload.get(req_id, {})}


def supersede_pending(task_id: str) -> str | None:
    """Resolve any pending confirm for this task as superseded.

    Returns the superseded request_id (for an expiry event) or None.
    """
    old_id = _pending_by_task.get(task_id)
    if not old_id:
        return None
    fut = _pending_confirms.get(old_id)
    if fut is not None and not fut.done():
        fut.set_result({'action': 'superseded'})
        return old_id
    return None


def resolve_confirm(request_id: str, payload: dict) -> bool:
    """Resolve a pending confirmation. Returns False if unknown/done."""
    fut = _pending_confirms.get(request_id)
    if fut is None or fut.done():
        return False
    fut.set_result(payload)
    return True


def discard_confirm(request_id: str, task_id: str | None = None) -> None:
    _pending_confirms.pop(request_id, None)
    _pending_payload.pop(request_id, None)
    if task_id and _pending_by_task.get(task_id) == request_id:
        _pending_by_task.pop(task_id, None)


# ───────────────────────── kie.ai client ──────────────────────


async def _upload_local_file(
    client: httpx.AsyncClient, path: Path, api_key: str
) -> str:
    """Base64-upload a local image to kie.ai, return its temp URL."""
    data = base64.b64encode(path.read_bytes()).decode('ascii')
    ext = path.suffix.lstrip('.').lower() or 'png'
    resp = await client.post(
        f'{_KIE_UPLOAD_BASE}/api/file-base64-upload',
        headers={'Authorization': f'Bearer {api_key}'},
        json={
            'base64Data': f'data:image/{ext};base64,{data}',
            'uploadPath': 'images',
            'fileName': path.name,
        },
    )
    body = resp.json()
    url = (body.get('data') or {}).get('downloadUrl') or (
        body.get('data') or {}
    ).get('url')
    if not url:
        raise RuntimeError(f'kie.ai upload failed: {body}')
    return url


async def _resolve_reference(
    client: httpx.AsyncClient, ref: str, task_dir: Path, api_key: str
) -> str | None:
    """Turn a reference (URL or workspace path) into a kie.ai URL."""
    if ref.startswith(('http://', 'https://')):
        return ref
    local = (task_dir / ref).resolve()
    if not local.is_file() or not local.is_relative_to(task_dir.resolve()):
        return None
    return await _upload_local_file(client, local, api_key)


async def generate_image(
    *,
    prompt: str,
    model: str,
    reference_images: list[str],
    task_dir: Path,
    aspect_ratio: str = '1:1',
    resolution: str = '2K',
) -> bytes:
    """Generate one image via kie.ai and return the PNG bytes.

    Raises RuntimeError on any kie.ai failure. Honours ``VISION_FAKE``.
    """
    if is_fake():
        return _fake_png(prompt, model)

    api_key = get_kie_api_key()
    if not api_key:
        raise RuntimeError('kie.ai API key not configured')
    kie_model = MODELS.get(model, MODELS[DEFAULT_MODEL])

    async with httpx.AsyncClient(timeout=120) as client:
        image_input: list[str] = []
        for ref in reference_images or []:
            url = await _resolve_reference(client, ref, task_dir, api_key)
            if url:
                image_input.append(url)

        create = await client.post(
            f'{_KIE_BASE}/api/v1/jobs/createTask',
            headers={'Authorization': f'Bearer {api_key}'},
            json={
                'model': kie_model,
                'input': {
                    'prompt': prompt,
                    'image_input': image_input,
                    'aspect_ratio': aspect_ratio,
                    'resolution': resolution,
                    'output_format': 'png',
                },
            },
        )
        cbody = create.json()
        task_id = (cbody.get('data') or {}).get('taskId')
        if not task_id:
            raise RuntimeError(f'kie.ai createTask failed: {cbody}')

        # Poll recordInfo until success/fail (Pro 2K ~ 60s).
        result_url = None
        for _ in range(60):
            await asyncio.sleep(4)
            info = await client.get(
                f'{_KIE_BASE}/api/v1/jobs/recordInfo',
                params={'taskId': task_id},
                headers={'Authorization': f'Bearer {api_key}'},
            )
            data = info.json().get('data') or {}
            state = data.get('state')
            if state == 'success':
                result_json = json.loads(data.get('resultJson') or '{}')
                urls = result_json.get('resultUrls') or []
                if urls:
                    result_url = urls[0]
                break
            if state == 'fail':
                raise RuntimeError(
                    f'kie.ai generation failed: {data.get("failMsg")}'
                )
        if not result_url:
            raise RuntimeError('kie.ai generation timed out')

        img = await client.get(result_url)
        return img.content


def _fake_png(prompt: str, model: str) -> bytes:
    """A deterministic placeholder PNG for VISION_FAKE mode.

    Uses Pillow when present for a legible tile; otherwise a bundled
    1x1 PNG so the display path still renders an ``<img>``.
    """
    try:
        import io  # noqa: PLC0415 — lazy: Pillow is optional

        from PIL import Image, ImageDraw  # noqa: PLC0415

        img = Image.new('RGB', (512, 512), (240, 240, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([8, 8, 503, 503], outline=(30, 41, 82), width=4)
        draw.text((20, 20), f'FAKE\n{model}', fill=(30, 41, 82))
        draw.text((20, 470), (prompt or '')[:60], fill=(90, 90, 100))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception:
        return base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m'
            'NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        )
