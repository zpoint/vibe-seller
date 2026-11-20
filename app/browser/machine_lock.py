"""A machine-wide lock owned by the OS: one holder across processes.

``flock`` (POSIX) / ``msvcrt.locking`` (Windows) on byte 0 of a file in the
user's home. The OS drops the lock when the holding process exits — clean
exit, crash, ``kill -9``, a server restart — so it can never be left stuck
the way a lock *file* or a pid file can: the file may stay on disk, the
lock does not. Same mechanism as the update-apply lock in
``app/pip_update.py``.

Used where the thing being protected is machine-wide rather than
per-server: the Ziniao client (one per user, whatever ``VIBE_HOME`` each
server uses), so two servers — or the old and new process of a restart —
never kill and launch it at the same time.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

from app.platform import IS_WINDOWS


class MachineLockTimeout(TimeoutError):
    """Someone else held the lock for the whole wait."""

    def __init__(self, path: Path, holder: str, waited: float):
        super().__init__(f'{path.name} held by pid {holder} for {waited:.0f}s')
        self.holder = holder
        self.waited = waited


def _lock_byte0(fd: int) -> None:
    """Take byte 0 of ``fd`` exclusively without blocking (OSError if held)."""
    if IS_WINDOWS:
        import msvcrt  # noqa: PLC0415 — Windows-only module

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    import fcntl  # noqa: PLC0415 — POSIX-only module

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


class MachineLock:
    """``acquire`` waits (bounded), ``release`` frees; the OS frees it on
    process exit regardless."""

    POLL_S = 0.25

    def __init__(self, name: str, directory: Path | None = None):
        self.path = (directory or Path.home()) / f'.vibe-seller-{name}.lock'
        self._fd: int | None = None

    def _try(self) -> bool:
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock_byte0(fd)
        except OSError:
            os.close(fd)
            return False
        try:
            # The holder's pid, for the message a waiter shows. Written
            # from offset 1: on Windows the locked byte 0 is unreadable to
            # anyone else.
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, f'{os.getpid()}\n'.encode())
        except OSError:
            pass
        self._fd = fd
        return True

    def _holder(self) -> str:
        try:
            with open(self.path, 'rb') as f:
                f.seek(1)
                raw = f.read(32).decode('ascii', 'replace')
        except OSError:
            return '?'
        return raw.split('\n', 1)[0].strip() or '?'

    async def acquire(self, timeout: float) -> bool:
        """Take the lock; True when somebody else had to be waited for.

        Raises ``MachineLockTimeout`` once ``timeout`` seconds pass.
        """
        started = time.monotonic()
        waited = False
        while True:
            if await asyncio.to_thread(self._try):
                return waited
            waited = True
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                holder = await asyncio.to_thread(self._holder)
                raise MachineLockTimeout(self.path, holder, elapsed)
            await asyncio.sleep(self.POLL_S)

    def release(self) -> None:
        """Free the lock (closing the descriptor releases it)."""
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
