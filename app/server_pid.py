"""When the pid file `vibe-seller start` wrote still names the server.

`start` writes ``backend_<port>.pid`` about two seconds AFTER spawning
the daemon. A release that dies at the end of importing the app
outlives that check, so the file can be written for a process that is
about to die; and Windows hands a dead PID to the next new process
within seconds — on Windows installer leg s5c, very likely the very
`vibe-seller start` bringing the old version back, which then refused
with "already running" and left the machine serving nothing. So the
PID counts only while its process is the one the file was written for:
alive, and created before the file.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from app.platform import process_started_after

#: The daemon is created ~2 s before the file's mtime; this is the
#: slack for clocks that disagree by a moment.
WRITE_SLACK_SECONDS = 1.0


def read_server_pid(path: Path, alive: Callable[[int], bool]) -> int | None:
    """The PID in *path* if it is still that server, else ``None``.

    A file that no longer names a running server is removed, so the
    next `start` is not refused by it.
    """
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        written_at = path.stat().st_mtime
    except (ValueError, OSError):
        return None
    if not alive(pid) or process_started_after(
        pid, written_at + WRITE_SLACK_SECONDS
    ):
        with suppress(OSError):
            path.unlink()
        return None
    return pid
