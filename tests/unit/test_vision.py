"""Unit tests for the vision image-gen config + confirm registry."""

import pytest

from app import vision

pytestmark = pytest.mark.unit


def test_save_load_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, 'VISION_CONFIG_PATH', tmp_path / 'vision.json')
    monkeypatch.delenv('KIE_API_KEY', raising=False)

    assert vision.get_kie_api_key() is None
    assert vision.mask_key(None) == ''

    vision.save_vision_config('sk-abcdef1234')
    assert vision.get_kie_api_key() == 'sk-abcdef1234'
    masked = vision.mask_key(vision.get_kie_api_key())
    assert masked.endswith('1234')
    assert 'abcdef' not in masked  # never leaks the body

    # Empty string clears it.
    vision.save_vision_config('')
    assert vision.get_kie_api_key() is None


def test_env_key_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, 'VISION_CONFIG_PATH', tmp_path / 'vision.json')
    vision.save_vision_config('file-key')
    monkeypatch.setenv('KIE_API_KEY', 'env-key')
    assert vision.get_kie_api_key() == 'env-key'


def test_is_fake(monkeypatch):
    monkeypatch.setenv('VISION_FAKE', '1')
    assert vision.is_fake() is True
    monkeypatch.setenv('VISION_FAKE', '0')
    assert vision.is_fake() is False
    monkeypatch.delenv('VISION_FAKE', raising=False)
    assert vision.is_fake() is False


def test_models_registry():
    ids = vision.model_ids()
    # Default is the first flattened variant (nano-banana-pro @ 2K).
    assert vision.DEFAULT_MODEL == 'nano-banana-pro-2k'
    assert ids[0] == vision.DEFAULT_MODEL
    # Tier variants exist for the multi-resolution models.
    assert 'gpt-image-2-1k' in ids
    assert 'gpt-image-2-4k' in ids
    assert 'nano-banana-2-2k' in ids
    # Ids are unique; every entry has a slug, a valid reference-image
    # field, and a positive price. Tiers of one model share a slug (that
    # is expected) but their ids differ.
    assert len(ids) == len(set(ids))
    for mid in ids:
        m = vision.get_model(mid)
        assert m.slug
        assert m.ref_field in (
            'image_input',
            'input_urls',
            'image_urls',
            'image_url',
        )
        assert m.usd > 0
    # An unknown id falls back to the default.
    assert vision.get_model('does-not-exist').id == vision.DEFAULT_MODEL


def test_tier_variants_carry_their_param():
    """Each tier variant must inject its own kie param (resolution /
    quality / rendering_speed) so generation targets that tier — this is
    what makes the per-tier prices real rather than cosmetic."""
    assert vision.get_model('gpt-image-2-4k').extra.get('resolution') == '4K'
    assert vision.get_model('gpt-image-2-1k').extra.get('resolution') == '1K'
    assert vision.get_model('seedream-5-pro-high').extra.get('quality') == (
        'high'
    )
    assert (
        vision.get_model('ideogram-v3-remix-turbo').extra.get('rendering_speed')
        == 'TURBO'
    )
    # Prices differ across tiers of one model.
    assert (
        vision.get_model('gpt-image-2-4k').usd
        > vision.get_model('gpt-image-2-1k').usd
    )


def test_catalog_public_shape():
    cat = vision.catalog_public()
    assert len(cat) == len(vision.model_ids())
    first = cat[0]
    assert set(first) == {'id', 'provider', 'label', 'usd', 'cny', 'default'}
    assert first['default'] is True
    # CNY is the fixed-rate conversion of USD.
    assert first['cny'] == round(first['usd'] * vision.USD_CNY, 2)
    # Exactly one default.
    assert sum(1 for m in cat if m['default']) == 1


class _FakeResp:
    def __init__(self, payload=None, content=b'PNG'):
        self._p = payload
        self.content = content

    def json(self):
        return self._p


class _FakeKieClient:
    """Records the createTask body and returns a successful poll +
    a stub image download, so ``generate_image`` runs end-to-end offline."""

    last_body: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeKieClient.last_body = json
        return _FakeResp({'data': {'taskId': 'x'}})

    async def get(self, url, params=None, headers=None):
        if 'recordInfo' in url:
            return _FakeResp({
                'data': {
                    'state': 'success',
                    'resultJson': '{"resultUrls": ["http://img/out.png"]}',
                }
            })
        return _FakeResp(content=b'PNG')  # image download


async def test_generate_image_builds_per_model_input(monkeypatch, tmp_path):
    """The reference field name/cardinality must follow the model spec —
    an array field for nano/gpt/seedream/flux, a single string for qwen/
    ideogram — so a non-nano model actually receives its references
    instead of silently dropping them."""
    monkeypatch.setattr(vision.httpx, 'AsyncClient', _FakeKieClient)
    monkeypatch.setattr(vision, 'get_kie_api_key', lambda: 'k')

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(vision.asyncio, 'sleep', _no_sleep)
    monkeypatch.setenv('VISION_FAKE', '0')

    refs = ['http://a/1.png', 'http://a/2.png']

    # Single-reference model → plain string, primary ref only.
    await vision.generate_image(
        prompt='p',
        model='qwen-image-edit',
        reference_images=refs,
        task_dir=tmp_path,
    )
    body = _FakeKieClient.last_body
    assert body['model'] == 'qwen/image-edit'
    assert body['input']['image_url'] == 'http://a/1.png'

    # Array-reference model + tier → full list under the model's own
    # field name, and the tier's resolution param is injected.
    await vision.generate_image(
        prompt='p',
        model='gpt-image-2-4k',
        reference_images=refs,
        task_dir=tmp_path,
    )
    body = _FakeKieClient.last_body
    assert body['model'] == 'gpt-image-2-image-to-image'
    assert body['input']['input_urls'] == refs
    assert body['input']['resolution'] == '4K'


