"""Fetch, verify and unpack a workplace CLI's release files.

Blocking by design — callers hop to a thread once per operation
(``asyncio.to_thread``), the same rule as every other download.

Every request goes through ``system_proxy.urlopen``: the network path
(exported proxy, the OS proxy, or direct) that actually reaches that
host. Each artifact is a list of candidate URLs tried in order — the
official host first, then its mirror (npmmirror, Gitee) — so a GitHub
the machine cannot reach falls through to a mirror that serves the
same bytes; the checksum, not the host, is what is trusted.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator, Sequence
import hashlib
import http.client
import io
import json
import posixpath
import tarfile
import urllib.error
import urllib.request
import zipfile

from app import system_proxy

TIMEOUT_S = 60.0
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
_CHUNK = 1024 * 1024
_UA = 'vibe-seller-cli-integrations'


class DownloadError(RuntimeError):
    """No candidate URL produced the artifact (each failure named)."""


class IntegrityError(DownloadError):
    """Bytes arrived but do not match the published checksum."""


def _read_capped(resp, max_bytes: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = resp.read(_CHUNK)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > max_bytes:
            raise DownloadError(f'response larger than {max_bytes} bytes')


def fetch(
    urls: Sequence[str],
    *,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    verify: Callable[[bytes], None] | None = None,
) -> tuple[bytes, str]:
    """Bytes of the first candidate that answers (and verifies).

    Returns ``(data, url)``. A candidate whose bytes fail *verify* is
    skipped like an unreachable one — a mirror serving stale or wrong
    bytes must not win over the next candidate.
    """
    errors: list[str] = []
    for url in urls:
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        try:
            with system_proxy.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = _read_capped(resp, max_bytes)
            if verify is not None:
                verify(data)
        except (
            OSError,
            urllib.error.URLError,
            http.client.HTTPException,
            DownloadError,
        ) as e:
            errors.append(f'{url}: {e}')
            continue
        return data, url
    raise DownloadError('; '.join(errors) or 'no candidate URL')


def fetch_json(urls: Sequence[str]) -> tuple[dict, str]:
    """Parsed JSON object of the first candidate that answers."""

    def _is_json(data: bytes) -> None:
        try:
            if not isinstance(json.loads(data), dict):
                raise DownloadError('not a JSON object')
        except ValueError as e:
            raise DownloadError(f'not JSON: {e}') from e

    data, url = fetch(urls, max_bytes=MAX_JSON_BYTES, verify=_is_json)
    return json.loads(data), url


NPMJS = 'https://registry.npmjs.org'
NPMMIRROR = 'https://registry.npmmirror.com'


def npm_registries() -> list[str]:
    """npm registries to ask, in order: the one the user configured (a
    corporate mirror), npmjs, then npmmirror (reachable from China)."""
    configured = system_proxy.npm_target().rstrip('/')
    return list(dict.fromkeys([configured, NPMJS, NPMMIRROR]))


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith('v') else tag


# ── checksums ──────────────────────────────────────────


def sha256_verifier(expected_hex: str) -> Callable[[bytes], None]:
    """A *verify* for ``fetch`` that pins a SHA-256 hex digest."""
    want = expected_hex.strip().lower()

    def _verify(data: bytes) -> None:
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise IntegrityError(f'sha256 {got} != expected {want}')

    return _verify


def sri_verifier(integrity: str) -> Callable[[bytes], None]:
    """A *verify* for an npm ``dist.integrity`` (``sha512-<base64>``)."""
    algo, _, b64 = integrity.strip().partition('-')
    if algo not in ('sha512', 'sha384', 'sha256') or not b64:
        raise IntegrityError(f'unsupported integrity {integrity!r}')
    want = base64.b64decode(b64)

    def _verify(data: bytes) -> None:
        if hashlib.new(algo, data).digest() != want:
            raise IntegrityError(f'{algo} integrity mismatch')

    return _verify


def parse_checksums(text: str) -> dict[str, str]:
    """``<hex>  <name>`` lines (sha256sum / goreleaser) → name: hex."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].lstrip('*')] = parts[0].lower()
    return out


# ── archives ───────────────────────────────────────────


def _safe_name(name: str) -> str | None:
    """Normalised relative path, or None for anything that escapes."""
    name = name.replace('\\', '/')
    if name.startswith('/') or (len(name) > 1 and name[1] == ':'):
        return None
    norm = posixpath.normpath(name)
    if norm in ('.', '') or norm.startswith('../') or norm == '..':
        return None
    return norm


def iter_archive(data: bytes) -> Iterator[tuple[str, bytes]]:
    """``(relative path, bytes)`` of every regular file in a tar(.gz)
    or zip, decided by magic bytes. Links, devices and any path that
    would escape the extraction root are skipped, never followed."""
    if data[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = _safe_name(info.filename)
                if name is None or info.is_dir():
                    continue
                # A symlink inside a zip carries S_IFLNK in the high bits.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    continue
                yield name, zf.read(info)
        return
    buf = io.BytesIO(data)
    if not tarfile.is_tarfile(buf):
        raise DownloadError('not a tar or zip archive')
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode='r:*') as tf:
        for member in tf:
            name = _safe_name(member.name)
            if name is None or not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is not None:
                yield name, fh.read()


def archive_member(data: bytes, wanted: Callable[[str], bool]) -> bytes:
    """The one file of the archive *wanted* accepts (first match)."""
    for name, body in iter_archive(data):
        if wanted(name):
            return body
    raise DownloadError('expected file not found in the archive')
