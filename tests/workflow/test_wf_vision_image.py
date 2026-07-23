"""Workflow tests for the confirm-gated image generation flow.

Exercises the real API + event bus (kie.ai stubbed via VISION_FAKE):
  - config PUT (admin) then GET returns masked, never the secret
  - generate fails immediately (400) when no key is configured
  - the full gate: generate emits image_request, blocks; confirm with an
    edited prompt resolves it; the PNG is saved and image_generated fires
  - cancel returns a cancelled status and writes nothing
"""

import asyncio
import json
import shutil
import uuid

import pytest

from app import vision
from app.events.bus import event_bus
from app.workspace.manager import VIBE_SELLER_DIR

pytestmark = pytest.mark.workflow

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'


async def _drain_until(queue, event_type, timeout=5.0):
    """Return the first bus payload of ``event_type`` within timeout."""

    async def _loop():
        while True:
            raw = await queue.get()
            data = json.loads(raw)
            if data.get('type') == event_type:
                return data

    return await asyncio.wait_for(_loop(), timeout)


async def test_config_put_get_masked(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(vision, 'VISION_CONFIG_PATH', tmp_path / 'vision.json')
    monkeypatch.delenv('KIE_API_KEY', raising=False)

    r = await admin_client.put(
        '/api/vision/config', json={'kie_api_key': 'sk-secret-9876'}
    )
    assert r.status_code == 200
    assert r.json()['kie_api_key_set'] is True

    g = await admin_client.get('/api/vision/config')
    body = g.json()
    assert body['kie_api_key_set'] is True
    assert body['kie_api_key_masked'].endswith('9876')
    assert 'secret' not in body['kie_api_key_masked']  # body never leaks
    assert 'nano-banana-pro' in body['models']


async def test_generate_fails_without_key(admin_client, monkeypatch):
    monkeypatch.delenv('KIE_API_KEY', raising=False)
    monkeypatch.delenv('VISION_FAKE', raising=False)
    monkeypatch.setattr(vision, 'load_vision_config', lambda: {})

    tid = str(uuid.uuid4())
    r = await admin_client.post(
        f'/api/tasks/{tid}/image/generate',
        json={'prompt': 'a main image', 'model': 'nano-banana-pro'},
    )
    assert r.status_code == 400
    assert 'not configured' in r.json()['detail']


async def test_confirm_flow_saves_and_emits(admin_client, monkeypatch):
    monkeypatch.setenv('VISION_FAKE', '1')  # no network
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    queue = event_bus.subscribe()
    try:
        gen = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={
                    'prompt': '主图：纯白背景',
                    'model': 'nano-banana-pro',
                    'output_name': 'main.png',
                    'kind': 'main',
                },
            )
        )
        req = await _drain_until(queue, 'image_request')
        assert req['task_id'] == tid
        assert req['prompt'] == '主图：纯白背景'
        request_id = req['request_id']

        # User edits the prompt, then confirms.
        c = await admin_client.post(
            f'/api/tasks/{tid}/image/confirm',
            json={
                'request_id': request_id,
                'action': 'confirm',
                'prompt': '主图：纯白背景，产品占85%',
                'model': 'nano-banana-pro',
            },
        )
        assert c.json()['ok'] is True

        gen_resp = await asyncio.wait_for(gen, timeout=10)
        body = gen_resp.json()
        assert body['status'] == 'ok'
        assert body['prompt'] == '主图：纯白背景，产品占85%'  # edit won
        assert body['path'] == 'generated_images/main.png'

        saved = task_dir / 'generated_images' / 'main.png'
        assert saved.is_file()
        assert saved.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'

        # A generating-in-progress event fires between confirm and the
        # finished image, so the UI can show a real "generating…" state.
        gen_ing = await _drain_until(queue, 'image_generating')
        assert gen_ing['request_id'] == request_id
        assert gen_ing['model'] == 'nano-banana-pro'

        gen_evt = await _drain_until(queue, 'image_generated')
        assert gen_evt['request_id'] == request_id
        assert (
            gen_evt['url']
            == f'/api/tasks/{tid}/files/generated_images/main.png'
        )

        # The image is persisted as a message so it re-renders on reload
        # (image_generated alone is a live-only SSE event).
        msgs = (await admin_client.get(f'/api/tasks/{tid}/messages')).json()
        gen_msgs = [m for m in msgs if m['role'] == 'generated_image']
        assert len(gen_msgs) == 1
        payload = json.loads(gen_msgs[0]['content'])
        assert payload['path'] == 'generated_images/main.png'
        assert payload['url'] == (
            f'/api/tasks/{tid}/files/generated_images/main.png'
        )
    finally:
        event_bus.unsubscribe(queue)
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_no_pending_image_request(admin_client):
    r = await admin_client.get(f'/api/tasks/{uuid.uuid4()}/image/pending')
    assert r.status_code == 200
    assert r.json() == {'pending': False}


