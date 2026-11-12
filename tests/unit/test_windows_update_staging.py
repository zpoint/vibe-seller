"""Staged-installer tests for app.windows_update.

The stage lives under ``<VIBE_HOME>/updates/<version>/`` and is shared
by the server's background download and the tray. These run on any
platform (the staging primitives are pure file/HTTP code) against a
loopback HTTP server that honors Range, so resume is exercised for
real, not mocked.
"""

import hashlib
import http.server
import json
import threading
from unittest.mock import patch

import pytest

import app.windows_update as wu

pytestmark = pytest.mark.unit

_BLOB = bytes(range(256)) * 40  # 10240 bytes, deterministic


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves the staged blob, honoring a single open-ended Range."""

    seen_ranges: list = []

    def do_GET(self):  # noqa: N802 — http.server API
        start = 0
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes='):
            start = int(rng.split('=')[1].split('-')[0])
            type(self).seen_ranges.append(rng)
        data = _BLOB[start:]
        if start:
            self.send_response(206)
            self.send_header(
                'Content-Range', f'bytes {start}-{len(_BLOB) - 1}/{len(_BLOB)}'
            )
        else:
            self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):  # silence
        pass


@pytest.fixture()
def http_server():
    _RangeHandler.seen_ranges = []
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}/VibeSeller-Setup.exe'
    server.shutdown()
    server.server_close()


@pytest.fixture()
def staged_root(tmp_path, monkeypatch):
    root = tmp_path / 'updates'
    monkeypatch.setattr(wu, 'updates_root', lambda: root)
    return root


def _offer(url, version='0.2.0', size=len(_BLOB), digest=None):
    return {
        'version': version,
        'url': url,
        'size': size,
        'digest': digest or f'sha256:{_sha(_BLOB)}',
    }


class TestStageInstaller:
    def test_download_verify_stage_meta(self, staged_root, http_server):
        path = wu.stage_installer(_offer(http_server))
        assert path.read_bytes() == _BLOB
        meta = json.loads(
            (staged_root / '0.2.0' / wu.META_NAME).read_text(encoding='utf-8')
        )
        assert meta['version'] == '0.2.0'
        assert meta['size'] == len(_BLOB)
        assert meta['sha256'] == _sha(_BLOB)
        assert not (staged_root / '0.2.0' / wu.PART_NAME).exists()

    def test_resume_with_range_after_partial_part(
        self, staged_root, http_server
    ):
        # An interrupted download leaves a partial .part (a connection
        # death is NOT a verify failure — only a mismatch discards it).
        # The next stage must keep those bytes and ask the server for
        # exactly the rest.
        version_dir = staged_root / '0.2.0'
        version_dir.mkdir(parents=True)
        part = version_dir / wu.PART_NAME
        part.write_bytes(_BLOB[: len(_BLOB) // 3])
        path = wu.stage_installer(_offer(http_server))
        assert _RangeHandler.seen_ranges == [f'bytes={len(_BLOB) // 3}-']
        assert path.read_bytes() == _BLOB

    def test_completed_but_corrupt_part_is_discarded(
        self, staged_root, http_server
    ):
        # A part at exactly the expected size is taken as complete and
        # verified — garbage tails fail the sha256 and are discarded,
        # so the next cycle starts clean instead of resuming junk.
        version_dir = staged_root / '0.2.0'
        version_dir.mkdir(parents=True)
        good = _BLOB[: len(_BLOB) // 2]
        (version_dir / wu.PART_NAME).write_bytes(
            good + bytes(len(_BLOB) - len(good))
        )
        with pytest.raises(wu.VerifyError):
            wu.stage_installer(_offer(http_server))
        assert not (version_dir / wu.PART_NAME).exists()

    def test_progress_reports_bytes(self, staged_root, http_server):
        calls = []
        wu.stage_installer(
            _offer(http_server),
            progress=lambda d, t: calls.append((d, t)),
        )
        assert calls[0] == (0, len(_BLOB))  # initial (no .part)
        assert calls[-1][0] == len(_BLOB)
        assert all(t == len(_BLOB) for _d, t in calls[1:])

    def test_sha256_mismatch_discards_part(self, staged_root, http_server):
        offer = _offer(http_server, digest='sha256:' + '0' * 64)
        with pytest.raises(wu.VerifyError):
            wu.stage_installer(offer)
        assert not (staged_root / '0.2.0' / wu.PART_NAME).exists()
        assert not (staged_root / '0.2.0' / wu.STAGED_NAME).exists()

    def test_size_mismatch_discards_part(self, staged_root, http_server):
        with pytest.raises(wu.VerifyError):
            wu.stage_installer(_offer(http_server, size=len(_BLOB) + 5))
        assert not (staged_root / '0.2.0' / wu.PART_NAME).exists()

    def test_no_digest_checks_size_only(self, staged_root, http_server):
        offer = _offer(http_server, digest=None)
        path = wu.stage_installer(offer)
        assert path.exists()

    def test_complete_part_is_not_refetched(self, staged_root, tmp_path):
        # A .part at exactly the expected size is complete: re-staging
        # verifies it instead of asking the server for bytes past the
        # end (which would only earn an HTTP 416).
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        root = staged_root / '0.2.0'
        root.mkdir(parents=True)
        (root / wu.PART_NAME).write_bytes(_BLOB)
        path = wu.stage_installer(_offer(str(src)))
        assert path.read_bytes() == _BLOB

    def test_only_newest_version_dir_kept(self, staged_root, tmp_path):
        old = staged_root / '0.1.0'
        old.mkdir(parents=True)
        (old / 'keep.txt').write_text('x', encoding='utf-8')
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        wu.stage_installer(_offer(str(src), version='0.2.0'))
        assert not old.exists()
        assert (staged_root / '0.2.0' / wu.STAGED_NAME).exists()


class TestStagedInstaller:
    def test_returns_path_when_meta_verifies(self, staged_root, tmp_path):
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        staged = wu.stage_installer(_offer(str(src)))
        assert wu.staged_installer('0.2.0') == staged
        assert wu.staged_installer('0.2.0', verify=False) == staged

    def test_none_when_file_tampered(self, staged_root, tmp_path):
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        staged = wu.stage_installer(_offer(str(src)))
        staged.write_bytes(b'x' * len(_BLOB))
        assert wu.staged_installer('0.2.0') is None

    def test_none_without_meta(self, staged_root):
        d = staged_root / '0.2.0'
        d.mkdir(parents=True)
        (d / wu.STAGED_NAME).write_bytes(_BLOB)
        assert wu.staged_installer('0.2.0') is None


class TestRunInstallerModes:
    def test_silent_mode_flags(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wu.sys, 'platform', 'win32')
        with patch.object(wu.subprocess, 'Popen') as popen:
            wu.run_installer(tmp_path / 's.exe', mode='silent')
        args = popen.call_args[0][0]
        assert args[1:] == [
            '/SILENT',
            '/SUPPRESSMSGBOXES',
            '/NORESTART',
            f'/LOG={tmp_path / "install.log"}',
        ]
        flags = popen.call_args[1]['creationflags']
        assert flags & wu.CREATE_BREAKAWAY_FROM_JOB
        assert flags & wu.DETACHED_PROCESS

    def test_breakaway_refused_falls_back_to_plain(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wu.sys, 'platform', 'win32')
        calls = []

        def flaky(args, creationflags=0, **_k):
            calls.append(creationflags)
            if len(calls) == 1:
                raise OSError(5010, 'not supported')

        with patch.object(wu.subprocess, 'Popen', side_effect=flaky):
            wu.run_installer(tmp_path / 's.exe', mode='silent')
        assert calls == [
            wu.DETACHED_PROCESS
            | wu.CREATE_NEW_PROCESS_GROUP
            | wu.CREATE_BREAKAWAY_FROM_JOB,
            wu.DETACHED_PROCESS | wu.CREATE_NEW_PROCESS_GROUP,
        ]

    def test_interactive_mode_no_flags(self, tmp_path):
        with patch.object(wu.subprocess, 'Popen') as popen:
            wu.run_installer(tmp_path / 's.exe')
        assert popen.call_args[0][0] == [str(tmp_path / 's.exe')]


class TestTrayReusesStage:
    def test_upgrade_uses_staged_installer_not_a_second_download(
        self, staged_root, tmp_path
    ):
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        offer = _offer(str(src))
        staged = wu.stage_installer(offer)
        with (
            patch.object(wu, 'check_for_update', return_value=offer),
            patch.object(wu, 'download_installer') as dl,
            patch.object(wu, 'run_installer') as run,
        ):
            res = wu.upgrade()
        assert res['status'] == 'updating'
        dl.assert_not_called()
        run.assert_called_once_with(staged, mode='interactive')

    def test_upgrade_downloads_when_no_stage(self, staged_root):
        offer = _offer('http://nowhere/VibeSeller-Setup.exe')
        with (
            patch.object(wu, 'check_for_update', return_value=offer),
            patch.object(wu, 'download_installer') as dl,
            patch.object(wu, 'run_installer'),
        ):
            wu.upgrade()
        dl.assert_called_once()


class TestBreakawayFallbackReview1:
    """Review MUST 2: ERROR_ACCESS_DENIED (winerror 5) on the breakaway
    attempt is the job object refusing the FLAG — it must fall back to
    the plain detached flags immediately, not be treated as a locked
    file and retried against the same flags."""

    def test_winerror5_on_breakaway_falls_back_to_plain(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(wu.sys, 'platform', 'win32')
        seen: list[int] = []

        def popen(args, creationflags=0, **_kw):
            seen.append(creationflags)
            if creationflags & wu.CREATE_BREAKAWAY_FROM_JOB:
                err = PermissionError(5, 'Access is denied')
                err.winerror = 5
                raise err

        with (
            patch.object(wu.subprocess, 'Popen', side_effect=popen),
            patch.object(wu.time, 'sleep') as sleep,
        ):
            wu.run_installer(tmp_path / 's.exe', mode='silent')
        assert len(seen) == 2
        assert seen[0] & wu.CREATE_BREAKAWAY_FROM_JOB
        assert not seen[1] & wu.CREATE_BREAKAWAY_FROM_JOB
        sleep.assert_not_called()  # immediate fallback, no retry sleep

    def test_winerror32_still_retries_with_sleep(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wu.sys, 'platform', 'win32')
        calls: list[int] = []

        def popen(args, creationflags=0, **_kw):
            calls.append(creationflags)
            if len(calls) < 3:
                err = PermissionError(32, 'in use')
                err.winerror = 32
                raise err

        with (
            patch.object(wu.subprocess, 'Popen', side_effect=popen),
            patch.object(wu.time, 'sleep'),
        ):
            wu.run_installer(tmp_path / 's.exe')
        # Locked: only the first flag-set is attempted per round.
        assert all(c & wu.CREATE_BREAKAWAY_FROM_JOB for c in calls)
        assert len(calls) == 3


class TestBadPartCannotWedgeReview1:
    """Review SHOULD 8: a 416 or an oversized part is discarded and the
    download restarts from zero."""

    def test_416_discards_part_and_refetches(self, staged_root, tmp_path):
        # Serve the blob but refuse any Range with 416 (asset changed).
        class Only416(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.headers.get('Range'):
                    self.send_error(416)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(_BLOB)))
                self.end_headers()
                self.wfile.write(_BLOB)

            def log_message(self, *_a):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Only416)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/x'
            vdir = staged_root / '0.2.0'
            vdir.mkdir(parents=True)
            # A stale part LARGER than the asset — the resume could
            # never succeed.
            (vdir / wu.PART_NAME).write_bytes(_BLOB + b'garbage')
            path = wu.stage_installer(_offer(url))
            assert path.read_bytes() == _BLOB
        finally:
            server.shutdown()
            server.server_close()

    def test_oversized_part_never_requests_range(
        self, staged_root, http_server
    ):
        vdir = staged_root / '0.2.0'
        vdir.mkdir(parents=True)
        (vdir / wu.PART_NAME).write_bytes(_BLOB + b'x' * 10)
        _RangeHandler.seen_ranges = []
        path = wu.stage_installer(_offer(http_server))
        assert _RangeHandler.seen_ranges == []  # restarted from 0
        assert path.read_bytes() == _BLOB


class TestStageMustMatchTheOfferedBytes:
    """A re-uploaded release asset must not be served from a stale
    stage: the tag alone does not identify the bytes."""

    def test_stale_digest_meta_rejected(self, staged_root, tmp_path):
        src = tmp_path / 'a.exe'
        src.write_bytes(_BLOB)
        wu.stage_installer(_offer(str(src)))
        assert wu.staged_installer('0.2.0') is not None
        other = 'sha256:' + hashlib.sha256(b'other').hexdigest()
        assert wu.staged_installer('0.2.0', expected_sha=other) is None
        # The CURRENT offer's own digest/size still accept the stage.
        assert (
            wu.staged_installer(
                '0.2.0',
                expected_sha=_sha(_BLOB),
                expected_size=len(_BLOB),
            )
            is not None
        )

    def test_stale_size_meta_rejected(self, staged_root, tmp_path):
        src = tmp_path / 'a.exe'
        src.write_bytes(_BLOB)
        wu.stage_installer(_offer(str(src)))
        assert wu.staged_installer('0.2.0', expected_size=999) is None

    def test_tray_rejects_the_stale_stage(self, staged_root, tmp_path):
        src = tmp_path / 'a.exe'
        src.write_bytes(_BLOB)
        wu.stage_installer(_offer(str(src)))
        # The release now offers DIFFERENT bytes under the same tag.
        src2 = tmp_path / 'b.exe'
        src2.write_bytes(b'new' * 200)
        offer = {
            'version': '0.2.0',
            'url': str(src2),
            'size': src2.stat().st_size,
            'digest': 'sha256:' + hashlib.sha256(b'new' * 200).hexdigest(),
        }
        with (
            patch.object(wu, 'check_for_update', return_value=offer),
            patch.object(wu, 'run_installer') as run,
        ):
            res = wu.upgrade()
        assert res['status'] == 'updating'
        # It re-downloaded: the path passed to run_installer is NOT
        # the stale stage.
        launched = run.call_args[0][0]
        assert launched != wu.staged_paths('0.2.0')[0]
