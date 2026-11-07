"""Unit tests for presigned skill-archive uploads.

Signing (tampered / expired / reused / other-user refusals), the
streaming size cap, magic-byte format sniffing, and every archive
rejection the extractor must make — crafted malicious tars and zips
included. The extraction output is then installed through the REAL
``save_skill`` and compared byte for byte.
"""

import asyncio
import base64
import io
import os
import stat
import tarfile
import time
import zipfile

import pytest

from app import skill_uploads
from app.skill_uploads import (
    UploadError,
    archive_path,
    check_upload_request,
    consume_upload,
    create_upload,
    extract_archive,
    sniff_format,
    stage_stream,
    staged_record,
    verify_upload_signature,
)
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)

SKILL_MD = (
    '---\nname: upload-skill\ndescription: d\n---\n# upload-skill\nBody.\n'
)


def _workspace(tmp_path):
    root = tmp_path / 'ws'
    (root / '.claude' / 'skills').mkdir(parents=True)
    (root / '.git').mkdir()
    return WorkspaceManager(root)


def _archive(tmp_path, entries, kind='tar.gz'):
    """Build an archive from {name: data | TarInfo-ish specs}.

    ``entries`` maps name -> bytes (regular file) or a tuple
    (kind, linkname/mode) for the hostile entry kinds.
    """
    path = tmp_path / f'archive.{kind if kind != "tar.gz" else "tgz"}'
    if kind == 'zip':
        with zipfile.ZipFile(path, 'w') as zf:
            for name, payload in entries.items():
                if isinstance(payload, tuple):
                    info = zipfile.ZipInfo(name)
                    info.external_attr = payload[1] << 16
                    zf.writestr(info, payload[0])
                else:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = 0o644 << 16
                    zf.writestr(info, payload)
        return path
    mode = 'w:gz' if kind == 'tar.gz' else 'w'
    with tarfile.open(path, mode) as tar:
        for name, payload in entries.items():
            if isinstance(payload, tuple):
                entry_kind, extra = payload
                info = tarfile.TarInfo(name)
                if entry_kind == 'sym':
                    info.type = tarfile.SYMTYPE
                    info.linkname = extra
                elif entry_kind == 'hard':
                    info.type = tarfile.LNKTYPE
                    info.linkname = extra
                elif entry_kind == 'fifo':
                    info.type = tarfile.FIFOTYPE
                else:
                    info.type = tarfile.CHRTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o755 if name.endswith('run.py') else 0o644
                tar.addfile(info, io.BytesIO(payload))
    return path


def _good_entries():
    return {
        'skill/SKILL.md': SKILL_MD.encode(),
        'skill/assets/template.png': PNG,
        'skill/scripts/run.py': b'print("hi")\n',
    }


async def _stage(path, upload_id, content_type='application/gzip'):
    data = await asyncio.to_thread(path.read_bytes)

    async def chunks():
        for i in range(0, len(data), 64 * 1024):
            yield data[i : i + 64 * 1024]

    return await stage_stream(upload_id, chunks(), content_type)


# ── Signing ─────────────────────────────────────────────────


