"""Live E2E: the image-model catalog against the REAL kie.ai.

What the in-process tests can't prove, because they mock kie.ai:

1. the ``vision_catalog`` sync, run on a real server, prices EVERY tier
   from kie.ai's live pricing table and finds every model still listed;
2. every family in the catalog actually generates — kie.ai accepts the
   slug, the reference field and the static params we send. A wrong
   field name is silently dropped by some models, so the reference must
   go through the real upload + confirm path, exactly as a user's does.

It SPENDS kie.ai credits (one cheapest-tier image per family, roughly
$0.3 in total), so it is opt-in twice: the server must run with a real
key (not ``VISION_FAKE``), and ``VISION_LIVE_E2E=1`` must be set.

Run:
  KIE_API_KEY=... MOCK_CLI=tests/e2e/mock_cli.py PORT=7798 \
    VIBE_HOME=~/.vibe-seller-vision-live ./start.sh 7798
  VISION_LIVE_E2E=1 E2E_BASE_URL=http://127.0.0.1:7798 \
    .venv/bin/python -m pytest --e2e tests/e2e/test_vision_catalog_live.py
"""

import contextlib
import io
import os
import threading
import time

import httpx
import pytest

from app import vision_catalog, vision_models
from tests.e2e.conftest import BASE_URL

pytestmark = [pytest.mark.e2e]

GEN_TIMEOUT_S = 330  # the server's own 2K poll budget (240s) + slack


def _login() -> httpx.Client:
    client = httpx.Client(timeout=60)
    client.post(
        f'{BASE_URL}/api/auth/login',
        json={'identifier': 'admin@vibe-seller.local', 'password': 'admin'},
    )
    return client


@contextlib.contextmanager
def _session():
    client = _login()
    try:
        yield client
    finally:
        client.close()


if os.environ.get('VISION_LIVE_E2E') != '1':
    pytest.skip(
        'live kie.ai e2e spends credits; set VISION_LIVE_E2E=1',
        allow_module_level=True,
    )
try:
    with _session() as _c:
        _cfg = _c.get(f'{BASE_URL}/api/vision/config').json()
except Exception:
    _cfg = {}
if _cfg.get('fake') or not _cfg.get('kie_api_key_set'):
    pytest.skip(
        'needs a server with a real kie.ai key and VISION_FAKE off',
        allow_module_level=True,
    )

FAMILIES = vision_models.IMAGE_FAMILIES
TIER_COUNT = len(vision_catalog.flatten(vision_catalog._bundled_families()))


def _cheapest_tier_id(fam: dict) -> str:
    tier = min(fam['tiers'], key=lambda t: t['usd'])
    return f'{fam["id"]}-{tier["suffix"]}' if tier['suffix'] else fam['id']


def _reference_png() -> bytes:
    """A plain product-like shape: something every model can edit."""
    from PIL import Image, ImageDraw  # noqa: PLC0415 — test-only dep

    img = Image.new('RGB', (768, 768), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([234, 160, 534, 640], radius=60, fill=(30, 90, 200))
    draw.rectangle([314, 110, 454, 170], fill=(40, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def test_sync_prices_and_lists_every_tier():
    with _session() as c:
        r = c.post(f'{BASE_URL}/api/sync/vision_catalog/run')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['status'] in ('updated', 'unchanged'), body
        detail = body['detail']
        # Every tier found its own live row on kie.ai's pricing table.
        assert detail.get('prices') in ('updated', 'unchanged'), detail
        assert detail.get('prices_priced') == TIER_COUNT, detail
        # kie.ai still lists every model we offer.
        assert detail.get('available') in ('updated', 'unchanged'), detail
        assert detail.get('available_hidden') == 0, detail

        models = c.get(f'{BASE_URL}/api/vision/config').json()['models']
        assert len(models) == TIER_COUNT
        assert all(m['usd'] > 0 for m in models)


@pytest.mark.parametrize('fam', FAMILIES, ids=[f['id'] for f in FAMILIES])
def test_family_generates_with_a_reference(fam):
    model_id = _cheapest_tier_id(fam)
    with _session() as c:
        r = c.post(
            f'{BASE_URL}/api/tasks', json={'title': f'live image {model_id}'}
        )
        r.raise_for_status()
        task_id = r.json()['id']
        up = c.post(
            f'{BASE_URL}/api/tasks/{task_id}/image/upload-reference',
            files={'file': ('bottle.png', _reference_png(), 'image/png')},
        )
        up.raise_for_status()
        ref_path = up.json()['path']

        # The agent's tool call: blocks until the user confirms.
        result: dict = {}

        def _generate():
            with _session() as gc:
                gc.timeout = GEN_TIMEOUT_S + 60
                resp = gc.post(
                    f'{BASE_URL}/api/tasks/{task_id}/image/generate',
                    json={
                        'prompt': (
                            'Place this bottle on a clean white studio '
                            'background with soft shadows.'
                        ),
                        'model': model_id,
                        'reference_images': [ref_path],
                        'output_name': 'live.png',
                    },
                )
                result['status'] = resp.status_code
                result['body'] = resp.json()

        worker = threading.Thread(target=_generate, daemon=True)
        worker.start()

        deadline = time.monotonic() + 30
        pending = {}
        while time.monotonic() < deadline:
            pending = c.get(
                f'{BASE_URL}/api/tasks/{task_id}/image/pending'
            ).json()
            if pending.get('pending'):
                break
            time.sleep(0.5)
        assert pending.get('pending'), 'confirm card never appeared'
        assert pending['model'] == model_id

        ok = c.post(
            f'{BASE_URL}/api/tasks/{task_id}/image/confirm',
            json={
                'request_id': pending['request_id'],
                'action': 'confirm',
                'model': model_id,
            },
        )
        assert ok.status_code == 200, ok.text

        worker.join(GEN_TIMEOUT_S + 90)
        assert not worker.is_alive(), f'{model_id}: generation hung'
        assert result.get('status') == 200, (model_id, result)
        assert result['body'].get('status') == 'ok', (model_id, result)

        path = result['body']['path']
        img = c.get(f'{BASE_URL}/api/tasks/{task_id}/files/{path}')
        assert img.status_code == 200
        # Decode rather than size-check: models return PNG, JPEG or WEBP,
        # and a well-compressed WEBP of a simple scene is only a few KB.
        from PIL import Image  # noqa: PLC0415 — test-only dep

        out = Image.open(io.BytesIO(img.content))
        out.load()
        assert min(out.size) >= 512, f'{model_id}: {out.size}'
        c.post(f'{BASE_URL}/api/tasks/{task_id}/stop')
