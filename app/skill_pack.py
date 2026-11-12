"""Pack a skill DIRECTORY (or an in-memory tree) into an archive.

The in-task MCP server runs on the same machine as the agent, so it
can read the local skill directory the agent names — but the install
path the server enforces takes an ARCHIVE (``app.skill_uploads``), the
same artifact an external agent ``tar czf``s itself. This module is
the packer half of that contract: everything here turns a tree into
bytes that ``extract_archive`` will accept, so a local directory and a
fetched GitHub tree both arrive at the ONE install path as archives,
never as hand-assembled file payloads.

Walk rules mirror the extractor's, and are checked EARLY (before any
byte is read) so the refusal names the offending path instead of an
opaque mid-stream 4xx:

* ``SKILL.md`` must exist at the directory root (packed relative to
  the root — one root, no wrapper dir);
* symlinks are never followed out of the directory: a link whose
  target resolves outside the root is refused by name, a link to a
  file INSIDE the root is installed as a plain copy of that file's
  bytes (an archive carrying a symlink entry would be refused at
  extraction anyway); symlinked subdirectories are refused outright;
* only regular files are packed — devices, FIFOs and sockets are
  refused by name;
* the ``MAX_SKILL_*`` caps (the extractor still owns enforcement —
  same imported constants) are pre-checked here as a fast fail.

macOS junk (``._*``, ``.DS_Store``, ``__MACOSX/``) is deliberately
NOT filtered here: extraction skips it and reports it under
``ignored`` — one owner, so a local import and an uploaded archive
report the same list.
"""

import io
import os
from pathlib import Path
import stat
import tarfile
import time

from app.skill_uploads import entry_name
from app.workspace.skills_manager import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_TOTAL_BYTES,
)

SKILL_MD = 'SKILL.md'


class PackError(ValueError):
    """A directory or tree that must not be packed: the message names
    what is refused. ValueError subclass so callers map it like every
    other refused source."""


def pack_tar_gz(files: dict[str, bytes], executables: set[str]) -> bytes:
    """A tar.gz carrying exactly *files* (rel path -> bytes).

    The exec bits are written from *executables* (names must be in
    *files*), the format ``extract_archive`` reads back. Entry names
    go through :func:`app.skill_uploads.entry_name` shape checks so a
    caller handing in a traversal name fails at pack time.
    """
    now = int(time.time())
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for rel in sorted(files):
            name = entry_name(rel)
            data = files[rel]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = now
            info.mode = 0o755 if rel in executables else 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def pack_skill_dir(path: str | os.PathLike) -> bytes:
    """Pack the skill directory at *path* into a tar.gz.

    Returns the archive bytes; raises :class:`PackError` (a ValueError)
    naming the first thing that makes this directory not installable.
    Synchronous — the async callers run it in a thread.
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        raise PackError(f'not a directory: {path}')
    root_resolved = root.resolve()

    files: dict[str, bytes] = {}
    executables: set[str] = set()
    total = 0

    def _walk() -> None:
        nonlocal total
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            for dn in list(dirnames):
                link = here / dn
                if link.is_symlink():
                    raise PackError(
                        f'symlinked subdirectory is refused: {link!s} '
                        '(pack the directory it points at separately '
                        'if you meant to include it)'
                    )
            for fn in filenames:
                src = here / fn
                rel = src.relative_to(root).as_posix()
                if not rel:
                    continue
                if src.is_symlink():
                    target = src.resolve()
                    if not target.is_relative_to(root_resolved):
                        raise PackError(
                            f'symlink escapes the skill directory: '
                            f'{rel!r} -> {target}'
                        )
                    if not target.is_file():
                        raise PackError(
                            f'symlink does not point at a regular '
                            f'file: {rel!r} -> {target}'
                        )
                st = src.stat()
                if not stat.S_ISREG(st.st_mode):
                    raise PackError(
                        f'only regular files can be installed: {rel!r}'
                    )
                if st.st_size > MAX_SKILL_FILE_BYTES:
                    raise PackError(
                        f'{rel!r} is {st.st_size} bytes — the per-file '
                        f'limit is {MAX_SKILL_FILE_BYTES}'
                    )
                if len(files) + 1 > MAX_SKILL_FILES:
                    raise PackError(
                        f'a skill may carry at most {MAX_SKILL_FILES} files'
                    )
                if total + st.st_size > MAX_SKILL_TOTAL_BYTES:
                    raise PackError(
                        'this skill exceeds the total size limit of '
                        f'{MAX_SKILL_TOTAL_BYTES} bytes'
                    )
                try:
                    data = src.read_bytes()
                except OSError as exc:  # dangling link, race, perms
                    raise PackError(f'cannot read {rel!r}: {exc}') from exc
                total += len(data)
                files[rel] = data
                if st.st_mode & 0o111:
                    executables.add(rel)

    _walk()

    if SKILL_MD not in files:
        raise PackError(
            f'{path} has no {SKILL_MD} at its root — point at the '
            'skill directory itself (the one holding SKILL.md)'
        )
    return pack_tar_gz(files, executables)
