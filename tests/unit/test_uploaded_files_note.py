"""The create-time / uploaded-files note surfaced to the agent prompt.

Regression: an image uploaded when creating a task never reached the
agent (it went to a DB blob dir, and its path was never in the prompt),
so the agent asked "where is the image?". Now uploads land in the task
workspace ``uploads/`` and their paths are surfaced here.
"""

from types import SimpleNamespace

import pytest

import app.task_runner_context as trc

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
