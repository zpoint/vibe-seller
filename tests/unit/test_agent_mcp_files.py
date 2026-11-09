"""Unit tests for the shared file contracts in
``app/agent_mcp/files.py`` (ops-expansion rule 4).

The ceilings and the text-vs-binary decision are behavioural
contracts every file-shaped tool depends on, so they are pinned here
without HTTP: an inline text file comes back as text, a binary as a
URL (+ blob under 5 MB), GB18030 never misclassifies a binary as
text, and uploads are validated and capped before they reach a
route.
"""

import asyncio
import base64

import pytest

from app.agent_mcp import files as mcp_files
from app.agent_mcp.support import ToolError

pytestmark = pytest.mark.unit


class _Request:
    base_url = 'http://test/'


class _Ctx:
    request = _Request()


class TestFileResult:
    def test_text_file_is_inline(self, tmp_path):
        f = tmp_path / 'notes.txt'
        f.write_text('hello knowledge', encoding='utf-8')
        out = mcp_files.file_result(
            _Ctx(),
            path='knowledge/notes.txt',
            abs_path=str(f),
            mime_type='text/plain',
            download_path='/api/workspace/file/raw?path=knowledge/x',
        )
        assert out['encoding'] == 'text'
        assert out['content'] == 'hello knowledge'
        assert 'url' not in out

    def test_binary_is_url_and_blob(self, tmp_path):
        # A non-image binary: an image is SHOWN instead (TestImageBlock).
        f = tmp_path / 'export.xlsx'
        f.write_bytes(b'PK\x03\x04binary body')
        out = mcp_files.file_result(
            _Ctx(),
            path='uploads/export.xlsx',
            abs_path=f,
            mime_type='application/vnd.ms-excel',
            download_path='/api/tasks/t/files/uploads/export.xlsx',
        )
        assert out['encoding'] == 'url'
        assert out['url'] == (
            'http://test/api/tasks/t/files/uploads/export.xlsx'
        )
        assert base64.b64decode(out['blob']) == f.read_bytes()

    def test_gb18030_never_turns_a_binary_into_text(self, tmp_path):
        """The detector may not use GB18030: it accepts nearly every
        byte sequence, so a PNG would come back as mojibake "text"."""
        f = tmp_path / 'blob.bin'
        f.write_bytes(bytes(range(256)) * 64)  # 16 KB of non-UTF-8
        out = mcp_files.file_result(
            _Ctx(),
            path='uploads/blob.bin',
            abs_path=f,
            mime_type='application/octet-stream',
            download_path='/api/tasks/t/files/uploads/blob.bin',
        )
        assert out['encoding'] == 'url'

    def test_unknown_type_utf8_text_is_inline(self, tmp_path):
        f = tmp_path / 'config.cfg'
        f.write_text('key = value\n', encoding='utf-8')
        out = mcp_files.file_result(
            _Ctx(),
            path='uploads/config.cfg',
            abs_path=f,
            mime_type='application/octet-stream',
            download_path='/api/tasks/t/files/uploads/config.cfg',
        )
        assert out['encoding'] == 'text'

    def test_oversize_binary_gets_url_without_blob(self, tmp_path):
        f = tmp_path / 'huge.png'
        f.write_bytes(b'\x89PNG' + b'x' * (mcp_files.MAX_BLOB_BYTES + 1))
        out = mcp_files.file_result(
            _Ctx(),
            path='uploads/huge.png',
            abs_path=f,
            mime_type='image/png',
            download_path='/api/tasks/t/files/uploads/huge.png',
        )
        assert out['encoding'] == 'url'
        assert 'blob' not in out
        assert 'download it from the URL' in out['note']


class TestValidatedTaskPath:
    @pytest.mark.parametrize(
        'bad',
        [
            '../escape.txt',
            'a/../../escape',
            '/abs/path',
            'win\\style',
            '',
            '   ',
        ],
    )
    def test_escapes_are_refused(self, bad):
        with pytest.raises(ToolError):
            mcp_files.validated_task_path(bad)

    def test_safe_paths_are_normalized(self):
        assert (
            mcp_files.validated_task_path('uploads/./report.txt')
            == 'uploads/report.txt'
        )


