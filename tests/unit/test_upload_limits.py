"""The upload cap is one number, enforced while streaming.

Two defects this pins:

1. **Three caps, already drifted.** Create-task attachments allowed
   10MB, chat staging 15MB, and the browser hardcoded a third copy —
   same allow-list, different ceilings. A 10.16MB product image was
   refused by the New Task dialog and accepted by the chat box of the
   task it had just created.

2. **The cap protected nothing.** Both routes ran
   ``content = await file.read()`` and only then compared
   ``len(content)`` to the limit, so an over-sized body was fully
   materialised in memory before anything decided to reject it. The
   number was a policy, not a defence.
"""

from pathlib import Path

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pytest

from app.main import app as fastapi_app
import app.routers.attachments as attachments_router
from app.uploads import (
    MAX_UPLOAD_SIZE,
    UploadBodyLimitMiddleware,
    declared_body_too_large,
    human_size,
    reject_disallowed_type,
    save_upload,
)

pytestmark = pytest.mark.unit


class _FakeUpload:
    """An UploadFile stand-in that reports how much was pulled from it."""

    def __init__(self, data: bytes, content_type: str = 'image/png'):
        self._data = data
        self._pos = 0
        self.content_type = content_type
        self.filename = 'x.png'
        self.bytes_read = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
        else:
            chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        self.bytes_read += len(chunk)
        return chunk


async def test_saves_a_file_under_the_cap(tmp_path: Path):
    up = _FakeUpload(b'abcdef')
    written = await save_upload(up, tmp_path / 'a.png')
    assert written == 6
    assert (tmp_path / 'a.png').read_bytes() == b'abcdef'


async def test_refuses_past_the_cap(tmp_path: Path):
    up = _FakeUpload(b'x' * (MAX_UPLOAD_SIZE + 1))
    with pytest.raises(HTTPException) as exc:
        await save_upload(up, tmp_path / 'big.png')
    assert exc.value.status_code == 413


async def test_leaves_no_partial_file_behind(tmp_path: Path):
    """A refused upload must not leave a truncated attachment on disk
    for the agent to find and treat as the user's image."""
    dest = tmp_path / 'big.png'
    up = _FakeUpload(b'x' * (MAX_UPLOAD_SIZE + 1))
    with pytest.raises(HTTPException):
        await save_upload(up, dest)
    assert not dest.exists()


async def test_stops_reading_instead_of_draining_the_whole_body(
    tmp_path: Path,
):
    """The heart of it: reject DURING the stream, not after.

    A 4x-over-cap upload must not be pulled into memory in full before
    being refused — which is precisely what the old
    ``await file.read()`` then ``len(...)`` order did.
    """
    oversize = MAX_UPLOAD_SIZE * 4
    up = _FakeUpload(b'x' * oversize)
    with pytest.raises(HTTPException):
        await save_upload(up, tmp_path / 'huge.png')
    assert up.bytes_read < oversize
    # One chunk's slack past the limit is all it takes to know.
    assert up.bytes_read <= MAX_UPLOAD_SIZE + 1024 * 1024


async def test_rejects_an_empty_upload(tmp_path: Path):
    dest = tmp_path / 'empty.png'
    with pytest.raises(HTTPException) as exc:
        await save_upload(_FakeUpload(b''), dest)
    assert exc.value.status_code == 400
    assert not dest.exists()


def test_rejects_a_type_outside_the_allow_list():
    with pytest.raises(HTTPException) as exc:
        reject_disallowed_type(_FakeUpload(b'x', content_type='image/bmp'))
    assert exc.value.status_code == 400


def test_allows_the_listed_types():
    for ct in (
        'image/png',
        'image/jpeg',
        'image/gif',
        'image/webp',
        'application/pdf',
    ):
        reject_disallowed_type(_FakeUpload(b'x', content_type=ct))


@pytest.mark.parametrize(
    'header,expected',
    [
        (None, False),  # chunked — save_upload is the backstop
        ('not-a-number', False),
        ('0', False),
        (str(MAX_UPLOAD_SIZE), False),
        (str(MAX_UPLOAD_SIZE * 10), True),
    ],
)
def test_declared_body_check(header, expected):
    assert declared_body_too_large(header) is expected


def test_human_size_matches_the_frontend_formatter():
    assert human_size(100 * 1024 * 1024) == '100MB'
    assert human_size(MAX_UPLOAD_SIZE) == '100MB'


# Names that have each been a private copy of the upload cap.
_RETIRED_CAPS = ('MAX_FILE_SIZE =', '_UPLOAD_MAX_SIZE =', '_MAX_UPLOAD =')


