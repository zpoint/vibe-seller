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
import pytest

from app.main import limit_upload_body
import app.routers.attachments as attachments_router
import app.routers.tasks_files as tasks_files_router
from app.uploads import (
    MAX_UPLOAD_SIZE,
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


def test_both_upload_routes_share_one_cap():
    """The drift guard. If someone reintroduces a local constant in
    either router, this fails — that divergence is the original bug."""
    for mod in (attachments_router, tasks_files_router):
        src = Path(mod.__file__).read_text()
        assert 'MAX_FILE_SIZE =' not in src, mod.__name__
        assert '_UPLOAD_MAX_SIZE =' not in src, mod.__name__
        assert 'await file.read()' not in src, (
            f'{mod.__name__} reads the whole upload before checking size'
        )


class _StubRequest:
    def __init__(self, content_length: str | None):
        self.headers = (
            {} if content_length is None else {'content-length': content_length}
        )


async def test_middleware_refuses_a_declared_oversize_body():
    """Pins the wiring, not just the predicate: main.py must actually
    turn an over-sized Content-Length into a 413 before the route (and
    therefore before Starlette spools the body to a temp file)."""

    async def _unreachable(_request):
        raise AssertionError('body was parsed despite exceeding the cap')

    resp = await limit_upload_body(
        _StubRequest(str(MAX_UPLOAD_SIZE * 10)), _unreachable
    )
    assert resp.status_code == 413


async def test_middleware_passes_an_ordinary_request_through():
    sentinel = object()

    async def _next(_request):
        return sentinel

    for header in (None, '0', str(MAX_UPLOAD_SIZE)):
        got = await limit_upload_body(_StubRequest(header), _next)
        assert got is sentinel, header
