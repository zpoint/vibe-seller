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
import collections
import dataclasses
import json
import os
from pathlib import Path

import httpx

from app.config import VIBE_SELLER_DIR

VISION_CONFIG_PATH = VIBE_SELLER_DIR / 'vision.json'

# kie.ai endpoints (verified live 2026-07).
_KIE_BASE = 'https://api.kie.ai'
_KIE_UPLOAD_BASE = 'https://kieai.redpandaai.co'

# kie.ai bills in credits at a flat $0.005/credit ($50 = 10,000 credits).
# We surface a per-image price *hint* on the confirm card, not a bill —
# actual cost varies by resolution/tier, so these are the representative
# (typically 2K / standard) image-to-image prices from kie.ai's public
# pricing API (POST api.kie.ai/client/v1/model-pricing/page), captured
# 2026-07. Refresh manually if kie.ai revises them.
CREDIT_USD = 0.005
# Fixed USD→CNY for the ¥ hint shown to Chinese users. Illustrative, not
# a live FX rate — the price itself is already approximate.
USD_CNY = 7.2


@dataclasses.dataclass(frozen=True)
class ImageModel:
    """One selectable image model, served through kie.ai's unified
    ``jobs/createTask`` API.

    ``id`` is OUR stable contract (what the agent/frontend/tool pass and
    what we validate); ``slug`` is kie.ai's exact ``model`` string, which
    we own the mapping to so kie's naming (slashes, ``-image-to-image``
    variants) never leaks into our API. ``ref_field`` / ``ref_array``
    capture the one thing that genuinely differs per model: the name and
    cardinality of the reference-image input — ``image_input`` (nano),
    ``input_urls`` (gpt/flux), ``image_urls`` (seedream), or a single
    ``image_url`` (qwen/ideogram). ``extra`` is static per-model input
    that the model's schema documents (aspect ratio, resolution, etc.).
    """

    id: str
    slug: str
    provider: str
    label: str
    ref_field: str
    ref_array: bool
    usd: float
    extra: dict = dataclasses.field(default_factory=dict)


# Verified catalog across the major image providers — every entry is
# image-to-image capable and runs on the one configured kie.ai key.
# Slugs, reference-field names, per-tier params, and prices were verified
# against kie.ai docs + pricing (2026-07).
#
# Defined as per-family specs and flattened to ONE selectable variant per
# resolution/quality tier, so the confirm card shows the real price of
# each tier (GPT Image 2 at 1K/2K/4K, etc.) instead of a single
# hand-picked number — the bug that made cross-model prices look
# inconsistent. FIRST flattened entry is the default.
#
# ``_Tier`` carries the tier's own kie param (``resolution`` /
# ``quality`` / ``rendering_speed``); ``base_extra`` is merged under every
# tier. A lone ``_Tier('', '', usd, {})`` = a model with no tiers.
_Tier = collections.namedtuple('_Tier', 'suffix label usd extra')
_Family = collections.namedtuple(
    '_Family',
    'fid slug provider label ref_field ref_array base_extra tiers',
)

