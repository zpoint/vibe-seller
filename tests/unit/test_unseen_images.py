"""Images the task's model cannot see are said so before it answers.

A text-only model (per the catalog) cannot see an attached picture: the
agent used to be told "Read them directly", the Read was refused by the
image guard, and the user learned it only from the reply — if at all.
The backend now writes a ``notice`` for the user and tells the agent not
to Read the images. An unknown model restricts nothing.
"""

import json
from types import SimpleNamespace

import pytest

import app.ai.unseen_images as ui
import app.task_runner_context as trc

pytestmark = pytest.mark.unit

_CAPS = {'text-model': False, 'vision-model': True}


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr(ui, 'model_sees_images', lambda m: _CAPS.get(m))


def test_notice_only_for_images_a_text_only_model_cannot_see():
    names = ['shot.png', 'photo.JPG', 'spec.pdf']
    notice = json.loads(ui.images_notice('text-model', names))
    assert notice == {
        'kind': 'images_unseen',
        'model': 'text-model',
        'files': ['shot.png', 'photo.JPG'],
    }
    assert ui.images_notice('vision-model', names) is None
    assert ui.images_notice('unlisted-model', names) is None
    assert ui.images_notice(None, names) is None
    assert ui.images_notice('text-model', ['spec.pdf']) is None


def test_chat_lines_tell_the_agent_not_to_read_unseen_images(monkeypatch):
    monkeypatch.setattr(ui, 'profile_model', lambda pid: 'text-model')
    lines, notice = ui.chat_attachment_lines(
        'p',
        [
            {'abs_path': '/w/uploads/shot.png', 'filename': 'shot.png'},
            {'abs_path': '/w/uploads/spec.pdf', 'filename': 'spec.pdf'},
        ],
    )
    assert 'cannot see images' in lines.splitlines()[0]
    assert lines.splitlines()[1] == 'Attached file: /w/uploads/spec.pdf'
    assert json.loads(notice)['files'] == ['shot.png']

    monkeypatch.setattr(ui, 'profile_model', lambda pid: 'vision-model')
    lines, notice = ui.chat_attachment_lines(
        'p', [{'abs_path': '/w/uploads/shot.png', 'filename': 'shot.png'}]
    )
    assert lines == 'Attached file: /w/uploads/shot.png'
    assert notice is None


def _uploads(tmp_path, monkeypatch, *names):
    monkeypatch.setattr(trc, 'VIBE_SELLER_DIR', tmp_path)
    up = tmp_path / 'tasks' / 't-abc' / 'uploads'
    up.mkdir(parents=True)
    for n in names:
        (up / n).write_bytes(b'x')
    return up


def test_prompt_note_on_a_text_only_model(tmp_path, monkeypatch):
    up = _uploads(tmp_path, monkeypatch, 'shot.png', 'spec.pdf')
    monkeypatch.setattr(trc, 'profile_model', lambda pid: 'text-model')
    note = trc.uploaded_files_note(SimpleNamespace(id='t-abc'))

    read_part, blind_part = note.split('\n\n', 1)
    # The PDF is still to be read; the image is listed apart, not to be
    # Read, and the agent knows the user was told.
    assert str(up / 'spec.pdf') in read_part
    assert str(up / 'shot.png') not in read_part
    assert str(up / 'shot.png') in blind_part
    assert 'Do NOT Read them' in blind_part
    assert 'already been told' in blind_part


def test_prompt_note_with_only_unseen_images(tmp_path, monkeypatch):
    up = _uploads(tmp_path, monkeypatch, 'shot.png')
    monkeypatch.setattr(trc, 'profile_model', lambda pid: 'text-model')
    note = trc.uploaded_files_note(SimpleNamespace(id='t-abc'))
    assert 'Read them directly' not in note
    assert str(up / 'shot.png') in note


def test_prompt_note_unchanged_on_a_vision_model(tmp_path, monkeypatch):
    up = _uploads(tmp_path, monkeypatch, 'shot.png')
    monkeypatch.setattr(trc, 'profile_model', lambda pid: 'vision-model')
    note = trc.uploaded_files_note(SimpleNamespace(id='t-abc'))
    assert 'Read them directly' in note
    assert str(up / 'shot.png') in note
    assert 'cannot see' not in note
