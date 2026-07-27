"""Unit tests: the backend log is bounded.

``--dev`` sets ``LOG_LEVEL=DEBUG`` (per-statement aiosqlite/httpcore/
websockets chatter) and the handler used to be a plain ``FileHandler``,
so a long dev session produced a 5 GB ``backend_<port>.log``. Rotation
must fire on size OR age, and the on-disk total must stay under
``LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1)``.
"""

import logging
import time

import pytest

from app.logging_setup import (
    SizeAndTimeRotatingFileHandler,
    build_file_handler,
)

pytestmark = pytest.mark.unit


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord('test', logging.INFO, __file__, 1, msg, None, None)


def _emit(handler: logging.Handler, n: int, payload: str) -> None:
    for _ in range(n):
        handler.emit(_record(payload))


class TestSizeRotation:
    def test_stays_bounded_across_MANY_rollovers(self, tmp_path):
        """The ceiling must hold for a log that rolls over and over.

        Regression guard: bolting a size check onto
        ``TimedRotatingFileHandler`` passed a single-rollover test and
        then silently stopped rotating — since Python 3.12 its
        ``doRollover`` returns early when the timestamped backup already
        exists, which is every subsequent roll inside the same window.
        The file grew unbounded while ``shouldRollover`` kept saying yes.
        """
        log = tmp_path / 'backend.log'
        handler = SizeAndTimeRotatingFileHandler(
            log, max_bytes=2000, rotate_seconds=7 * 86400, backup_count=1
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        try:
            _emit(handler, 400, 'x' * 100)  # ~40 KB >> 2 KB cap
        finally:
            handler.close()

        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ['backend.log', 'backend.log.1'], names
        total = sum(p.stat().st_size for p in tmp_path.iterdir())
        assert total <= 2000 * 2, f'total {total} exceeds max_bytes*(n+1)'

    def test_backup_count_zero_keeps_only_current(self, tmp_path):
        log = tmp_path / 'backend.log'
        handler = SizeAndTimeRotatingFileHandler(
            log, max_bytes=2000, rotate_seconds=0, backup_count=0
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        try:
            _emit(handler, 200, 'x' * 100)
        finally:
            handler.close()
        assert [p.name for p in tmp_path.iterdir()] == ['backend.log']
        assert log.stat().st_size <= 2000

    def test_no_rollover_below_cap(self, tmp_path):
        log = tmp_path / 'backend.log'
        handler = SizeAndTimeRotatingFileHandler(
            log, max_bytes=10 * 1024, rotate_seconds=7 * 86400, backup_count=1
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        try:
            _emit(handler, 5, 'small')
        finally:
            handler.close()
        assert [p.name for p in tmp_path.iterdir()] == ['backend.log']


class TestTimeRotation:
    def test_rolls_on_age_even_when_small(self, tmp_path):
        """A quiet week must still roll — size alone would never fire."""
        log = tmp_path / 'backend.log'
        handler = SizeAndTimeRotatingFileHandler(
            log,
            max_bytes=10**9,
            rotate_seconds=7 * 86400,
            backup_count=1,
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        try:
            _emit(handler, 1, 'before')
            handler.rollover_at = time.time() - 1  # the week elapsed
            _emit(handler, 1, 'after')
        finally:
            handler.close()

        assert sorted(p.name for p in tmp_path.iterdir()) == [
            'backend.log',
            'backend.log.1',
        ]
        assert log.read_text().strip() == 'after'
        assert (tmp_path / 'backend.log.1').read_text().strip() == 'before'
        # Clock re-armed, so it does not roll on every subsequent record.
        assert handler.rollover_at > time.time()


class TestBuildFileHandler:
    def test_defaults_are_bounded(self, tmp_path, monkeypatch):
        monkeypatch.delenv('LOG_MAX_BYTES', raising=False)
        monkeypatch.delenv('LOG_ROTATE_DAYS', raising=False)
        monkeypatch.delenv('LOG_BACKUP_COUNT', raising=False)
        h = build_file_handler(tmp_path / 'b.log')
        try:
            assert isinstance(h, SizeAndTimeRotatingFileHandler)
            assert h.max_bytes == 5 * 1024**3  # 5 GiB
            assert h.backup_count == 1  # → 10 GiB ceiling
            assert h.rotate_seconds == 7 * 86400  # → 2 weeks
        finally:
            h.close()

    def test_both_zero_disables_rotation(self, tmp_path, monkeypatch):
        monkeypatch.setenv('LOG_MAX_BYTES', '0')
        monkeypatch.setenv('LOG_ROTATE_DAYS', '0')
        h = build_file_handler(tmp_path / 'b.log')
        try:
            assert type(h) is logging.FileHandler
        finally:
            h.close()

    def test_size_only_when_days_zero(self, tmp_path, monkeypatch):
        """days=0 must not mean 'roll constantly' — size still governs."""
        monkeypatch.setenv('LOG_MAX_BYTES', '4096')
        monkeypatch.setenv('LOG_ROTATE_DAYS', '0')
        h = build_file_handler(tmp_path / 'b.log')
        try:
            assert isinstance(h, SizeAndTimeRotatingFileHandler)
            assert h.max_bytes == 4096
            # Age trigger disabled outright, so only size governs.
            assert h.rotate_seconds == 0
            assert h.rollover_at == 0.0
            assert h.shouldRollover(_record('tiny')) == 0
        finally:
            h.close()
