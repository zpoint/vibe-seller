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
import logging
import os
from pathlib import Path

import httpx

from app import vision_catalog
from app.config import VIBE_SELLER_DIR
from app.events.bus import event_bus

logger = logging.getLogger(__name__)

VISION_CONFIG_PATH = VIBE_SELLER_DIR / 'vision.json'

# kie.ai endpoints (verified live 2026-07).
_KIE_BASE = 'https://api.kie.ai'
_KIE_UPLOAD_BASE = 'https://kieai.redpandaai.co'

# kie.ai bills in credits at a flat $0.005/credit ($50 = 10,000 credits).
# The confirm card shows a per-image price *hint*, not a bill: kie.ai's
# live per-image price for that tier when the vision_catalog sync could
# match it, else the bundled price in app/vision_models.py.
CREDIT_USD = 0.005
# Fixed USD→CNY for the ¥ hint shown to Chinese users. Illustrative, not
# a live FX rate — the price itself is already approximate.
USD_CNY = 7.2


# The catalog itself — which models, their kie params, prices, and
# availability — lives in ``app/vision_catalog.py`` (served state) and
# ``app/vision_models.py`` (the curated data). These accessors are the
# only way the rest of the app reads it, so a sync is picked up on the
# next call without any caller caching a stale list.
ImageModel = vision_catalog.ImageModel


def image_models() -> list[ImageModel]:
    """Selectable tiers in display order; the first is the default."""
    return vision_catalog.current_models()


def default_model() -> str:
    """The default model id: the first selectable tier."""
    return image_models()[0].id


def model_ids() -> list[str]:
    """Valid model ids, in display order (default first)."""
    return [m.id for m in image_models()]


def is_known_model(model_id: str | None) -> bool:
    """Whether ``model_id`` is in the catalog, hidden tiers included."""
    return any(m.id == model_id for m in vision_catalog.all_models())


def resolve_model_id(model_id: str | None) -> str:
    """A requested id → the id to use.

    A catalog id is kept as-is, EVEN if the availability check has
    since hidden it: an id here was already chosen (proposed, confirmed
    on a card, or named by a resume after a timeout), and silently
    swapping it would generate — and bill — a different model than the
    one shown, or orphan an in-flight job. kie.ai rejecting a truly
    delisted slug is an honest error; a substitution is not. An id that
    left the catalog maps to its named successor (agents and saved
    prompts may still say ``gpt-image-2-2k``); anything else is the
    default.
    """
    if is_known_model(model_id):
        return model_id
    successor = vision_catalog.retired_successor(model_id or '')
    if is_known_model(successor):
        return successor
    return default_model()


def get_model(model_id: str | None) -> ImageModel:
    """Resolve a model id to its ``ImageModel`` (see resolve_model_id)."""
    wanted = resolve_model_id(model_id)
    return next(m for m in vision_catalog.all_models() if m.id == wanted)


def catalog_public() -> list[dict]:
    """The catalog as the frontend/agent see it: id, provider, label, and
    a per-image price hint in both USD and (fixed-rate) CNY. Never the
    kie.ai slug — that stays server-side."""
    models = image_models()
    return [
        {
            'id': m.id,
            'provider': m.provider,
            'label': m.label,
            'usd': round(m.usd, 4),
            'cny': round(m.usd * USD_CNY, 2),
            'default': i == 0,
        }
        for i, m in enumerate(models)
    ]


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


async def interrupt_pending_confirm(task_id: str) -> bool:
    """Retire a pending confirm card when the user replies instead of
    confirming. Resolves the parked future as ``interrupted`` (so the
    blocked ``generate`` tool returns at once, telling the agent to read
    the user's message rather than generate the proposal) and emits
    ``image_request_interrupted`` so the card shows a truthful, retired
    footer — distinct from ``image_request_expired`` (replaced by a newer
    request). No-op (returns False) if nothing is pending, i.e. the image
    is already generating or there was never a card."""
    pending = get_pending_request(task_id)
    if not pending:
        return False
    resolve_confirm(pending['request_id'], {'action': 'interrupted'})
    await event_bus.emit(
        'image_request_interrupted',
        {'task_id': task_id, 'request_id': pending['request_id']},
    )
    return True


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


