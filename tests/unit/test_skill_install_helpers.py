"""Unit tests for the SHARED import sequence helpers.

``install_stream`` (mint → stage → install) and
``skill_fetch.install_url`` (fetch → install_stream) are what every
door calls instead of assembling the sequence itself;
``describe_refusal`` is the single source of (status, message) for
pipeline refusals. These tests drive the helpers directly with a
recording manager — the doors' own journeys are pinned
workflow-tier.
"""

import io
import tarfile

import pytest

from app import skill_fetch, skill_uploads
from app.skill_install import describe_refusal, install_stream
from app.skill_uploads import UploadError

pytestmark = pytest.mark.unit

SKILL_MD = '---\nname: helper-skill\ndescription: d\n---\n'


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        data = SKILL_MD.encode()
        info = tarfile.TarInfo('SKILL.md')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _chunks(data: bytes):
    async def gen():
        yield data

    return gen()


class _FakeManager:
    """Records what the install core was asked to do."""

    def __init__(self):
        self.installed = []
        self.commits = []

    def save_skill_blocking(
        self,
        slug,
        skill_md,
        files=None,
        files_bin=None,
        executables=None,
        origin_url='',
        replace=False,
        overwrite=False,
    ):
        self.installed.append({
            'slug': slug,
            'origin_url': origin_url,
            'replace': replace,
            'overwrite': overwrite,
        })
        return (
            {
                'slug': slug,
                'path': f'.claude/skills/{slug}',
                'action': 'created',
            },
            False,
        )

    async def _auto_commit(self, msg):
        self.commits.append(msg)


@pytest.fixture
def staging(monkeypatch, tmp_path):
    monkeypatch.setattr(
        skill_uploads, '_uploads_dir', lambda: tmp_path / 'uploads'
    )


class TestInstallStream:
    async def test_full_sequence_installs_and_commits(self, staging):
        mgr = _FakeManager()
        result = await install_stream(
            mgr,
            'user-1',
            _chunks(_archive()),
            'application/gzip',
            origin_url='origin-x',
        )
        assert result['slug'] == 'helper-skill'  # derived by the core
        assert mgr.installed == [
            {
                'slug': 'helper-skill',
                'origin_url': 'origin-x',
                'replace': True,
                'overwrite': False,
            }
        ]
        assert mgr.commits == ['Create skill: helper-skill']
        # the upload was consumed by the sequence
        assert result['action'] == 'created'

    async def test_oversize_body_refuses_before_any_install(
        self, staging, monkeypatch
    ):
        monkeypatch.setattr(skill_uploads, 'MAX_ARCHIVE_UPLOAD_BYTES', 8)
        mgr = _FakeManager()
        with pytest.raises(UploadError) as excinfo:
            await install_stream(
                mgr, 'u', _chunks(_archive()), 'application/gzip'
            )
        assert excinfo.value.status_code == 413
        assert mgr.installed == []


class TestInstallUrl:
    async def test_fetches_then_installs_with_url_provenance(
        self, staging, monkeypatch
    ):
        data = _archive()
        seen = []

        async def fake_stream(url):
            seen.append(url)
            yield data

        monkeypatch.setattr(skill_fetch, 'open_archive_stream', fake_stream)
        mgr = _FakeManager()
        url = 'https://example.invalid/skill.tar.gz'
        result = await skill_fetch.install_url(mgr, 'u', url)
        assert seen == [url]
        assert result['slug'] == 'helper-skill'
        # origin_url defaults to the URL INSIDE the helper — doors
        # pass nothing and still get imported-provenance
        assert mgr.installed[0]['origin_url'] == url


class TestDescribeRefusal:
    def test_upload_error_keeps_status_and_message(self):
        assert describe_refusal(UploadError(409, 'nope')) == (409, 'nope')

    def test_status_carrying_value_error(self):
        class _E(ValueError):
            status_code = 502

        assert describe_refusal(_E('unreachable')) == (502, 'unreachable')

    def test_plain_value_error_is_400(self):
        assert describe_refusal(ValueError('x')) == (400, 'x')
