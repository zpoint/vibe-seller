"""Presigned skill-archive uploads.

An external agent importing a local skill must move the skill's BYTES
from its own machine to this server. Routing them through the MCP tool
arguments would push every byte (and every base64 character) through
the agent's LLM — token cost proportional to file size, and silent
corruption risk on long base64 strings. Instead the bytes travel as a
plain HTTP upload straight from the agent's shell to a presigned URL:

1. ``manage_skill(action=begin_upload)`` mints a short-lived,
   single-use, HMAC-signed ``put_url``;
2. the agent ``PUT``s the archive (``tar czf``/``zip`` of the skill
   directory) to that URL — the signature IS the authorization, so no
   API token ever appears in a shell command;
3. ``manage_skill(action=import, upload_id=…)`` extracts the archive
   and installs it through the ONE install path
   (``workspace.save_skill``, replace semantics).

Contracts held here, in one place:

* **Signing** — HMAC-SHA256 with a key DERIVED from the install's JWT
  secret (never the raw secret) over ``upload_id.user_id.expiry``.
  Short TTL (:data:`UPLOAD_TTL_SECONDS`), single use per upload id.
* **Staging** — streamed to ``DATA_DIR/skill-uploads/<id>/`` with the
  compressed-size cap enforced WHILE streaming (a big body is refused
  before it is all on disk); stale records are swept lazily, no new
  daemon.
* **Extraction** — no trust in the archive: regular files only (no
  symlinks, hardlinks, devices or FIFOs), safe relative names, no
  duplicates, byte caps from ``app.workspace.skills_manager``'s
  ``MAX_SKILL_*`` constants (the same numbers the install path
  enforces — one source), format sniffed from magic bytes. Extraction
  is purely in-memory: a rejected archive never touches the workspace.
* **Staged install** — :func:`import_staged` is the ONE composition
  every archive source runs (external ``manage_skill``, the in-task
  local-directory import, the URL import): lock the skills tree,
  extract-then-install in one thread hop through
  ``manager.save_skill_blocking``, auto-commit. A source that wanted
  its own copy of that sequence would be a second install path.
"""

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import time
import unicodedata
import uuid
import zipfile

from app.config import DATA_DIR, JWT_SECRET
from app.workspace import skills_tree
from app.workspace.skills_manager import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_TOTAL_BYTES,
    derive_skill_slug,
)

logger = logging.getLogger(__name__)

# How long a minted upload URL stays valid, and how long a record
# (and its staged archive) outlives that before the sweep removes it.
UPLOAD_TTL_SECONDS = 600
SWEEP_GRACE_SECONDS = 3600
# The COMPRESSED upload cap, enforced while streaming. Uncompressed
# caps come from the install path (MAX_SKILL_*), so a zip bomb is
# bounded there regardless of how well it compresses.
MAX_ARCHIVE_UPLOAD_BYTES = 32 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 256 * 1024

ACCEPTED_CONTENT_TYPES = frozenset({
    'application/gzip',
    'application/x-gzip',
    'application/x-tar',
    'application/zip',
})
FORMATS = ('tar.gz', 'tar', 'zip')

UPLOAD_ID_RE = re.compile(r'^[0-9a-f]{32}$')


