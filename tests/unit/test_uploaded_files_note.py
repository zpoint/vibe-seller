"""The create-time / uploaded-files note surfaced to the agent prompt.

Regression: an image uploaded when creating a task never reached the
agent (it went to a DB blob dir, and its path was never in the prompt),
so the agent asked "where is the image?". Now uploads land in the task
workspace ``uploads/`` and their paths are surfaced here.
"""

from types import SimpleNamespace

from PIL import Image
import pytest

import app.task_runner_context as trc
from app.workspace import upload_tiles as ut

pytestmark = pytest.mark.unit


def _task(tid='t-abc'):
    return SimpleNamespace(id=tid)


def test_lists_uploaded_files(tmp_path, monkeypatch):
    monkeypatch.setattr(trc, 'VIBE_SELLER_DIR', tmp_path)
    up = tmp_path / 'tasks' / 't-abc' / 'uploads'
    up.mkdir(parents=True)
    (up / 'photo.jpg').write_bytes(b'\x89PNG')
    (up / 'doc.pdf').write_bytes(b'%PDF')

    note = trc.uploaded_files_note(_task())
    assert str(up / 'photo.jpg') in note
    assert str(up / 'doc.pdf') in note
    # Instructs the agent to read them, not re-ask.
    assert 'do NOT ask' in note or 'do not ask' in note.lower()


def test_empty_when_no_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(trc, 'VIBE_SELLER_DIR', tmp_path)
    # No uploads dir at all.
    assert trc.uploaded_files_note(_task()) == ''
    # Uploads dir exists but empty.
    (tmp_path / 'tasks' / 't-abc' / 'uploads').mkdir(parents=True)
    assert trc.uploaded_files_note(_task()) == ''


def _tall_page(path, w=600, h=6000):
    Image.new('RGB', (w, h), 'white').save(path)


def test_tall_image_is_tiled_and_tiles_listed(tmp_path, monkeypatch):
    # A supplier detail page read whole shrinks until no text survives;
    # the note must hand the agent readable tiles, in order.
    monkeypatch.setattr(trc, 'VIBE_SELLER_DIR', tmp_path)
    up = tmp_path / 'tasks' / 't-abc' / 'uploads'
    up.mkdir(parents=True)
    _tall_page(up / 'detail.jpg')

    note = trc.uploaded_files_note(_task())
    tiles = sorted((up / '.tiles' / 'detail').glob('part*.jpg'))
    assert len(tiles) >= 4
    assert note.index(str(tiles[0])) < note.index(str(tiles[-1]))
    assert 'Read ALL' in note
    # The tile dir is not itself listed as an upload.
    assert f'- {up / ".tiles"}' not in note.splitlines()


def test_tiles_cover_the_whole_page_with_overlap(tmp_path):
    page = tmp_path / 'p.png'
    _tall_page(page, w=2000, h=20000)
    tiles = ut.tiles_for(page)
    # Downscaled to the tile width; consecutive tiles overlap, and the
    # last one reaches the bottom of the page.
    sizes = [Image.open(t).size for t in tiles]
    assert all(w == ut.TILE_WIDTH for w, _ in sizes)
    scale = ut.TILE_WIDTH / 2000
    covered = (len(tiles) - 1) * (ut.TILE_HEIGHT - ut.OVERLAP) + sizes[-1][1]
    assert covered >= 20000 * scale - 2
    # Second call reuses the tiles instead of re-cutting them.
    mtimes = [t.stat().st_mtime for t in tiles]
    assert ut.tiles_for(page) == tiles
    assert [t.stat().st_mtime for t in tiles] == mtimes


def test_ordinary_photo_and_non_image_are_not_tiled(tmp_path):
    photo = tmp_path / 'photo.jpg'
    _tall_page(photo, w=1000, h=1500)
    assert ut.tiles_for(photo) == []
    doc = tmp_path / 'spec.pdf'
    doc.write_bytes(b'%PDF')
    assert ut.tiles_for(doc) == []
    broken = tmp_path / 'broken.jpg'
    broken.write_bytes(b'not an image')
    assert ut.tiles_for(broken) == []
