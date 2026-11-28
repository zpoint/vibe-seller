"""A private browser-harness runtime whose AF_UNIX paths always fit."""

import hashlib
import os
from pathlib import Path
import stat

# browser-use permits a 64-character BU_NAME. Its shared runtime layout adds
# "/bu-" and ".sock"; macOS sun_path holds 104 bytes including the final NUL.
_MAX_SESSION_NAME = 64
_MAX_SOCKET_PATH_BYTES = 103


def _socket_path_fits(runtime: Path) -> bool:
    socket = runtime.resolve() / f'bu-{"x" * _MAX_SESSION_NAME}.sock'
    return len(os.fsencode(socket)) <= _MAX_SOCKET_PATH_BYTES


def browser_runtime_dir(home: Path) -> Path:
    """Return the daemon dir, falling back to a bounded path for long homes.

    Keep ``$VIBE_HOME/r`` whenever every legal socket fits, so existing
    installs and their reaper state do not move. On POSIX a longer home gets
    a deterministic per-user, per-home directory under /tmp. With a 14-hex
    suffix its resolved macOS path is at most 103 bytes even after /tmp
    resolves to /private/tmp.
    """
    legacy = home / 'r'
    if os.name != 'posix' or _socket_path_fits(legacy):
        legacy.mkdir(parents=True, exist_ok=True)
        return legacy

    identity = f'{os.getuid()}\0{home.resolve()}'.encode()
    digest = hashlib.sha256(identity).hexdigest()[:14]
    runtime = Path('/tmp') / f'vs-{digest}'
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f'unsafe browser runtime directory: {runtime}')
    runtime.chmod(0o700)
    if not _socket_path_fits(runtime):  # pragma: no cover - fixed by contract
        raise RuntimeError(
            f'browser runtime socket path is too long: {runtime}'
        )
    return runtime