async def test_pending_image_request_recoverable(admin_client, monkeypatch):
    """A client that opens the task AFTER image_request fired can recover
    the confirm card via GET …/image/pending, then confirm it."""
    monkeypatch.setenv('VISION_FAKE', '1')
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    queue = event_bus.subscribe()
    try:
        gen = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={
                    'prompt': '主图：纯白背景',
                    'model': 'nano-banana-pro',
                    'reference_images': ['uploads/ref.png'],
                    'output_name': 'main.png',
                    'kind': 'main',
                },
            )
        )
        req = await _drain_until(queue, 'image_request')
        rid = req['request_id']

        # Recover the card (no SSE replay — this is the reconnect path).
        p = (await admin_client.get(f'/api/tasks/{tid}/image/pending')).json()
        assert p['pending'] is True
        assert p['request_id'] == rid
        assert p['prompt'] == '主图：纯白背景'
        assert p['reference_images'] == ['uploads/ref.png']
        assert p['kind'] == 'main'
        assert 'nano-banana-pro' in p['models']

        # Confirming it clears the pending state.
        c = await admin_client.post(
            f'/api/tasks/{tid}/image/confirm',
            json={'request_id': rid, 'action': 'confirm'},
        )
        assert c.json()['ok'] is True
        await asyncio.wait_for(gen, timeout=10)
        p2 = (await admin_client.get(f'/api/tasks/{tid}/image/pending')).json()
        assert p2 == {'pending': False}
    finally:
        event_bus.unsubscribe(queue)
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_upload_reference_saves_file(admin_client):
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    try:
        r = await admin_client.post(
            f'/api/tasks/{tid}/image/upload-reference',
            files={
                'file': ('my ref.png', b'\x89PNG\r\n\x1a\nfake', 'image/png')
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body['path'].startswith('generated_images/refs/')
        assert (task_dir / body['path']).is_file()
        assert body['url'] == f'/api/tasks/{tid}/files/{body["path"]}'
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_added_references_merged_into_generation(
    admin_client, monkeypatch
):
    monkeypatch.setenv('VISION_FAKE', '1')
    captured = {}

    async def _fake_gen(*, prompt, model, reference_images, task_dir, **kw):
        captured['refs'] = reference_images
        return b'\x89PNG\r\n\x1a\nx'

    monkeypatch.setattr(vision, 'generate_image', _fake_gen)

    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    queue = event_bus.subscribe()
    try:
        gen = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={
                    'prompt': 'p',
                    'model': 'nano-banana-pro',
                    'reference_images': ['https://example.com/a.jpg'],
                },
            )
        )
        req = await _drain_until(queue, 'image_request')
        await admin_client.post(
            f'/api/tasks/{tid}/image/confirm',
            json={
                'request_id': req['request_id'],
                'action': 'confirm',
                'added_references': ['generated_images/refs/mine.png'],
            },
        )
        await asyncio.wait_for(gen, timeout=10)
        # Agent's reference plus the user-added one both reach generation.
        assert captured['refs'] == [
            'https://example.com/a.jpg',
            'generated_images/refs/mine.png',
        ]
    finally:
        event_bus.unsubscribe(queue)
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_new_request_supersedes_pending(admin_client, monkeypatch):
    """Single-pending-per-task: a second generate for the same task
    resolves the first as 'superseded' and emits image_request_expired
    for the old card. Confirms never time out otherwise."""
    monkeypatch.setenv('VISION_FAKE', '1')
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    queue = event_bus.subscribe()
    try:
        gen1 = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={'prompt': 'first', 'model': 'nano-banana-pro'},
            )
        )
        req1 = await _drain_until(queue, 'image_request')

        gen2 = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={'prompt': 'second', 'model': 'nano-banana-pro'},
            )
        )
        expired = await _drain_until(queue, 'image_request_expired')
        assert expired['request_id'] == req1['request_id']

        body1 = (await asyncio.wait_for(gen1, timeout=10)).json()
        assert body1['status'] == 'superseded'

        req2 = await _drain_until(queue, 'image_request')
        assert req2['prompt'] == 'second'
        await admin_client.post(
            f'/api/tasks/{tid}/image/confirm',
            json={'request_id': req2['request_id'], 'action': 'confirm'},
        )
        body2 = (await asyncio.wait_for(gen2, timeout=10)).json()
        assert body2['status'] == 'ok'
    finally:
        event_bus.unsubscribe(queue)
        shutil.rmtree(task_dir, ignore_errors=True)


# Chat-attachment upload coverage moved to
# tests/workflow/test_wf_chat_attachments.py (stage-until-send redesign).


async def test_ref_proxy_rejects_bad_and_private_urls(admin_client):
    # Non-http scheme is rejected.
    r = await admin_client.get(
        '/api/vision/ref-proxy', params={'url': 'ftp://x/y'}
    )
    assert r.status_code == 400
    # SSRF guard: loopback/private hosts are rejected.
    for host in ('127.0.0.1', 'localhost', '10.0.0.5', '169.254.1.1'):
        rp = await admin_client.get(
            '/api/vision/ref-proxy',
            params={'url': f'http://{host}/x.png'},
        )
        assert rp.status_code == 400, host


async def test_cancel_flow_writes_nothing(admin_client, monkeypatch):
    monkeypatch.setenv('VISION_FAKE', '1')
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    queue = event_bus.subscribe()
    try:
        gen = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={'prompt': 'x', 'model': 'nano-banana-pro'},
            )
        )
        req = await _drain_until(queue, 'image_request')
        await admin_client.post(
            f'/api/tasks/{tid}/image/confirm',
            json={'request_id': req['request_id'], 'action': 'cancel'},
        )
        body = (await asyncio.wait_for(gen, timeout=10)).json()
        assert body['status'] == 'cancelled'
        assert not (task_dir / 'generated_images').exists()
    finally:
        event_bus.unsubscribe(queue)
        shutil.rmtree(task_dir, ignore_errors=True)
