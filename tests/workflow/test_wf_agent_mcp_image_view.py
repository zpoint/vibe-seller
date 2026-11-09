"""Workflow tests: an external agent can SEE, choose and add to an
image confirm card, and see the image it produced — over ``/api/mcp``.

What the web card lets a user do, the MCP must let an operator do:

* look at the reference images the task agent attached (MCP ``image``
  content blocks — base64 inside JSON text is opaque to a client);
* pick a model from the card — an unknown id is an ERROR, never a
  silent fallback to the agent's proposal; a retired id maps to its
  successor and says so;
* add references: task files, URLs, or images uploaded from its side,
  which reach the generator exactly as the card's own uploads do;
* see the generated image (``task_files`` read → image block), even
  when kie.ai's WEBP sits under a ``.png`` name.

Generation runs in ``VISION_FAKE`` mode (offline, deterministic).
"""

import asyncio
import base64
import json
import shutil
import uuid

import pytest

from app import vision
from app.events.bus import event_bus
from app.workspace.manager import VIBE_SELLER_DIR

pytestmark = pytest.mark.workflow

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'
PNG_1PX = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m'
    'NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)
# A minimal RIFF/WEBP header: enough for the magic-byte sniffer.
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 24


async def call_tool(client, name, arguments):
    r = await client.post(
        '/api/mcp',
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()['result']
    text = result['content'][0]['text']
    data = {'error': text} if result.get('isError') else json.loads(text)
    return data, result


def images_of(envelope) -> list[dict]:
    return [c for c in envelope['content'] if c['type'] == 'image']


async def _wait_card(queue):
    async def _loop():
        while True:
            data = json.loads(await queue.get())
            if data.get('type') == 'image_request':
                return data

    return await asyncio.wait_for(_loop(), 5)


@pytest.fixture
def card(admin_client, monkeypatch):
    """Start a parked generation for a fresh task; yields a helper."""
    monkeypatch.setenv('VISION_FAKE', '1')
    state = {'queue': event_bus.subscribe(), 'dirs': [], 'gens': []}

    async def open_card(**body):
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        state['dirs'].append(task_dir)
        (task_dir / 'refs').mkdir(parents=True)
        (task_dir / 'refs' / 'bottle.png').write_bytes(PNG_1PX)
        gen = asyncio.create_task(
            admin_client.post(
                f'/api/tasks/{tid}/image/generate',
                json={'prompt': 'white background', **body},
            )
        )
        state['gens'].append(gen)
        await _wait_card(state['queue'])
        return tid, task_dir, gen

    yield open_card
    event_bus.unsubscribe(state['queue'])
    for gen in state['gens']:
        gen.cancel()
    for d in state['dirs']:
        shutil.rmtree(d, ignore_errors=True)


async def test_get_shows_the_reference_images(admin_client, card):
    tid, _, gen = await card(
        reference_images=['refs/bottle.png', 'https://example.com/a.png']
    )
    got, env = await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'get'}
    )
    # The workspace reference is SHOWN, as a real image block…
    shown = images_of(env)
    assert len(shown) == 1
    assert shown[0]['mimeType'] == 'image/png'
    assert base64.b64decode(shown[0]['data']) == PNG_1PX
    # …the URL is listed, not fetched by the server.
    assert got['references'] == [
        {'ref': 'refs/bottle.png', 'shown': 1},
        {
            'ref': 'https://example.com/a.png',
            'url': 'https://example.com/a.png',
        },
    ]
    # Every selectable model comes with its price.
    assert all('usd' in m for m in got['models'])
    assert '_images' not in got
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
    )
    await asyncio.wait_for(gen, 10)


async def test_unknown_model_is_an_error_and_the_card_stays(admin_client, card):
    tid, _, gen = await card(model='nano-banana-pro-2k')
    _, env = await call_tool(
        admin_client,
        'image_request',
        {'task_id': tid, 'action': 'confirm', 'model': 'no-such-model'},
    )
    assert env['isError'] is True
    text = env['content'][0]['text']
    assert 'no-such-model' in text and 'flux-3-klein-1k' in text
    still, _ = await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'get'}
    )
    assert still['pending'] is True
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
    )
    await asyncio.wait_for(gen, 10)


