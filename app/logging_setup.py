"""Bounded logging for the backend.

``--dev`` sets ``LOG_LEVEL=DEBUG``, which turns on per-statement chatter
from ``aiosqlite``/``httpcore``/``websockets`` — a long dev session with
a few concurrent tasks produced a **5 GB** ``backend_<port>.log``. The
handler was a plain ``logging.FileHandler``, so nothing ever bounded it.

Rotation fires on size **or** age, whichever comes first, because either
alone misses a real case: a quiet week still ages out, and a busy
afternoon still blows the size cap.

Why not subclass ``TimedRotatingFileHandler`` and bolt a size check onto
``shouldRollover``: since Python 3.12 its ``doRollover`` returns early
when the timestamped backup already exists ("already rolled over"). A
second size-triggered rollover inside the same time window computes the
same name, hits that guard, and silently does nothing — the file grows
unbounded again while ``shouldRollover`` keeps saying yes. So this owns
rotation outright, with plain numbered backups.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
import time

from app.env_options import Options


class SizeAndTimeRotatingFileHandler(logging.handlers.BaseRotatingHandler):
    """Roll on size OR age; keep ``backup_count`` numbered backups.

    Backups are ``<log>.1`` (newest) … ``<log>.<backup_count>``; the
    oldest is deleted as the others shift down, so the on-disk total is
    bounded by ``max_bytes * (backup_count + 1)``.

    The age anchor is the current file's mtime at construction (or now,
    for a fresh file), and resets on every rollover. A restart therefore
    re-anchors the clock — the size cap is the hard guarantee, age is
    best-effort housekeeping for a log too quiet to ever hit the cap.
    """

    def __init__(
        self,
        filename,
        *,
        max_bytes: int = 0,
        rotate_seconds: float = 0,
        backup_count: int = 1,
        encoding: str | None = None,
        delay: bool = False,
    ):
        super().__init__(filename, 'a', encoding=encoding, delay=delay)
        self.max_bytes = max_bytes
        self.rotate_seconds = rotate_seconds
        self.backup_count = backup_count
        self.rollover_at = self._compute_rollover(initial=True)

    def _compute_rollover(self, *, initial: bool = False) -> float:
        if self.rotate_seconds <= 0:
            return 0.0
        anchor = time.time()
        if initial:
            try:
                st = os.stat(self.baseFilename)
                if st.st_size > 0:
                    anchor = st.st_mtime
            except OSError:
                pass
        return anchor + self.rotate_seconds

    def shouldRollover(self, record: logging.LogRecord) -> int:  # noqa: N802
        if self.rollover_at and time.time() >= self.rollover_at:
            return 1
        if self.max_bytes <= 0:
            return 0
        if self.stream is None:
            self.stream = self._open()
        try:
            self.stream.seek(0, os.SEEK_END)
            projected = self.stream.tell() + len(self.format(record)) + 1
        except (OSError, ValueError):
            return 0
        return 1 if projected >= self.max_bytes else 0

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None

        if self.backup_count > 0:
            # Drop the oldest, then shift every backup one slot down.
            oldest = f'{self.baseFilename}.{self.backup_count}'
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except OSError:
                    pass
            for i in range(self.backup_count - 1, 0, -1):
                src = f'{self.baseFilename}.{i}'
                dst = f'{self.baseFilename}.{i + 1}'
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except OSError:
                        pass
            try:
                os.replace(self.baseFilename, f'{self.baseFilename}.1')
            except OSError:
                pass
        else:
            # No backups wanted — just start over.
            try:
                os.remove(self.baseFilename)
            except OSError:
                pass

        self.rollover_at = self._compute_rollover()
        if not self.delay:
            self.stream = self._open()


def build_file_handler(log_file: Path) -> logging.Handler:
    """File handler for the backend log, bounded per the LOG_* options.

    ``LOG_MAX_BYTES=0`` and ``LOG_ROTATE_DAYS=0`` together disable
    rotation entirely (plain ``FileHandler``) for anyone who wants the
    old unbounded behaviour.
    """
    max_bytes = Options.LOG_MAX_BYTES.get_int()
    rotate_days = Options.LOG_ROTATE_DAYS.get_int()
    backup_count = Options.LOG_BACKUP_COUNT.get_int()

    if max_bytes <= 0 and rotate_days <= 0:
        return logging.FileHandler(str(log_file))

    return SizeAndTimeRotatingFileHandler(
        str(log_file),
        max_bytes=max(0, max_bytes),
        rotate_seconds=max(0, rotate_days) * 86400,
        backup_count=max(0, backup_count),
        encoding='utf-8',
    )