_FAMILIES: list = [
    _Family(
        'nano-banana-pro',
        'nano-banana-pro',
        'Google',
        'Nano Banana Pro',
        'image_input',
        True,
        {'aspect_ratio': '1:1', 'output_format': 'png'},
        [
            _Tier('2k', '2K', 0.09, {'resolution': '2K'}),
            _Tier('4k', '4K', 0.12, {'resolution': '4K'}),
        ],
    ),
    _Family(
        'nano-banana-2',
        'nano-banana-2',
        'Google',
        'Nano Banana 2',
        'image_input',
        True,
        {'aspect_ratio': '1:1'},
        [
            _Tier('1k', '1K', 0.04, {'resolution': '1K'}),
            _Tier('2k', '2K', 0.06, {'resolution': '2K'}),
            _Tier('4k', '4K', 0.09, {'resolution': '4K'}),
        ],
    ),
    _Family(
        'nano-banana-edit',
        'google/nano-banana-edit',
        'Google',
        'Nano Banana Edit',
        'image_urls',
        True,
        {},
        [_Tier('', '', 0.02, {})],
    ),
    _Family(
        'gpt-image-2',
        'gpt-image-2-image-to-image',
        'OpenAI',
        'GPT Image 2',
        'input_urls',
        True,
        {'aspect_ratio': '1:1'},
        [
            _Tier('1k', '1K', 0.03, {'resolution': '1K'}),
            _Tier('2k', '2K', 0.05, {'resolution': '2K'}),
            _Tier('4k', '4K', 0.08, {'resolution': '4K'}),
        ],
    ),
    _Family(
        'gpt-image-1.5',
        'gpt-image/1.5-image-to-image',
        'OpenAI',
        'GPT Image 1.5',
        'input_urls',
        True,
        {'aspect_ratio': '1:1'},
        [
            _Tier('medium', 'Medium', 0.02, {'quality': 'medium'}),
            _Tier('high', 'High', 0.11, {'quality': 'high'}),
        ],
    ),
    _Family(
        'seedream-5-pro',
        'seedream/5-pro-image-to-image',
        'ByteDance',
        'Seedream 5 Pro',
        'image_urls',
        True,
        {},
        [
            _Tier('basic', 'Basic', 0.035, {'quality': 'basic'}),
            _Tier('high', 'High', 0.07, {'quality': 'high'}),
        ],
    ),
    _Family(
        'seedream-4.5',
        'seedream/4.5-edit',
        'ByteDance',
        'Seedream 4.5',
        'image_urls',
        True,
        {},
        [_Tier('', '', 0.0325, {})],
    ),
    _Family(
        'flux-2-pro',
        'flux-2/pro-image-to-image',
        'Black Forest Labs',
        'Flux-2 Pro',
        'input_urls',
        True,
        {},
        [
            _Tier('1k', '1K', 0.025, {'resolution': '1K'}),
            _Tier('2k', '2K', 0.035, {'resolution': '2K'}),
        ],
    ),
    _Family(
        'flux-2-flex',
        'flux-2/flex-image-to-image',
        'Black Forest Labs',
        'Flux 2 Flex',
        'input_urls',
        True,
        {},
        [
            _Tier('1k', '1K', 0.07, {'resolution': '1K'}),
            _Tier('2k', '2K', 0.12, {'resolution': '2K'}),
        ],
    ),
    _Family(
        'qwen-image-edit',
        'qwen/image-edit',
        'Qwen',
        'Qwen Image Edit',
        'image_url',
        False,
        {},
        [_Tier('', '', 0.03, {})],
    ),
    _Family(
        'ideogram-v3-remix',
        'ideogram/v3-remix',
        'Ideogram',
        'Ideogram V3 Remix',
        'image_url',
        False,
        {},
        [
            _Tier('turbo', 'Turbo', 0.0175, {'rendering_speed': 'TURBO'}),
            _Tier(
                'balanced', 'Balanced', 0.035, {'rendering_speed': 'BALANCED'}
            ),
            _Tier('quality', 'Quality', 0.05, {'rendering_speed': 'QUALITY'}),
        ],
    ),
]

IMAGE_MODELS: list[ImageModel] = [
    ImageModel(
        id=f'{fam.fid}-{tier.suffix}' if tier.suffix else fam.fid,
        slug=fam.slug,
        provider=fam.provider,
        label=f'{fam.label} · {tier.label}' if tier.label else fam.label,
        ref_field=fam.ref_field,
        ref_array=fam.ref_array,
        usd=tier.usd,
        extra={**fam.base_extra, **tier.extra},
    )
    for fam in _FAMILIES
    for tier in fam.tiers
]

_MODELS_BY_ID: dict[str, ImageModel] = {m.id: m for m in IMAGE_MODELS}
DEFAULT_MODEL = IMAGE_MODELS[0].id


def model_ids() -> list[str]:
    """Valid model ids, in display order (default first)."""
    return [m.id for m in IMAGE_MODELS]


def get_model(model_id: str | None) -> ImageModel:
    """Resolve a model id to its ``ImageModel`` (falls back to default)."""
    return _MODELS_BY_ID.get(model_id or '', _MODELS_BY_ID[DEFAULT_MODEL])


def catalog_public() -> list[dict]:
    """The catalog as the frontend/agent see it: id, provider, label, and
    a per-image price hint in both USD and (fixed-rate) CNY. Never the
    kie.ai slug — that stays server-side."""
    return [
        {
            'id': m.id,
            'provider': m.provider,
            'label': m.label,
            'usd': round(m.usd, 4),
            'cny': round(m.usd * USD_CNY, 2),
            'default': m.id == DEFAULT_MODEL,
        }
        for m in IMAGE_MODELS
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


def create_confirm(request_id: str, task_id: str) -> asyncio.Future:
    """Register a pending confirmation and return its future.

    Returns the request_id of a superseded prior request via
    ``supersede_pending`` — call that first (it needs the event loop
    running) if you want to notify the frontend.
    """
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_confirms[request_id] = fut
    _pending_by_task[task_id] = request_id
    return fut


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
) -> bytes:
    """Generate one image via kie.ai and return the PNG bytes.

    Raises RuntimeError on any kie.ai failure. Honours ``VISION_FAKE``.

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