# ── Poll budget ───────────────────────────────────────────────────
#
# kie.ai generation is async: createTask returns a taskId, then we poll
# recordInfo until ``success``. The budget below is how long we are
# willing to poll, and it is per RESOLUTION TIER because generation time
# scales with output pixels — a flat budget sized for 2K kills 4K jobs.
#
# Measured against the live endpoint (2026-07, nano-banana-pro, same
# prompt + reference): 2K lands in ~60s; 4K took 147s on one run and blew
# past 240s on another (a real user generation, which the flat 60x4s
# budget then killed at 4m17s). So 4K's tail is what needs covering, not
# its median — hence 600s, ~4x the measured median.
#
# Giving up does NOT cancel the kie.ai job: it finishes and is billed.
# That is why exhaustion raises :class:`GenerationTimeout` carrying the
# taskId instead of a bare RuntimeError — see that class.
_POLL_EVERY_S = 4
_POLL_BUDGET_S = {'1K': 240, '2K': 240, '4K': 600}
_POLL_BUDGET_DEFAULT_S = 240


def poll_budget_s(m: ImageModel) -> int:
    """Seconds we are willing to poll for *m*'s tier.

    Tier comes from the model's own ``extra['resolution']`` (the param
    the tier injects). Models whose tier is quality- or speed-based
    rather than resolution-based (``quality`` / ``rendering_speed``) get
    the default.
    """
    return _POLL_BUDGET_S.get(
        str(m.extra.get('resolution') or ''), _POLL_BUDGET_DEFAULT_S
    )


class GenerationTimeout(RuntimeError):
    """Our poll budget ran out while the kie.ai job was still running.

    Carries ``kie_task_id`` because giving up on our side does NOT cancel
    the job — it completes on kie.ai and is billed either way. Dropping
    the id orphans an image the user paid for and makes a retry pay a
    second time, so callers must keep it and resume polling instead
    (``generate_image(resume_task_id=...)``).
    """

    def __init__(self, kie_task_id: str, waited_s: int, model_id: str):
        self.kie_task_id = kie_task_id
        self.waited_s = waited_s
        self.model_id = model_id
        super().__init__(
            f'kie.ai generation still running after {waited_s}s '
            f'(model {model_id}, kie taskId {kie_task_id})'
        )


async def generate_image(
    *,
    prompt: str,
    model: str,
    reference_images: list[str],
    task_dir: Path,
    resume_task_id: str | None = None,
) -> bytes:
    """Generate one image via kie.ai and return the PNG bytes.

    Raises RuntimeError on any kie.ai failure, or
    :class:`GenerationTimeout` (a subclass, carrying the kie.ai taskId)
    when the poll budget runs out with the job still in flight. Honours
    ``VISION_FAKE``.

    ``resume_task_id`` polls an EXISTING kie.ai job instead of creating a
    new one — the retry path after a ``GenerationTimeout``. It skips both
    the reference upload and ``createTask``, so the already-billed image
    is collected rather than paid for twice.

    The ``input`` payload is assembled per-model: every model here goes
    through the same ``jobs/createTask`` endpoint, but the reference-image
    field name and cardinality differ (see ``ImageModel``), and each model
    carries its own static ``extra`` params. Getting this right is what
    makes a non-nano model actually receive its references instead of
    silently dropping them.
    """
    if is_fake():
        return _fake_png(prompt, model)

    api_key = get_kie_api_key()
    if not api_key:
        raise RuntimeError('kie.ai API key not configured')
    m = get_model(model)

    async with httpx.AsyncClient(timeout=120) as client:
        if resume_task_id:
            # Resume: the job is already created (and already billed).
            kie_task = resume_task_id
            logger.info(
                'Resuming kie.ai job %s (model %s) instead of creating a '
                'new one',
                kie_task,
                m.id,
            )
        else:
            resolved: list[str] = []
            for ref in reference_images or []:
                url = await _resolve_reference(client, ref, task_dir, api_key)
                if url:
                    resolved.append(url)

            image_input: dict = {'prompt': prompt}
            if m.ref_array:
                image_input[m.ref_field] = resolved
            elif resolved:
                # Single-reference models (qwen/image-edit, ideogram remix)
                # take exactly one image; use the primary reference.
                image_input[m.ref_field] = resolved[0]
            image_input.update(m.extra)

            create = await client.post(
                f'{_KIE_BASE}/api/v1/jobs/createTask',
                headers={'Authorization': f'Bearer {api_key}'},
                json={'model': m.slug, 'input': image_input},
            )
            cbody = create.json()
            kie_task = (cbody.get('data') or {}).get('taskId')
            if not kie_task:
                raise RuntimeError(f'kie.ai createTask failed: {cbody}')

        # Poll recordInfo until success/fail. Budget is per tier — see
        # ``poll_budget_s``.
        budget_s = poll_budget_s(m)
        result_url = None
        for _ in range(max(1, budget_s // _POLL_EVERY_S)):
            await asyncio.sleep(_POLL_EVERY_S)
            info = await client.get(
                f'{_KIE_BASE}/api/v1/jobs/recordInfo',
                params={'taskId': kie_task},
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
            raise GenerationTimeout(kie_task, budget_s, m.id)

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