async def test_retired_model_confirms_its_successor(admin_client, card):
    tid, _, gen = await card(model='nano-banana-pro-2k')
    done, env = await call_tool(
        admin_client,
        'image_request',
        {'task_id': tid, 'action': 'confirm', 'model': 'gpt-image-2-2k'},
    )
    assert env['isError'] is False, env
    assert done['model'] == 'gpt-image-2-5-2k'
    assert 'retired' in done['notes'][0]
    body = (await asyncio.wait_for(gen, 10)).json()
    assert body['model'] == 'gpt-image-2-5-2k'


async def test_added_and_uploaded_references_reach_the_generator(
    admin_client, card, monkeypatch
):
    seen = {}
    real = vision.generate_image

    async def spy(**kw):
        seen['refs'] = kw['reference_images']
        return await real(**kw)

    monkeypatch.setattr(vision, 'generate_image', spy)
    tid, task_dir, gen = await card(reference_images=['refs/bottle.png'])
    done, env = await call_tool(
        admin_client,
        'image_request',
        {
            'task_id': tid,
            'action': 'confirm',
            'add_references': ['https://example.com/side.png'],
            'reference_uploads': [
                {
                    'filename': 'from-user.png',
                    'mime_type': 'image/png',
                    'content_base64': base64.b64encode(PNG_1PX).decode(),
                }
            ],
        },
    )
    assert env['isError'] is False, env
    await asyncio.wait_for(gen, 10)
    uploaded = done['added_references'][1]
    assert seen['refs'] == [
        'refs/bottle.png',
        'https://example.com/side.png',
        uploaded,
    ]
    # Saved through the card's own upload route, inside the task.
    assert (task_dir / uploaded).read_bytes() == PNG_1PX


async def test_bad_added_references_are_refused(admin_client, card):
    tid, _, gen = await card()
    for args, needle in (
        ({'add_references': ['../../etc/passwd']}, '..'),
        ({'add_references': ['refs/missing.png']}, 'not a file'),
        (
            {
                'reference_uploads': [
                    {
                        'filename': 'notes.txt',
                        'mime_type': 'text/plain',
                        'content_base64': base64.b64encode(b'hi').decode(),
                    }
                ]
            },
            'not allowed',  # the shared upload allow-list, first
        ),
        (
            {
                'reference_uploads': [
                    {
                        'filename': 'spec.pdf',
                        'mime_type': 'application/pdf',
                        'content_base64': base64.b64encode(
                            b'%PDF-1.4'
                        ).decode(),
                    }
                ]
            },
            'not an image',  # an allowed upload, but not a reference
        ),
    ):
        _, env = await call_tool(
            admin_client,
            'image_request',
            {'task_id': tid, 'action': 'confirm', **args},
        )
        assert env['isError'] is True, args
        assert needle in env['content'][0]['text']
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
    )
    await asyncio.wait_for(gen, 10)


async def test_the_generated_image_is_shown(admin_client, card):
    tid, task_dir, gen = await card(output_name='hero.png')
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'confirm'}
    )
    await asyncio.wait_for(gen, 10)

    listed, _ = await call_tool(
        admin_client, 'task_files', {'task_id': tid, 'action': 'list'}
    )
    assert 'generated_images/hero.png' in {f['name'] for f in listed['files']}
    read, env = await call_tool(
        admin_client,
        'task_files',
        {'task_id': tid, 'action': 'read', 'path': 'generated_images/hero.png'},
    )
    assert read['shown_as_image'] is True
    assert '/api/tasks/' in read['url']  # still downloadable
    [img] = images_of(env)
    assert base64.b64decode(img['data'])[:8] == b'\x89PNG\r\n\x1a\n'

    # kie.ai hands back WEBP under the .png name: shown as what it IS.
    (task_dir / 'generated_images' / 'remix.png').write_bytes(WEBP)
    _, env = await call_tool(
        admin_client,
        'task_files',
        {
            'task_id': tid,
            'action': 'read',
            'path': 'generated_images/remix.png',
        },
    )
    assert images_of(env)[0]['mimeType'] == 'image/webp'


