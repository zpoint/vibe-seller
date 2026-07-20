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
    assert vision.DEFAULT_MODEL in vision.MODELS
    assert 'nano-banana-pro' in vision.MODELS
    assert 'nano-banana-2' in vision.MODELS


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
        model='nano-banana-pro',
        reference_images=[],
        task_dir=None,  # unused in fake mode
    )
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