class TestSigning:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        assert minted['put_url'].startswith('http://host:1/api/skill-uploads/')
        assert 'sig=' in minted['put_url']
        # put_path is the origin-independent contract: a client whose
        # MCP origin differs re-homes the path and keeps the query.
        assert minted['put_path'].startswith('/api/skill-uploads/')
        assert 'sig=' in minted['put_path']
        assert minted['put_url'].endswith(minted['put_path'])
        assert 'COPYFILE_DISABLE=1 tar czf' in minted['curl']
        assert 'zip -X' in minted['curl']

    def test_tampered_signature_refused(self):
        assert not verify_upload_signature('a' * 32, 'u', 9999999999, 'x' * 64)
        good = skill_uploads._signature('a' * 32, 'u', 9999999999)
        assert verify_upload_signature('a' * 32, 'u', 9999999999, good)
        assert not verify_upload_signature('b' * 32, 'u', 9999999999, good)
        assert not verify_upload_signature('a' * 32, 'u', 9999999998, good)

    def test_expired_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']
        past = int(time.time()) - 5
        sig = skill_uploads._signature(upload_id, 'user-a', past)
        with pytest.raises(UploadError) as excinfo:
            check_upload_request(upload_id, 'user-a', past, sig)
        assert excinfo.value.status_code == 403
        assert 'expired' in excinfo.value.message

    def test_other_users_signature_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']
        exp = int(time.time()) + 600
        other_sig = skill_uploads._signature(upload_id, 'user-b', exp)
        with pytest.raises(UploadError) as excinfo:
            check_upload_request(upload_id, 'user-b', exp, other_sig)
        assert excinfo.value.status_code == 403
        with pytest.raises(UploadError):
            check_upload_request(
                upload_id,
                'user-a',
                exp,
                skill_uploads._signature(upload_id, 'user-b', exp),
            )

    def test_unknown_upload_is_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        with pytest.raises(UploadError) as excinfo:
            check_upload_request('f' * 32, 'u', 9999999999, 'sig')
        assert excinfo.value.status_code == 404


# ── Staging ─────────────────────────────────────────────────


class TestStaging:
    def _mint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        return create_upload('user-a', 'http://host:1/')

    async def test_stage_and_consume(self, tmp_path, monkeypatch):
        minted = self._mint(tmp_path, monkeypatch)
        path = _archive(tmp_path, _good_entries())
        result = await _stage(path, minted['upload_id'])
        assert result['status'] == 'staged'
        assert result['format'] == 'tar.gz'
        record = staged_record(minted['upload_id'], 'user-a')
        assert record['status'] == 'staged'

        skill_uploads.consume_upload(minted['upload_id'])
        with pytest.raises(UploadError) as excinfo:
            staged_record(minted['upload_id'], 'user-a')
        assert 'consumed' in excinfo.value.message

    async def test_reused_upload_refused(self, tmp_path, monkeypatch):
        minted = self._mint(tmp_path, monkeypatch)
        path = _archive(tmp_path, _good_entries())
        await _stage(path, minted['upload_id'])
        with pytest.raises(UploadError) as excinfo:
            await _stage(path, minted['upload_id'])
        assert excinfo.value.status_code == 403
        assert 'already used' in excinfo.value.message

    async def test_streaming_size_cap(self, tmp_path, monkeypatch):
        minted = self._mint(tmp_path, monkeypatch)
        monkeypatch.setattr(skill_uploads, 'MAX_ARCHIVE_UPLOAD_BYTES', 1024)

        async def chunks():
            for _ in range(10):
                yield b'x' * 512

        with pytest.raises(UploadError) as excinfo:
            await stage_stream(
                minted['upload_id'], chunks(), 'application/gzip'
            )
        assert excinfo.value.status_code == 413
        # Nothing half-written is left behind.
        assert not skill_uploads.archive_path(minted['upload_id']).exists()

    async def test_wrong_content_type_refused(self, tmp_path, monkeypatch):
        minted = self._mint(tmp_path, monkeypatch)

        async def chunks():
            return
            yield  # pragma: no cover - empty async iterator

        with pytest.raises(UploadError) as excinfo:
            await stage_stream(minted['upload_id'], chunks(), 'text/html')
        assert excinfo.value.status_code == 415

    async def test_unrecognised_format_refused(self, tmp_path, monkeypatch):
        minted = self._mint(tmp_path, monkeypatch)
        fake = tmp_path / 'not-an-archive'
        fake.write_bytes(b'hello world, definitely not an archive')

        async def chunks():
            yield fake.read_bytes()

        with pytest.raises(UploadError) as excinfo:
            await stage_stream(
                minted['upload_id'], chunks(), 'application/gzip'
            )
        assert excinfo.value.status_code == 415