def test_no_upload_route_keeps_its_own_cap_or_unbounded_read():
    """The drift guard — and the reason it scans instead of listing.

    A hardcoded two-module list passed while ``vision.py`` still held a
    FOURTH copy of the cap and the same read-then-judge order; review
    caught it, the test did not. Every router that accepts an
    ``UploadFile`` is checked, so the next upload route is covered the
    day it is written.
    """
    routers_dir = Path(attachments_router.__file__).parent
    checked = []
    for path in sorted(routers_dir.glob('*.py')):
        src = path.read_text()
        if 'UploadFile' not in src:
            continue
        checked.append(path.name)
        assert 'await file.read()' not in src, (
            f'{path.name} reads the whole upload before checking its size'
        )
        for banned in _RETIRED_CAPS:
            assert banned not in src, (
                f'{path.name} redeclares the upload cap ({banned.strip()}) '
                f'— import MAX_UPLOAD_SIZE from app.uploads instead'
            )
    # If a rename ever makes this scan match nothing, fail loudly
    # rather than pass vacuously.
    assert {'attachments.py', 'tasks_files.py', 'vision.py'} <= set(checked), (
        checked
    )


class _DrainingApp:
    """Stands in for the multipart parser: reads the body to the end
    before responding, which is exactly what spools it to disk."""

    def __init__(self):
        self.drained = 0
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            if message['type'] != 'http.request':
                break
            self.drained += len(message.get('body', b''))
            if not message.get('more_body'):
                break
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [],
        })
        await send({'type': 'http.response.body', 'body': b'ok'})


async def _drive(inner, headers, chunks):
    """Push `chunks` through the middleware wrapping `inner`."""
    middleware = UploadBodyLimitMiddleware(inner)
    scope = {'type': 'http', 'headers': headers}
    remaining = list(chunks)
    sent = []

    async def receive():
        if not remaining:
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        body = remaining.pop(0)
        return {
            'type': 'http.request',
            'body': body,
            'more_body': bool(remaining),
        }

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


_MULTIPART = [(b'content-type', b'multipart/form-data; boundary=x')]


def _status(sent):
    for m in sent:
        if m['type'] == 'http.response.start':
            return m['status']
    return None


async def test_declared_oversize_is_refused_without_reaching_the_app():
    inner = _DrainingApp()
    sent = await _drive(
        inner,
        _MULTIPART + [(b'content-length', str(MAX_UPLOAD_SIZE * 10).encode())],
        [b'x'],
    )
    assert _status(sent) == 413
    assert not inner.called


async def test_chunked_oversize_is_cut_off_mid_stream():
    """The hole a Content-Length check alone leaves open.

    A chunked upload announces no length, so the only place it can be
    bounded is the receive channel — otherwise the parser spools the
    whole thing to a temp file before any route code runs, and
    ``save_upload`` is too late to protect the disk.
    """
    inner = _DrainingApp()
    chunk = b'x' * (1024 * 1024)
    total = MAX_UPLOAD_SIZE * 3
    sent = await _drive(inner, _MULTIPART, [chunk] * (total // len(chunk)))
    assert _status(sent) == 413
    assert inner.drained < total, 'the whole body was buffered anyway'


async def test_a_multipart_body_under_the_cap_passes_through():
    inner = _DrainingApp()
    sent = await _drive(inner, _MULTIPART, [b'x' * 1024])
    assert _status(sent) == 200
    assert inner.called


async def test_a_large_json_body_is_not_treated_as_an_upload():
    """Scoping guard. A global body cap would answer a big JSON PUT
    (e.g. saving a workspace file) with "File too large"."""
    inner = _DrainingApp()
    sent = await _drive(
        inner,
        [
            (b'content-type', b'application/json'),
            (b'content-length', str(MAX_UPLOAD_SIZE * 10).encode()),
        ],
        [b'{"content":"..."}'],
    )
    assert _status(sent) == 200
    assert inner.called


async def test_non_http_scopes_pass_straight_through():
    """A websocket or lifespan scope has no body to bound, and must
    reach the app untouched rather than be inspected for headers."""
    seen = {}

    async def inner(scope, receive, send):
        seen['scope'] = scope

    async def receive():
        return {'type': 'lifespan.startup'}

    async def send(_message):
        pass

    middleware = UploadBodyLimitMiddleware(inner)
    await middleware({'type': 'lifespan'}, receive, send)
    assert seen['scope']['type'] == 'lifespan'


def test_cors_wraps_the_body_limit():
    """A 413 emitted outside CORS reaches a cross-origin caller as an
    opaque network error. Starlette's add_middleware inserts at the
    front, so CORS must be registered LAST to end up outermost."""
    classes = [m.cls for m in fastapi_app.user_middleware]
    assert CORSMiddleware in classes, classes
    assert UploadBodyLimitMiddleware in classes, classes
    assert classes.index(CORSMiddleware) < classes.index(
        UploadBodyLimitMiddleware
    ), 'CORS must be outermost (registered after the body limit)'