async def test_too_many_references_are_refused_before_any_upload(
    admin_client, card
):
    """The count is checked first — a refused call leaves no orphan
    uploads in the task — and it includes the card's own references."""
    tid, task_dir, gen = await card(reference_images=['refs/bottle.png'] * 13)
    before = sorted(p.name for p in task_dir.rglob('*') if p.is_file())
    upload = {
        'filename': 'extra.png',
        'mime_type': 'image/png',
        'content_base64': base64.b64encode(PNG_1PX).decode(),
    }
    _, env = await call_tool(
        admin_client,
        'image_request',
        {
            'task_id': tid,
            'action': 'confirm',
            'reference_uploads': [upload, upload],
        },
    )
    assert env['isError'] is True
    assert 'at most 14' in env['content'][0]['text']
    after = sorted(p.name for p in task_dir.rglob('*') if p.is_file())
    assert after == before
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
    )
    await asyncio.wait_for(gen, 10)


async def test_reference_images_share_one_reply_budget(
    admin_client, card, monkeypatch
):
    """A client forwards the reply to its model; once the per-reply
    image budget is spent, references are listed, not shown."""
    from app.agent_mcp import tools_task_files  # noqa: PLC0415

    monkeypatch.setattr(
        tools_task_files, 'MAX_REPLY_IMAGE_BYTES', len(PNG_1PX) + 10
    )
    tid, _, gen = await card(reference_images=['refs/bottle.png'] * 3)
    got, env = await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'get'}
    )
    assert len(images_of(env)) == 1
    assert got['references'][0]['shown'] == 1
    assert all('not_shown' in r for r in got['references'][1:])
    await call_tool(
        admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
    )
    await asyncio.wait_for(gen, 10)


async def test_a_fresh_proposal_never_names_a_hidden_model(
    admin_client, card, tmp_path, monkeypatch
):
    """A task's tool enum can predate the sync that hid a model. A NEW
    card must not propose it (the card lists and prices visible models
    only, and kie.ai would reject the slug) — but a 504 resume of a job
    already in flight keeps its model, and the card still lists it."""
    from app import vision_catalog as vc  # noqa: PLC0415
    from app.routers import vision as vision_router  # noqa: PLC0415

    monkeypatch.setattr(vc, '_cache_path', lambda: tmp_path / 'vc.json')
    vc.reset()
    try:
        vc._commit(hidden=frozenset({'flux-3/klein-edit-image'}))
        tid, _, gen = await card(model='flux-3-klein-2k')
        got, _ = await call_tool(
            admin_client, 'image_request', {'task_id': tid, 'action': 'get'}
        )
        assert got['model'] == vision.default_model()
        assert 'flux-3-klein-2k' not in {m['id'] for m in got['models']}
        await call_tool(
            admin_client, 'image_request', {'task_id': tid, 'action': 'cancel'}
        )
        await asyncio.wait_for(gen, 10)

        tid2 = str(uuid.uuid4())
        vision_router._INFLIGHT[tid2] = ('kie-job-1', 'flux-3-klein-2k')
        try:
            queue = event_bus.subscribe()
            gen2 = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid2}/image/generate',
                    json={'prompt': 'resume', 'model': 'flux-3-klein-2k'},
                )
            )
            await _wait_card(queue)
            event_bus.unsubscribe(queue)
            got2, _ = await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid2, 'action': 'get'},
            )
            assert got2['model'] == 'flux-3-klein-2k'
            assert 'flux-3-klein-2k' in {m['id'] for m in got2['models']}
            await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid2, 'action': 'cancel'},
            )
            await asyncio.wait_for(gen2, 10)
        finally:
            vision_router._INFLIGHT.pop(tid2, None)
            shutil.rmtree(_TASKS_DIR / tid2, ignore_errors=True)
    finally:
        vc.reset()