class TestSniffing:
    def test_formats(self, tmp_path):
        tarball = _archive(tmp_path, _good_entries(), 'tar.gz')
        plain = _archive(tmp_path, _good_entries(), 'tar')
        zipped = _archive(tmp_path, _good_entries(), 'zip')
        assert sniff_format(tarball) == 'tar.gz'
        assert sniff_format(plain) == 'tar'
        assert sniff_format(zipped) == 'zip'
        junk = tmp_path / 'junk'
        junk.write_bytes(b'not an archive at all')
        assert sniff_format(junk) is None


# ── Extraction ──────────────────────────────────────────────


class TestExtraction:
    def test_top_dir_stripped_and_bytes_identical(self, tmp_path):
        path = _archive(tmp_path, _good_entries(), 'tar.gz')
        got = extract_archive(path, 'tar.gz')
        assert got.skill_md == SKILL_MD
        assert got.files['assets/template.png'] == PNG
        assert got.executables == {'scripts/run.py'}

    def test_macos_junk_is_skipped_and_reported(self, tmp_path):
        entries = dict(_good_entries())
        entries['skill/._template.png'] = b'junk'
        entries['__MACOSX/skill/._SKILL.md'] = b'junk'
        entries['skill/.DS_Store'] = b'junk'
        path = _archive(tmp_path, entries, 'tar')

        got = extract_archive(path, 'tar')
        assert got.files['assets/template.png'] == PNG
        assert sorted(got.files) == [
            'assets/template.png',
            'scripts/run.py',
        ], got.files
        assert sorted(got.ignored) == [
            '__MACOSX/skill/._SKILL.md',
            'skill/.DS_Store',
            'skill/._template.png',
        ], got.ignored

    def test_zip_exec_bit_from_external_attr(self, tmp_path):
        entries = dict(_good_entries())
        path = tmp_path / 'z.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            for name, payload in entries.items():
                info = zipfile.ZipInfo(name)
                mode = 0o755 if name.endswith('run.py') else 0o644
                info.external_attr = mode << 16
                zf.writestr(info, payload)
        got = extract_archive(path, 'zip')
        assert got.executables == {'scripts/run.py'}

    def test_no_common_root_is_not_stripped(self, tmp_path):
        entries = {
            'SKILL.md': SKILL_MD.encode(),
            'scripts/run.py': b'print(1)\n',
        }
        path = _archive(tmp_path, entries, 'tar')
        got = extract_archive(path, 'tar')
        assert got.files['scripts/run.py'] == b'print(1)\n'

    def test_missing_skill_md_refused(self, tmp_path):
        path = _archive(tmp_path, {'skill/notes.md': b'x'}, 'tar')
        with pytest.raises(ValueError, match='SKILL.md'):
            extract_archive(path, 'tar')

    def test_case_variant_of_skill_md_refused(self, tmp_path):
        entries = {
            'skill/SKILL.md': SKILL_MD.encode(),
            'skill/skill.md': b'sneaky\n',
        }
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='collide by case'):
            extract_archive(path, 'tar')

    def test_non_utf8_skill_md_refused(self, tmp_path):
        path = _archive(tmp_path, {'skill/SKILL.md': b'\xff\xfe bad'}, 'tar')
        with pytest.raises(ValueError, match='UTF-8'):
            extract_archive(path, 'tar')

    def test_traversal_names_refused(self, tmp_path):
        for bad in ('../escape.md', '/abs.md', 'skill/../up.md'):
            path = _archive(
                tmp_path,
                {'skill/SKILL.md': SKILL_MD.encode(), bad: b'x'},
                'tar',
            )
            with pytest.raises(ValueError, match='escapes'):
                extract_archive(path, 'tar')

    def test_windows_drive_refused(self, tmp_path):
        entries = {
            'skill/SKILL.md': SKILL_MD.encode(),
            'C:/evil.md': b'x',
        }
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='escapes'):
            extract_archive(path, 'tar')

    def test_symlink_refused(self, tmp_path):
        entries = dict(_good_entries())
        entries['skill/evil'] = ('sym', '/etc/passwd')
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='unsupported'):
            extract_archive(path, 'tar')

    def test_hardlink_fifo_and_device_refused(self, tmp_path):
        for kind, extra in (
            ('hard', 'skill/SKILL.md'),
            ('fifo', None),
            ('char', None),
        ):
            entries = dict(_good_entries())
            entries['skill/hostile'] = (kind, extra)
            path = _archive(tmp_path, entries, 'tar')
            with pytest.raises(ValueError, match='unsupported'):
                extract_archive(path, 'tar')

    def test_zip_symlink_refused(self, tmp_path):
        path = tmp_path / 'z.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr(zipfile.ZipInfo('skill/SKILL.md'), SKILL_MD)
            info = zipfile.ZipInfo('skill/evil')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, '/etc/passwd')
        with pytest.raises(ValueError, match='unsupported'):
            extract_archive(path, 'zip')

    def test_case_colliding_entries_refused(self, tmp_path):
        entries = dict(_good_entries())
        entries['skill/Notes.md'] = b'one'
        entries['skill/notes.md'] = b'two'
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='collide by case'):
            extract_archive(path, 'tar')

    def test_duplicate_entries_refused(self, tmp_path):
        path = tmp_path / 'dup.tar'
        with tarfile.open(path, 'w') as tar:
            for _ in range(2):
                info = tarfile.TarInfo('skill/SKILL.md')
                payload = SKILL_MD.encode()
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        with pytest.raises(ValueError, match='duplicate'):
            extract_archive(path, 'tar')

    def test_per_file_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_uploads, 'MAX_SKILL_FILE_BYTES', 8)
        entries = dict(_good_entries())
        entries['skill/big.bin'] = b'x' * 9
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='per-file'):
            extract_archive(path, 'tar')

    def test_total_cap_is_a_zip_bomb_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_uploads, 'MAX_SKILL_TOTAL_BYTES', 16)
        entries = dict(_good_entries())
        entries['skill/big.bin'] = b'x' * 4096
        path = _archive(tmp_path, entries, 'zip')
        with pytest.raises(ValueError, match='total size'):
            extract_archive(path, 'zip')

    def test_file_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_uploads, 'MAX_SKILL_FILES', 3)
        entries = dict(_good_entries())
        for i in range(4):
            entries[f'skill/f{i}.txt'] = b'x'
        path = _archive(tmp_path, entries, 'tar')
        with pytest.raises(ValueError, match='more than'):
            extract_archive(path, 'tar')