class TestDecodeUpload:
    def _ok(self):
        return {
            'filename': 'widget.png',
            'mime_type': 'image/png',
            'content_base64': base64.b64encode(b'pngbytes').decode(),
        }

    def test_valid_entry_decodes(self):
        name, mime, data = mcp_files.decode_upload(self._ok())
        assert (name, mime, data) == ('widget.png', 'image/png', b'pngbytes')

    def test_bad_base64_is_refused(self):
        entry = self._ok()
        entry['content_base64'] = '!!not b64!!'
        with pytest.raises(ToolError, match='base64'):
            mcp_files.decode_upload(entry)

    def test_missing_fields_are_named(self):
        for key in ('filename', 'mime_type', 'content_base64'):
            entry = self._ok()
            del entry[key]
            with pytest.raises(ToolError, match=key):
                mcp_files.decode_upload(entry)

    def test_over_cap_is_refused_before_decoding(self):
        entry = self._ok()
        # ~14 MB of base64: past the 10 MB cap even before decoding.
        entry['content_base64'] = 'A' * 14 * 1024 * 1024
        with pytest.raises(ToolError, match='10 MB'):
            mcp_files.decode_upload(entry)

    def test_disallowed_type_is_refused_up_front(self):
        """The upload routes' own allow-list, checked BEFORE anything
        is created around the attachment."""
        entry = self._ok()
        entry['mime_type'] = 'application/x-msdownload'
        with pytest.raises(ToolError, match='not allowed'):
            mcp_files.decode_upload(entry)

    def test_empty_payload_is_refused(self):
        """An empty upload is caught by the required-string check —
        the base64 of zero bytes IS the empty string."""
        entry = self._ok()
        entry['content_base64'] = base64.b64encode(b'').decode()
        with pytest.raises(ToolError, match='content_base64 is required'):
            mcp_files.decode_upload(entry)


class TestAsUploadFile:
    def test_the_route_cannot_tell_it_from_multipart(self):
        upload = mcp_files.as_upload_file('x.png', 'image/png', b'data')
        assert upload.filename == 'x.png'
        assert upload.content_type == 'image/png'
        assert asyncio.run(upload.read()) == b'data'


class TestImageBlock:
    """What the MCP SHOWS for an image file (an ``image`` content block)."""

    @pytest.mark.parametrize(
        'head,mime',
        [
            (b'\x89PNG\r\n\x1a\n', 'image/png'),
            (b'\xff\xd8\xff\xe0', 'image/jpeg'),
            (b'GIF89a', 'image/gif'),
            (b'RIFF\x00\x00\x00\x00WEBPVP8 ', 'image/webp'),
        ],
    )
    def test_type_comes_from_the_bytes_not_the_name(self, tmp_path, head, mime):
        # kie.ai returns WEBP/JPEG saved as .png — trust the magic bytes.
        f = tmp_path / 'out.png'
        f.write_bytes(head + b'\x00' * 32)
        block = mcp_files.image_block(f)
        assert block['mime_type'] == mime
        assert base64.b64decode(block['data']) == f.read_bytes()

    def test_non_images_are_not_shown(self, tmp_path):
        f = tmp_path / 'report.pdf'
        f.write_bytes(b'%PDF-1.4 not a picture')
        assert mcp_files.image_block(f) is None
        assert mcp_files.image_block(tmp_path / 'missing.png') is None

    def test_a_large_image_is_shown_as_a_preview(self, tmp_path, monkeypatch):
        Image = pytest.importorskip('PIL.Image')
        from PIL import ImageFilter  # noqa: PLC0415

        f = tmp_path / 'big.png'
        # Photo-like: a lossless PNG of ~1.4 MB whose 1568 px JPEG
        # preview is ~0.1 MB — over the raw limit, under it as a preview.
        Image.effect_noise((3000, 2000), 40).filter(
            ImageFilter.GaussianBlur(4)
        ).convert('RGB').save(f)
        monkeypatch.setattr(mcp_files, 'MAX_IMAGE_BLOCK_BYTES', 500_000)
        assert f.stat().st_size > 500_000
        block = mcp_files.image_block(f)
        assert block['mime_type'] == 'image/jpeg'
        import io  # noqa: PLC0415

        preview = Image.open(io.BytesIO(base64.b64decode(block['data'])))
        assert max(preview.size) == mcp_files.PREVIEW_MAX_EDGE

    def test_file_result_shows_an_image_and_keeps_the_download(self, tmp_path):
        f = tmp_path / 'image.png'
        f.write_bytes(b'\x89PNG\r\n\x1a\nbinary body')
        out = mcp_files.file_result(
            _Ctx(),
            path='generated_images/image.png',
            abs_path=f,
            mime_type='image/png',
            download_path='/api/tasks/t/files/generated_images/image.png',
        )
        assert out['shown_as_image'] is True
        assert out[mcp_files.IMAGES_KEY][0]['mime_type'] == 'image/png'
        assert out['url'].endswith('generated_images/image.png')
        # The image block IS the bytes — no second copy as a JSON blob.
        assert 'blob' not in out

    def test_only_the_head_is_read_to_rule_out_an_image(
        self, tmp_path, monkeypatch
    ):
        f = tmp_path / 'big.zip'
        f.write_bytes(b'PK\x03\x04' + b'\x00' * 4096)
        monkeypatch.setattr(
            mcp_files.Path,
            'read_bytes',
            lambda self: pytest.fail('read the whole file'),
        )
        assert mcp_files.image_block(f) is None

    def test_a_budget_too_small_for_any_preview_shows_nothing(self, tmp_path):
        pytest.importorskip('PIL.Image')
        from PIL import Image  # noqa: PLC0415

        f = tmp_path / 'big.png'
        Image.effect_noise((1200, 1200), 100).convert('RGB').save(f)
        assert mcp_files.image_block(f, max_bytes=100) is None