class UploadError(Exception):
    """A refused upload: carries the HTTP status and a plain message."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ── Signing ─────────────────────────────────────────────────


def _signing_key() -> bytes:
    """The HMAC key, derived from the JWT secret.

    Derived (domain-separated) so a leaked signing key cannot forge
    a session cookie and vice versa.
    """
    return hmac.new(
        JWT_SECRET.encode('utf-8'),
        b'vibe-seller.skill-upload.v1',
        hashlib.sha256,
    ).digest()


def _signature(upload_id: str, user_id: str, expires_at: int) -> str:
    payload = f'{upload_id}.{user_id}.{expires_at}'.encode()
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def verify_upload_signature(
    upload_id: str, user_id: str, expires_at: int, signature: str
) -> bool:
    """Constant-time check of a put_url signature (not its expiry)."""
    if not isinstance(signature, str) or not signature:
        return False
    return hmac.compare_digest(
        _signature(upload_id, user_id, expires_at), signature
    )


# ── Records and staging ────────────────────────────────────


def _uploads_dir() -> Path:
    return DATA_DIR / 'skill-uploads'


def _upload_dir(upload_id: str) -> Path:
    """The upload's staging dir — only ever under ``skill-uploads/``.

    Every path builder funnels through here, and the id shape is
    validated FIRST: ``upload_id`` arrives from a tool argument, and
    an unchecked ``../`` in it would walk out of the staging dir.
    """
    if not UPLOAD_ID_RE.match(upload_id or ''):
        raise ValueError(f'Invalid upload id: {upload_id!r}')
    return _uploads_dir() / upload_id


def _state_path(upload_id: str) -> Path:
    return _upload_dir(upload_id) / 'state.json'


def _archive_path(upload_id: str) -> Path:
    return _upload_dir(upload_id) / 'archive.bin'


def archive_path(upload_id: str) -> Path:
    """Where a staged archive lives on disk (the import path reads it)."""
    if not UPLOAD_ID_RE.match(upload_id or ''):
        raise UploadError(404, 'Unknown upload')
    return _archive_path(upload_id)


def _load_record(upload_id: str) -> dict | None:
    try:
        data = json.loads(_state_path(upload_id).read_text('utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_record(record: dict) -> None:
    path = _state_path(record['upload_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record), encoding='utf-8')
    os.replace(tmp, path)


def sweep_stale() -> int:
    """Drop upload records (and staged bytes) past their lifetime.

    Called on ``begin_upload`` and ``import`` — the only two places
    that touch uploads — so nothing accumulates without a daemon.
    """
    root = _uploads_dir()
    if not root.is_dir():
        return 0
    cutoff = time.time() - (UPLOAD_TTL_SECONDS + SWEEP_GRACE_SECONDS)
    removed = 0
    for entry in root.iterdir():
        try:
            record = _load_record(entry.name)
            created = (record or {}).get('created_at', 0)
            stale = record is None or float(created) < cutoff
        except ValueError:
            # Not an upload id we ever minted — treat it like the
            # corrupt-record case: staging is ours alone, it is junk.
            stale = True
        if stale:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def create_upload(user_id: str, base_url: str) -> dict:
    """Mint a presigned single-use upload, ready to ``PUT`` to."""
    sweep_stale()
    upload_id = uuid.uuid4().hex
    now = int(time.time())
    expires_at = now + UPLOAD_TTL_SECONDS
    record = {
        'upload_id': upload_id,
        'user_id': user_id,
        'expires_at': expires_at,
        'created_at': now,
        'status': 'pending',
        'size': None,
        'format': None,
    }
    _save_record(record)
    signature = _signature(upload_id, user_id, expires_at)
    put_path = (
        f'/api/skill-uploads/{upload_id}'
        f'?uid={user_id}&exp={expires_at}&sig={signature}'
    )
    put_url = f'{base_url.rstrip("/")}{put_path}'
    return {
        'upload_id': upload_id,
        'put_url': put_url,
        'put_path': put_path,
        'expires_at': datetime.fromtimestamp(expires_at, UTC).isoformat(),
        'max_bytes': MAX_ARCHIVE_UPLOAD_BYTES,
        'formats': list(FORMATS),
        'curl': (
            f"COPYFILE_DISABLE=1 tar czf - -C '<skill dir>' . | "
            f'curl -fsS -X PUT '
            f"--data-binary @- -H 'Content-Type: application/gzip' "
            f"'{put_url}'\n"
            f"cd '<skill dir>' && zip -Xqr - . | curl -fsS -X PUT "
            f"--data-binary @- -H 'Content-Type: application/zip' "
            f"'{put_url}'"
        ),
    }


def check_upload_request(
    upload_id: str,
    user_id: str | None,
    expires_at: int,
    signature: str | None,
) -> dict:
    """Validate a put_url before any byte is read.

    Raises :class:`UploadError` (403 bad/expired/reused signature,
    404 unknown upload); returns the record when the request is good.
    """
    if not UPLOAD_ID_RE.match(upload_id or ''):
        raise UploadError(404, 'Unknown upload')
    record = _load_record(upload_id)
    if record is None:
        raise UploadError(404, 'Unknown upload')
    if not verify_upload_signature(
        upload_id, user_id or '', expires_at, signature or ''
    ):
        raise UploadError(403, 'Invalid upload signature')
    if record.get('user_id') != user_id:
        raise UploadError(403, 'This upload belongs to another user')
    if int(expires_at) < int(time.time()):
        raise UploadError(403, 'Upload URL has expired')
    if record.get('status') != 'pending':
        raise UploadError(403, 'Upload URL already used')
    return record


def _claim(upload_id: str) -> None:
    """Atomically claim the upload before a body byte is read.

    Single-use must be decided in ONE step: two concurrent PUTs that
    both read ``status=pending`` would otherwise both stream. The
    claim file is created with ``O_CREAT|O_EXCL`` — the OS makes it
    exactly one winner — and is released when the attempt ends,
    successfully or not, so a failed attempt can be retried while the
    record's own status still refuses a re-USE.
    """
    try:
        fd = os.open(
            _upload_dir(upload_id) / 'claim',
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as exc:
        raise UploadError(
            409, 'An upload for this id is already in progress'
        ) from exc
    os.close(fd)


def _release_claim(upload_id: str) -> None:
    (_upload_dir(upload_id) / 'claim').unlink(missing_ok=True)


async def stage_stream(upload_id: str, chunks, content_type: str) -> dict:
    """Stream the archive bytes to staging with the cap enforced inline.

    *chunks* is an async iterator of ``bytes``. The body is refused
    (413) the moment it passes :data:`MAX_ARCHIVE_UPLOAD_BYTES`, so
    an oversize upload is never fully written down. The upload is
    CLAIMED atomically before the first byte (see :func:`_claim`), so
    a concurrent second PUT is refused instead of racing.

    One clear shape for the disk work: every file operation — the
    claim, the open, each chunk write, the sniff, the record save —
    is handed to a thread (``asyncio.to_thread``). The event loop
    only counts bytes and enforces the cap, so a multi-megabyte
    upload never blocks the server's other requests.
    """
    record = await asyncio.to_thread(_load_record, upload_id)
    if record is None:
        raise UploadError(404, 'Unknown upload')
    if record.get('status') != 'pending':
        raise UploadError(403, 'Upload URL already used')
    if content_type.split(';')[0].strip().lower() not in (
        ACCEPTED_CONTENT_TYPES | {'application/octet-stream'}
    ):
        raise UploadError(
            415,
            'Content-Type must be one of: '
            + ', '.join(sorted(ACCEPTED_CONTENT_TYPES)),
        )
    dest = await asyncio.to_thread(_prepare_staging, upload_id)
    total = 0
    try:
        out = await asyncio.to_thread(open, dest, 'wb')
        try:
            async for chunk in chunks:
                total += len(chunk)
                if total > MAX_ARCHIVE_UPLOAD_BYTES:
                    raise UploadError(
                        413,
                        'Archive exceeds the '
                        f'{MAX_ARCHIVE_UPLOAD_BYTES} byte upload limit',
                    )
                await asyncio.to_thread(out.write, chunk)
        finally:
            await asyncio.to_thread(out.close)
        fmt = await asyncio.to_thread(sniff_format, dest)
        if fmt is None:
            raise UploadError(
                415,
                'Unrecognised archive format — send .tar.gz, .tar or '
                '.zip (magic bytes decide, not the file name)',
            )
        record.update({'status': 'staged', 'size': total, 'format': fmt})
        await asyncio.to_thread(_save_record, record)
    except Exception:
        await asyncio.to_thread(_discard_staging, dest)
        raise
    finally:
        await asyncio.to_thread(_release_claim, upload_id)
    return {
        'upload_id': upload_id,
        'size': total,
        'format': fmt,
        'status': 'staged',
    }


def _prepare_staging(upload_id: str) -> Path:
    """Claim the upload and return its staging path (sync target)."""
    dest = _archive_path(upload_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _claim(upload_id)
    return dest


def _discard_staging(dest: Path) -> None:
    dest.unlink(missing_ok=True)


def staged_record(upload_id: str, user_id: str) -> dict:
    """The staged record for an import — or the reason there is none.

    The import gate: correct shape, the caller's own upload, actually
    staged, and inside its lifetime — an import is refused on every
    one of those, never guessed at.
    """
    if not UPLOAD_ID_RE.match(upload_id or ''):
        raise UploadError(404, 'Unknown upload')
    record = _load_record(upload_id)
    if record is None:
        raise UploadError(404, 'Unknown upload')
    if record.get('user_id') != user_id:
        raise UploadError(403, 'This upload belongs to another user')
    if int(record.get('expires_at') or 0) < int(time.time()):
        raise UploadError(403, 'Upload URL has expired')
    if record.get('status') == 'pending':
        raise UploadError(400, 'Nothing has been uploaded yet')
    if record.get('status') != 'staged':
        raise UploadError(403, 'Upload already consumed')
    return record


def consume_upload(upload_id: str) -> None:
    """Consume a staged upload: the bytes go, the refusal to reuse stays."""
    if not UPLOAD_ID_RE.match(upload_id or ''):
        raise UploadError(404, 'Unknown upload')
    _archive_path(upload_id).unlink(missing_ok=True)
    record = _load_record(upload_id)
    if record is not None:
        record['status'] = 'consumed'
        _save_record(record)


# ── Format sniffing ────────────────────────────────────────


def sniff_format(path: Path) -> str | None:
    """The archive format by MAGIC BYTES — never the file name."""
    with open(path, 'rb') as handle:
        head = handle.read(512)
    if head.startswith(b'\x1f\x8b'):
        return 'tar.gz'
    if head.startswith(b'PK\x03\x04') or head.startswith(b'PK\x05\x06'):
        return 'zip'
    # Plain tar (ustar or the older v7 layout): the only way to know
    # is to parse it — tar has no leading magic.
    try:
        with tarfile.open(path, 'r:') as probe:
            next(iter(probe), None)
        return 'tar'
    except (tarfile.TarError, OSError, EOFError):
        return None


# ── Extraction (the safety core) ───────────────────────────


# How many skipped-junk names get REPORTED to the caller. The count is
# always the truth; the list is a sample — a 32 MB archive packed by
# Finder can carry hundreds of thousands of `._*` entries, and
# shipping them all would re-serialize the flood into the agent's
# tool result for zero information past the first page.
MAX_IGNORED_REPORT = 1000


@dataclass
class ExtractedSkill:
    """What an archive holds: SKILL.md text + files + exec-bit names.

    ``skill_md`` is validated UTF-8 text (the install path re-encodes
    it exactly); every other file stays raw ``bytes`` and is handed to
    the install path as base64 so it lands byte-identical. ``ignored``
    names the macOS junk that was skipped — reported, never silent,
    sampled at :data:`MAX_IGNORED_REPORT` with ``ignored_count``
    carrying the true total.
    """

    skill_md: str
    files: dict[str, bytes] = field(default_factory=dict)
    executables: set[str] = field(default_factory=set)
    ignored: list[str] = field(default_factory=list)
    ignored_count: int = 0


def is_macos_junk(name: str) -> bool:
    """macOS packaging junk: skipped at extraction, listed in ``ignored``.

    ``tar``/``zip`` on macOS sprinkle AppleDouble ``._*`` files, a
    ``__MACOSX/`` shadow tree and ``.DS_Store`` into archives. None of
    them are skill content, and a strict extractor would otherwise
    refuse the whole import over them — so they are dropped here and
    REPORTED, because a silently different tree is a lie.
    """
    parts = name.split('/')
    base = parts[-1]
    return '__MACOSX' in parts or base.startswith('._') or base == '.DS_Store'


def entry_name(name: str) -> str:
    """Normalise an archive entry name to a clean relative path.

    Only the shape is checked here (plus the Windows-drive case the
    workspace path rule does not see from a filesystem caller); the
    FULL path contract stays in ``skills_manager`` — one owner.
    """
    name = name.replace('\\', '/')
    while name.startswith('./'):
        name = name[2:]
    if not name or name in ('.', '/'):
        raise ValueError('empty archive entry name')
    if name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ValueError(f'archive entry escapes the root: {name!r}')
    if any(part == '..' for part in name.split('/')):
        raise ValueError(f'archive entry escapes the root: {name!r}')
    return name


def _claim_case(name: str, seen: dict) -> None:
    """Refuse two entries whose paths collide on a normalizing host.

    Case is the obvious collision (macOS/Windows fold it), but APFS
    also normalizes Unicode: ``café.md`` in NFC and in NFD are
    different strings yet ONE file on macOS — the second install
    write would silently win there and make TWO files on ext4, so one
    archive would install a different tree per host OS. The collision
    key is therefore NFC + casefold. The SKILL.md variant rule is the
    same idea, applied to the one name that must be exact.
    """
    key = unicodedata.normalize('NFC', name).casefold()
    prior = seen.get(key)
    if prior is not None and prior != name:
        raise ValueError(
            f'archive entries collide by case/normalization: '
            f'{prior!r} vs {name!r}'
        )
    seen[key] = name


def _check_caps(count: int, size: int, total: int) -> None:
    if count > MAX_SKILL_FILES:
        raise ValueError(f'archive holds more than {MAX_SKILL_FILES} files')
    if size > MAX_SKILL_FILE_BYTES:
        raise ValueError(
            f'archive entry is {size} bytes — the per-file limit is '
            f'{MAX_SKILL_FILE_BYTES}'
        )
    if total > MAX_SKILL_TOTAL_BYTES:
        raise ValueError(
            'archive exceeds the total size limit of '
            f'{MAX_SKILL_TOTAL_BYTES} bytes'
        )


def _strip_common_root(files: dict, execs: set) -> tuple[dict, set]:
    """Drop one shared top-level directory, if there is exactly one.

    The exec-bit names follow the files through the same rename, or
    they would point at paths that no longer exist.
    """
    if not files:
        return files, execs
    tops = {name.split('/', 1)[0] for name in files}
    nested = all('/' in name for name in files)
    if len(tops) == 1 and nested:
        root = tops.pop()

        def _cut(name: str) -> str:
            return name[len(root) + 1 :]

        return (
            {_cut(name): data for name, data in files.items()},
            {_cut(name) for name in execs},
        )
    return files, execs


def _read_tar(path: Path) -> tuple[dict, set, list]:
    files: dict[str, bytes] = {}
    execs: set[str] = set()
    ignored: list[str] = []
    seen: dict[str, str] = {}
    total = 0
    with tarfile.open(path, 'r:*') as tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(
                    f'unsupported archive entry (links, devices and '
                    f'FIFOs are refused): {member.name!r}'
                )
            name = entry_name(member.name)
            if is_macos_junk(name):
                ignored.append(name)
                continue
            _claim_case(name, seen)
            if name in files:
                raise ValueError(f'duplicate archive entry: {name!r}')
            _check_caps(len(files) + 1, member.size, total + member.size)
            handle = tar.extractfile(member)
            data = handle.read() if handle else b''
            total += len(data)
            _check_caps(len(files) + 1, len(data), total)
            files[name] = data
            if member.mode & 0o111:
                execs.add(name)
    return files, execs, ignored


def _read_zip(path: Path) -> tuple[dict, set, list]:
    files: dict[str, bytes] = {}
    execs: set[str] = set()
    ignored: list[str] = []
    seen: dict[str, str] = {}
    with zipfile.ZipFile(path) as zf:
        total = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(
                    f'unsupported archive entry (symlinks are '
                    f'refused): {info.filename!r}'
                )
            name = entry_name(info.filename)
            if is_macos_junk(name):
                ignored.append(name)
                continue
            _claim_case(name, seen)
            if name in files:
                raise ValueError(f'duplicate archive entry: {name!r}')
            data = bytearray()
            with zf.open(info) as part:
                while True:
                    chunk = part.read(256 * 1024)
                    if not chunk:
                        break
                    data.extend(chunk)
                    # The declared size can lie: bound what is
                    # actually decompressed, not just the header.
                    _check_caps(
                        len(files) + 1,
                        len(data),
                        total + len(data),
                    )
            total += len(data)
            files[name] = bytes(data)
            if mode & 0o111:
                execs.add(name)
    return files, execs, ignored


def extract_archive(path: Path, fmt: str) -> ExtractedSkill:
    """Extract an archive in memory, strictly.

    Rejects anything that is not a plain file tree inside the root
    (links, devices, traversal names, duplicates, oversized entries),
    strips one common top-level directory, then requires ``SKILL.md``
    at the resulting root — exactly that name (a case-variant is
    refused, it would collide on case-insensitive filesystems) and
    valid UTF-8 (it is the skill's instructions). Every other file is
    kept as raw bytes and is stored byte-identical by the install
    path; executable bits come from the archive itself.
    """
    if fmt == 'zip':
        try:
            files, execs, ignored = _read_zip(path)
        except zipfile.BadZipFile as exc:
            raise ValueError(f'Unreadable archive: {exc}') from exc
    elif fmt in ('tar.gz', 'tar'):
        try:
            files, execs, ignored = _read_tar(path)
        except tarfile.TarError as exc:
            raise ValueError(f'Unreadable archive: {exc}') from exc
    else:
        raise ValueError(f'Unsupported archive format: {fmt!r}')

    files, execs = _strip_common_root(files, execs)
    for name in files:
        base = name.rsplit('/', 1)[-1]
        if base.lower() == 'skill.md' and base != 'SKILL.md':
            raise ValueError(
                f'archive holds a case-variant of SKILL.md: {name!r}'
            )
    if 'SKILL.md' not in files:
        # Honest refusal: a Finder-packed archive may hold its ONLY
        # SKILL.md inside the __MACOSX junk — name that case instead
        # of pointing at a root the packer did fill.
        junk_md = any(name.rsplit('/', 1)[-1] == 'SKILL.md' for name in ignored)
        hint = (
            ' (the archive only carries SKILL.md inside macOS '
            'packaging junk — repack with COPYFILE_DISABLE=1 '
            'tar czf - -C <dir> .)'
            if junk_md
            else ''
        )
        raise ValueError(
            'archive has no SKILL.md at its root — pack the skill '
            f'directory itself{hint}'
        )
    try:
        skill_md = files.pop('SKILL.md').decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('SKILL.md must be valid UTF-8') from exc
    execs.discard('SKILL.md')
    _check_caps(len(files) + 1, 0, sum(len(v) for v in files.values()))
    return ExtractedSkill(
        skill_md=skill_md,
        files=files,
        executables={e for e in execs if e in files},
        ignored=sorted(ignored)[:MAX_IGNORED_REPORT],
        ignored_count=len(ignored),
    )


# ── Staged install (archive → skill, the ONE path) ─────────


def install_staged_blocking(
    manager,
    upload_id: str,
    user_id: str,
    *,
    slug: str | None = None,
    origin_url: str = '',
    overwrite: bool = False,
) -> tuple[dict, bool]:
    """Sync target shared by EVERY archive source.

    Reads the staged record, extracts the archive strictly, derives
    the slug (SKILL.md frontmatter name unless one was given) and
    installs the tree through ``manager.save_skill_blocking`` — the
    same install core the inline save path uses. The upload is
    consumed only after the install succeeded, so a refused install
    can be retried without re-uploading; skipped macOS junk is
    REPORTED (``ignored``), never silent.

    Raises :class:`UploadError` (unknown/unowned/stale upload) or
    ``ValueError`` (refused archive or install) — callers map them to
    their own error shape.
    """
    record = staged_record(upload_id, user_id)
    extracted = extract_archive(archive_path(upload_id), record['format'])
    slug = slug or derive_skill_slug(extracted.skill_md)
    if not slug:
        raise ValueError(
            'SKILL.md carries no frontmatter name to derive the '
            'skill slug from — pass slug explicitly'
        )
    result, existed = manager.save_skill_blocking(
        slug,
        extracted.skill_md,
        files_bin={
            rel: base64.b64encode(data).decode('ascii')
            for rel, data in extracted.files.items()
        },
        executables=sorted(extracted.executables),
        origin_url=origin_url,
        replace=True,
        overwrite=overwrite,
    )
    consume_upload(upload_id)
    # macOS packaging junk is skipped at extraction — say so, never
    # silently hand back a different tree than the one packed. The
    # list is the sample, the count the truth (MAX_IGNORED_REPORT).
    result['ignored'] = extracted.ignored
    result['ignored_count'] = extracted.ignored_count
    return result, existed


async def import_staged(
    manager,
    upload_id: str,
    user_id: str,
    *,
    slug: str | None = None,
    origin_url: str = '',
    overwrite: bool = False,
) -> dict:
    """The ONE archive→skill journey: lock, extract+install in ONE
    thread hop, auto-commit outside the lock.

    Every caller (external ``manage_skill``, the in-task local
    directory route, the URL route) stages bytes first and ends here
    — there is no second install composition.
    """
    async with skills_tree.hold():
        result, existed = await asyncio.to_thread(
            install_staged_blocking,
            manager,
            upload_id,
            user_id,
            slug=slug,
            origin_url=origin_url,
            overwrite=overwrite,
        )
    await manager._auto_commit(
        f'{"Update" if existed else "Create"} skill: {result["slug"]}'
    )
    return result