# ── Extract → install (the one install path) ────────────────


class TestInstallThroughSaveSkill:
    async def test_round_trip_is_byte_identical(self, tmp_path):
        manager = _workspace(tmp_path)
        path = _archive(tmp_path, _good_entries(), 'tar.gz')
        got = extract_archive(path, 'tar.gz')
        await manager.save_skill(
            'upload-skill',
            got.skill_md,
            files_bin={
                name: base64.b64encode(data).decode()
                for name, data in got.files.items()
            },
            executables=sorted(got.executables),
            replace=True,
        )
        skill_dir = tmp_path / 'ws' / '.claude' / 'skills' / 'upload-skill'
        assert (skill_dir / 'SKILL.md').read_bytes() == SKILL_MD.encode()
        assert (skill_dir / 'assets' / 'template.png').read_bytes() == PNG
        assert os.stat(skill_dir / 'scripts' / 'run.py').st_mode & 0o111

    async def test_overwrite_gate_applies(self, tmp_path):
        manager = _workspace(tmp_path)
        await manager.save_skill(
            'upload-skill', SKILL_MD, files={'hand-made.md': 'work'}
        )
        path = _archive(tmp_path, _good_entries(), 'tar.gz')
        got = extract_archive(path, 'tar.gz')
        with pytest.raises(ValueError, match='already exists'):
            await manager.save_skill('upload-skill', got.skill_md, replace=True)
        result = await manager.save_skill(
            'upload-skill',
            got.skill_md,
            files_bin={
                name: base64.b64encode(data).decode()
                for name, data in got.files.items()
            },
            executables=sorted(got.executables),
            replace=True,
            overwrite=True,
        )
        assert result['action'] == 'updated'
        skill_dir = tmp_path / 'ws' / '.claude' / 'skills' / 'upload-skill'
        assert not (skill_dir / 'hand-made.md').exists()