async def test_confirm_registry_resolve():
    req = 'req-1'
    fut = vision.create_confirm(req, 'task-1')
    assert not fut.done()
    ok = vision.resolve_confirm(req, {'action': 'confirm', 'prompt': 'p'})
    assert ok is True
    assert (await fut) == {'action': 'confirm', 'prompt': 'p'}
    # Second resolve is a no-op (already done).
    assert vision.resolve_confirm(req, {'action': 'cancel'}) is False
    vision.discard_confirm(req, 'task-1')


async def test_confirm_registry_unknown():
    assert vision.resolve_confirm('nope', {'action': 'confirm'}) is False


async def test_confirm_discard():
    req = 'req-2'
    vision.create_confirm(req, 'task-2')
    vision.discard_confirm(req, 'task-2')
    assert vision.resolve_confirm(req, {'action': 'confirm'}) is False


async def test_supersede_pending():
    """A new request for the same task resolves the old one as
    superseded; other tasks are untouched."""
    fut_a = vision.create_confirm('req-a', 'task-s')
    fut_other = vision.create_confirm('req-o', 'task-other')

    old = vision.supersede_pending('task-s')
    assert old == 'req-a'
    assert (await fut_a) == {'action': 'superseded'}
    assert not fut_other.done()

    # Nothing pending anymore for task-s after discard.
    vision.discard_confirm('req-a', 'task-s')
    assert vision.supersede_pending('task-s') is None
    vision.discard_confirm('req-o', 'task-other')


async def test_fake_png_is_png(monkeypatch):
    monkeypatch.setenv('VISION_FAKE', '1')
    data = await vision.generate_image(
        prompt='test',
        model='nano-banana-pro-2k',
        reference_images=[],
        task_dir=None,  # unused in fake mode
    )
    assert data[:8] == b'\x89PNG\r\n\x1a\n'


def test_poll_budget_is_per_resolution_tier():
    """A flat budget sized for 2K kills 4K jobs.

    Measured live (2026-07, nano-banana-pro, same prompt + reference):
    2K ~60s, 4K 147s on one run and past 240s on another — which the old
    flat 60x4s budget killed at 4m17s. 4K's tail is what needs covering.
    """
    assert vision.poll_budget_s(vision.get_model('nano-banana-pro-2k')) == 240
    assert vision.poll_budget_s(vision.get_model('nano-banana-pro-4k')) == 600
    assert vision.poll_budget_s(vision.get_model('gpt-image-2-4k')) == 600
    assert vision.poll_budget_s(vision.get_model('gpt-image-2-1k')) == 240
    # Quality/speed-tier models carry no `resolution` — they get the
    # default rather than a KeyError.
    speed_tier = vision.get_model('ideogram-v3-remix-turbo')
    assert 'resolution' not in speed_tier.extra
    assert vision.poll_budget_s(speed_tier) == 240


def test_every_model_has_a_poll_budget():
    """No model may fall through to an exception on budget lookup."""
    for m in vision.IMAGE_MODELS:
        assert vision.poll_budget_s(m) >= 240


class _StuckKieClient(_FakeKieClient):
    """createTask succeeds, but the job never leaves ``waiting`` — the
    shape that produced the real 4m17s timeout."""

    async def get(self, url, params=None, headers=None):
        if 'recordInfo' in url:
            _StuckKieClient.polled_task = (params or {}).get('taskId')
            return _FakeResp({'data': {'state': 'waiting'}})
        return _FakeResp(content=b'PNG')


async def test_timeout_carries_the_kie_task_id(monkeypatch, tmp_path):
    """Giving up does not cancel the job — it finishes and is billed.

    So the taskId must survive the exception, or the retry pays twice for
    an image the first call already bought.
    """
    monkeypatch.setattr(vision.httpx, 'AsyncClient', _StuckKieClient)
    monkeypatch.setattr(vision, 'get_kie_api_key', lambda: 'k')

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(vision.asyncio, 'sleep', _no_sleep)
    monkeypatch.setenv('VISION_FAKE', '0')

    with pytest.raises(vision.GenerationTimeout) as exc:
        await vision.generate_image(
            prompt='p',
            model='nano-banana-pro-4k',
            reference_images=[],
            task_dir=tmp_path,
        )
    assert exc.value.kie_task_id == 'x'
    assert exc.value.waited_s == 600
    assert exc.value.model_id == 'nano-banana-pro-4k'
    # Still a RuntimeError, so existing except-clauses keep working.
    assert isinstance(exc.value, RuntimeError)


async def test_resume_polls_existing_job_without_recreating(
    monkeypatch, tmp_path
):
    """The resume path must NOT call createTask — that is the second
    charge it exists to avoid."""
    monkeypatch.setattr(vision.httpx, 'AsyncClient', _FakeKieClient)
    monkeypatch.setattr(vision, 'get_kie_api_key', lambda: 'k')

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(vision.asyncio, 'sleep', _no_sleep)
    monkeypatch.setenv('VISION_FAKE', '0')

    _FakeKieClient.last_body = {'sentinel': 'not-overwritten'}
    data = await vision.generate_image(
        prompt='p',
        model='nano-banana-pro-4k',
        reference_images=['http://a/1.png'],
        task_dir=tmp_path,
        resume_task_id='prior-job-42',
    )
    assert data == b'PNG'
    # createTask was never POSTed.
    assert _FakeKieClient.last_body == {'sentinel': 'not-overwritten'}