def test_sweep_removes_stale_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_uploads, '_uploads_dir', lambda: tmp_path / 'up')
    minted = create_upload('user-a', 'http://host:1/')
    record_dir = tmp_path / 'up' / minted['upload_id']
    state = skill_uploads._load_record(minted['upload_id'])
    state['created_at'] = time.time() - (
        skill_uploads.UPLOAD_TTL_SECONDS
        + skill_uploads.SWEEP_GRACE_SECONDS
        + 10
    )
    skill_uploads._save_record(state)
    assert record_dir.is_dir()
    assert skill_uploads.sweep_stale() == 1
    assert not record_dir.exists()


class TestUploadIdHardening:
    """``upload_id`` arrives from a tool argument — every entry point
    validates its shape before it touches a path."""

    def test_every_entry_point_refuses_a_bad_id(self):
        for bad in ('../escape', 'a/b', '', '/etc/passwd', 'Z' * 32):
            with pytest.raises(UploadError) as excinfo:
                archive_path(bad)
            assert excinfo.value.status_code == 404
            with pytest.raises(UploadError):
                consume_upload(bad)
            with pytest.raises(UploadError):
                staged_record(bad, 'user-a')

    async def test_claim_blocks_a_second_put(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']
        # Simulate a PUT already streaming for this id.
        skill_uploads._claim(upload_id)
        path = _archive(tmp_path, _good_entries())

        with pytest.raises(UploadError) as excinfo:
            await _stage(path, upload_id)
        assert excinfo.value.status_code == 409

    async def test_concurrent_puts_one_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']
        path = _archive(tmp_path, _good_entries())

        data = await asyncio.to_thread(path.read_bytes)

        async def slow_chunks():
            for i in range(0, len(data), 16 * 1024):
                await asyncio.sleep(0)
                yield data[i : i + 16 * 1024]

        async def attempt():
            try:
                await stage_stream(upload_id, slow_chunks(), 'application/gzip')
                return 'ok'
            except UploadError as exc:
                return exc.status_code

        results = set(await asyncio.gather(attempt(), attempt()))
        assert results in ({'ok', 409}, {'ok', 403}), results

    async def test_failed_attempt_releases_the_claim(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']

        async def exploding():
            yield b'partial'
            raise RuntimeError('connection dropped')

        with pytest.raises(RuntimeError):
            await stage_stream(upload_id, exploding(), 'application/gzip')

        # The claim is gone and the record is still pending: a retry
        # may re-upload, while a re-USE after success may not.
        path = _archive(tmp_path, _good_entries())
        result = await _stage(path, upload_id)
        assert result['status'] == 'staged'

    async def test_import_refuses_an_expired_upload(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            skill_uploads, '_uploads_dir', lambda: tmp_path / 'up'
        )
        minted = create_upload('user-a', 'http://host:1/')
        upload_id = minted['upload_id']
        await _stage(_archive(tmp_path, _good_entries()), upload_id)
        state = skill_uploads._load_record(upload_id)
        state['expires_at'] = int(time.time()) - 1
        skill_uploads._save_record(state)

        with pytest.raises(UploadError) as excinfo:
            staged_record(upload_id, 'user-a')
        assert excinfo.value.status_code == 403
        assert 'expired' in excinfo.value.message
