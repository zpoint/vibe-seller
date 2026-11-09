"""File plumbing shared by the operator toolset (the A rows).

Three contracts live here so every file-shaped tool answers with the
SAME shape and the SAME limits (ops-expansion rule 4):

* **Inline vs download.** A decodable text file up to
  :data:`MAX_INLINE_BYTES` is returned inline; anything else answers
  with metadata plus an absolute download URL on this server (a
  ``vst_`` bearer works there), and a binary up to
  :data:`MAX_BLOB_BYTES` additionally rides along as an MCP
  ``resource`` content item (base64 ``blob`` + mimeType).
* **Uploads.** Tools accept ``attachments`` as
  ``{filename, mime_type, content_base64}``, capped at
  :data:`MAX_UPLOAD_BYTES`, decoded here and handed to the SAME upload
  routes the UI posts to — as a real :class:`fastapi.UploadFile`,
  built the way FastAPI's multipart parser would build it.
* **Path confinement.** Every path argument is validated up front
  (``_validated_path`` in ``tools_workspace.py``, generalised here):
  no backslash, no absolute path, no ``..`` anywhere — the route's own
  guard stays as the second line.

This module registers no tools; it exists so the rules above have one
home shared by ``tools_task_files.py`` and ``tools_workspace_files.py``.
"""

import base64
import binascii
import io
import logging
from pathlib import Path
import posixpath

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.agent_mcp.support import McpContext, ToolError
from app.uploads import ALLOWED_UPLOAD_TYPES

logger = logging.getLogger(__name__)

#: Key a tool result uses to hand the dispatcher pictures to SHOW; they
#: leave as MCP ``image`` content blocks, not as JSON (router.py).
IMAGES_KEY = '_images'
#: Image types MCP clients render.
IMAGE_MIME_TYPES = frozenset({
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
})
# An image block is base64 in the JSON-RPC reply. Clients cap one image
# around 5 MB ENCODED (Claude's API limit), so the raw ceiling is ~3/4
# of that; anything larger is downscaled for viewing — the download URL
# still serves the original.
MAX_IMAGE_BLOCK_BYTES = 3_500_000
# Longest edge of a downscaled preview: what vision models see anyway.
PREVIEW_MAX_EDGE = 1568
# Past this an image is not decoded for a preview at all (decoding a
# huge file costs memory and seconds); the URL still serves it.
MAX_PREVIEW_SOURCE_BYTES = 50 * 1024 * 1024
# All image blocks in ONE tool reply, raw bytes. A client forwards the
# reply to its model, whose request has a size ceiling; past this budget
# further images are shrunk to previews or listed without being shown.
MAX_REPLY_IMAGE_BYTES = 8 * 1024 * 1024

# Rule 4 ceilings, one definition each.
# Text at or under this size is returned inline.
MAX_INLINE_BYTES = 256 * 1024
# A binary at or under this size also rides along as a base64 blob.
MAX_BLOB_BYTES = 5 * 1024 * 1024
# A decoded upload may not exceed this (base64 inflates ~4/3, so the
# ENCODED argument is rejected proportionally earlier).
#
# Deliberately NOT the web UI's 100 MB (``app.uploads``): the UI's
# multipart upload STREAMS to disk, while an MCP attachment is
# base64 inside a JSON document the endpoint parses in memory — a
# 100 MB file would mean a ~137 MB JSON string and a multi-hundred-MB
# parse. 10 MB keeps one attachment at roughly 14 MB of JSON; the
# 64 MB endpoint body cap (``router.MAX_BODY_BYTES``) bounds a whole
# call. An agent with a detail image past this ceiling uploads it
# through the web UI (or any HTTP file path) and references it.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# How many attachments one tool call accepts, and the total DECODED
# bytes a call's attachments may sum to (the per-file cap alone would
# let a call stage count × cap). The total is set BELOW what the
# endpoint's 64 MB body cap allows after base64 inflation (~53 MB
# encoded), so the specific "your attachments are too big" answer
# wins over a generic body-size refusal.
MAX_ATTACHMENTS_PER_CALL = 10
MAX_ATTACHMENTS_TOTAL_BYTES = 40 * 1024 * 1024

# Text the agent is handed for one inline file. Even an inlinable file
# can be large for a context window; the existing read budget
# (``support.MAX_FILE_CHARS``) applies on top of the byte ceiling.
_TEXT_DECODINGS = ('utf-8', 'gb18030')

# MIME types that mean "this is text" (with ``text/*``). Anything
# else is treated as binary UNLESS it is an unknown type that decodes
# as strict UTF-8 — see :func:`_is_text_candidate` for why GB18030
# cannot be the detector.
_TEXTY_TYPES = frozenset({
    'application/json',
    'application/xml',
    'application/javascript',
    'application/yaml',
    'application/x-yaml',
    'image/svg+xml',
})


def _is_text_candidate(mime_type: str, data: bytes) -> bool:
    """Whether this file may be returned as inline text.

    The route's own MIME guess (from the filename) is the primary
    signal. For an unknown type, strict UTF-8 decodability decides —
    GB18030 CANNOT be the detector: it accepts almost every byte
    sequence, so a PNG "decodes" and would be handed to the agent as
    mojibake text. (A GB18030-only text file still reaches the agent
    through its download URL, and the workspace reader decodes it
    properly on the read path that knows to try.)
    """
    if mime_type.startswith('text/') or mime_type in _TEXTY_TYPES:
        return True
    if mime_type == 'application/octet-stream':
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            return False
        return True
    return False


def absolute_url(ctx: McpContext, path: str) -> str:
    """An absolute URL for *path* on THIS server, as the caller sees it.

    Built from the request's own base URL, so a server behind a proxy
    answers with the host the agent already reached it on.

    TRUST MODEL (documented, deliberate): Starlette's ``base_url``
    follows the client-controlled ``Host`` header, so a caller CAN
    make the tool print ``http://<their-host>/api/...``. The URL is
    inert text — fetching it sends nothing anywhere — but an agent
    that follows it would hand its bearer to that host. Behind a
    reverse proxy the operator pins the header (proxy_set_header
    Host), which is the real fix; on a LAN deployment the Host header
    is not attacker-influenceable for a directly-connected agent. If
    this needs to be server-authoritative, a configured external base
    URL replaces ``request.base_url`` — nothing else in this function
    changes.
    """
    base = str(ctx.request.base_url).rstrip('/')
    return f'{base}{path}'


def validated_task_path(raw: object) -> str:
    """Confinement for a path INSIDE a task workspace.

    The task-file route's own guard (``_safe_task_file``: resolve, then
    require the result to sit under the task dir) stays as the second
    line; this up-front check keeps the obvious escapes out of the
    route and matches the read_knowledge rules: POSIX-style relative
    paths only, no ``..`` anywhere.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError('path must be a non-empty string')
    candidate = raw.strip()
    if '\\' in candidate or candidate.startswith('/'):
        raise ToolError(
            'path must be relative and POSIX-style (no backslash, no leading /)'
        )
    if any(part == '..' for part in candidate.split('/')):
        raise ToolError('path may not contain ".."')
    return posixpath.normpath(candidate)


def decode_text(data: bytes) -> str | None:
    """The file's text, if it decodes as one of the known text encodings.

    The workspace reader tries UTF-8 then GB18030 (platform exports);
    the same pair decides "text" here so a tool and the UI agree about
    what a file is. Strict both times — an ``errors='ignore'`` decode
    would accept almost any bytes and hand a binary back as mojibake
    text. (A caller decoding a BOUNDED PREFIX should slice off a few
    tail bytes first: a cut mid–multi-byte-character must not fail an
    otherwise decodable file.)
    """
    for encoding in _TEXT_DECODINGS:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def file_result(
    ctx: McpContext,
    *,
    path: str,
    abs_path,
    mime_type: str,
    download_path: str,
) -> dict:
    """One file, shaped by rule 4: inline text, or URL (+ blob).

    ``abs_path`` is the validated on-disk location the ROUTE resolved —
    the tool never goes looking for the file itself.
    """
    # A route's FileResponse carries its path as a str.
    abs_path = Path(abs_path)
    size = abs_path.stat().st_size
    payload: dict = {
        'path': path,
        'size': size,
        'mime_type': mime_type,
    }
    if size <= MAX_INLINE_BYTES:
        data = abs_path.read_bytes()
        if _is_text_candidate(mime_type, data):
            text = decode_text(data)
            if text is not None:
                payload['encoding'] = 'text'
                payload['content'] = text
                return payload
    # Too large for inline, or binary: the URL is always there; a
    # small binary also rides along as a blob.
    payload['encoding'] = 'url'
    payload['url'] = absolute_url(ctx, download_path)
    # An image is SHOWN (an MCP image block), so the operator — or the
    # model driving it — can look at the result, not just fetch it. When
    # the block carries the ORIGINAL bytes, repeating them as a base64
    # blob would double the reply (and feed the client model megabytes
    # of base64 tokens), so it carries no blob. A downscaled preview is
    # not the file: then the original still rides along as the blob.
    shown = image_block(abs_path)
    if shown is not None:
        payload[IMAGES_KEY] = [shown]
        payload['shown_as_image'] = True
        if not shown.get('preview'):
            return payload
        payload['shown_as_preview'] = True
    if size <= MAX_BLOB_BYTES:
        data = abs_path.read_bytes()
        payload['blob'] = base64.b64encode(data).decode('ascii')
    else:
        payload['note'] = (
            f'File is larger than the {MAX_BLOB_BYTES // (1024 * 1024)} MB '
            'in-tool limit — download it from the URL instead.'
        )
    return payload


def sniff_image_type(data: bytes) -> str | None:
    """The real image type from magic bytes (kie.ai returns WEBP or
    JPEG under a ``.png`` name — the extension is not evidence)."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def image_block(abs_path, max_bytes: int | None = None) -> dict | None:
    """An MCP image for ``abs_path``, or None when it is not a viewable
    image (or too big to show and no preview fits ``max_bytes``).

    Only the first bytes are read to decide whether it is an image at
    all — a 1 GB archive is never loaded to learn it is not one. An image
    within ``max_bytes`` goes as-is; a larger one (up to
    :data:`MAX_PREVIEW_SOURCE_BYTES`) is downscaled to a JPEG preview of
    at most :data:`PREVIEW_MAX_EDGE` px — enough to judge the picture,
    while the download URL keeps the full-resolution file. Blocking
    (file I/O, Pillow): call it off the event loop.
    """
    if max_bytes is None:
        max_bytes = MAX_IMAGE_BLOCK_BYTES
    path = Path(abs_path)
    try:
        size = path.stat().st_size
        with path.open('rb') as fh:
            head = fh.read(16)
    except OSError:
        return None
    mime = sniff_image_type(head)
    if mime is None:
        return None
    if size <= max_bytes:
        return {
            'data': base64.b64encode(path.read_bytes()).decode('ascii'),
            'mime_type': mime,
            'preview': False,
        }
    if size > MAX_PREVIEW_SOURCE_BYTES:
        return None
    try:
        from PIL import Image  # noqa: PLC0415 — optional dependency

        img = Image.open(path)
        img.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=85)
    except Exception:  # noqa: BLE001 — no Pillow / undecodable: no preview
        logger.info('no preview for %s', abs_path, exc_info=True)
        return None
    if buf.tell() > max_bytes:
        return None
    return {
        'data': base64.b64encode(buf.getvalue()).decode('ascii'),
        'mime_type': 'image/jpeg',
        'preview': True,
    }


def decode_upload(raw: object, *, what: str = 'attachment') -> tuple:
    """Validate one ``{filename, mime_type, content_base64}`` entry.

    Returns ``(filename, mime_type, bytes)``. The 10 MB cap is checked
    against the DECODED size, with the encoded length as an early-out —
    a payload past the cap is refused before it is materialised. The
    type is checked against the SAME allow-list the upload route
    enforces (``app.uploads``) so a refused attachment is refused
    BEFORE anything is created around it — a task must not linger
    PENDING because its second attachment was an .exe the route was
    always going to reject.
    """
    if not isinstance(raw, dict):
        raise ToolError(
            f'each {what} must be an object with filename, '
            'mime_type and content_base64'
        )
    filename = raw.get('filename')
    mime_type = raw.get('mime_type')
    encoded = raw.get('content_base64')
    if not isinstance(filename, str) or not filename.strip():
        raise ToolError(f'{what} filename is required')
    if not isinstance(mime_type, str) or not mime_type.strip():
        raise ToolError(f'{what} mime_type is required')
    if mime_type.strip() not in ALLOWED_UPLOAD_TYPES:
        raise ToolError(f'File type not allowed: {mime_type.strip()}')
    if not isinstance(encoded, str) or not encoded.strip():
        raise ToolError(f'{what} content_base64 is required')
    if len(encoded) > (MAX_UPLOAD_BYTES // 3 + 4) * 4:
        raise ToolError(f'{what} {filename!r} exceeds the 10 MB upload limit')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(f'{what} {filename!r} is not valid base64') from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise ToolError(f'{what} {filename!r} exceeds the 10 MB upload limit')
    return filename.strip(), mime_type.strip(), data


def decode_uploads(raw: object) -> list[tuple]:
    """Validate a whole ``attachments`` argument (list of uploads).

    The per-entry rules come from :func:`decode_upload` (the per-file
    ceiling, the type allow-list, the base64 shape); the CALL-level
    caps (count, total decoded bytes) live in
    :func:`attachment_problem` and are enforced at the dispatcher, so
    a tool that forgets this helper is still bounded.
    """
    if not isinstance(raw, list):
        raise ToolError('attachments must be an array')
    problem = attachment_problem(raw)
    if problem:
        raise ToolError(problem)
    return [decode_upload(item) for item in raw]


def _decoded_size_estimate(encoded: str) -> int:
    """The decoded size of a base64 string, from its length alone.

    An UPPER bound (padding included), computed without decoding —
    the call-level total cap must be checkable before any of the
    payload is materialised.
    """
    return (len(encoded) // 4) * 3


def attachment_problem(raw: object) -> str | None:
    """Why an ``attachments`` argument is over the call-level caps.

    The policy, in ONE place, so every tool that accepts attachments
    (create_task, send_message, and any future one) is bounded the
    same way without each having to remember:

    * at most :data:`MAX_ATTACHMENTS_PER_CALL` entries — a hundred
      files is not a task instruction, it is a denial of service on
      the server's own memory;
    * at most :data:`MAX_ATTACHMENTS_TOTAL_BYTES` decoded in total —
      the per-file ceiling alone would let one call stage
      count × ceiling.

    The total is computed from the base64 LENGTHS (an upper bound),
    so a hostile payload is refused without being decoded.
    """
    if not isinstance(raw, list):
        return None  # the schema check reports the type problem
    if len(raw) > MAX_ATTACHMENTS_PER_CALL:
        return (
            f'attachments accepts at most {MAX_ATTACHMENTS_PER_CALL} '
            f'items (got {len(raw)})'
        )
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        encoded = item.get('content_base64')
        if isinstance(encoded, str):
            total += _decoded_size_estimate(encoded)
    if total > MAX_ATTACHMENTS_TOTAL_BYTES:
        return (
            'attachments total about '
            f'{total // (1024 * 1024)} MB — the per-call ceiling is '
            f'{MAX_ATTACHMENTS_TOTAL_BYTES // (1024 * 1024)} MB'
        )
    return None


def as_upload_file(filename: str, mime_type: str, data: bytes) -> UploadFile:
    """An :class:`UploadFile` built the way FastAPI's parser would.

    The upload routes read ``file.filename``, ``file.content_type`` and
    stream ``file.read()``; constructing the object with real headers
    (instead of subclassing) means the routes cannot tell this call
    from the multipart POST the web UI makes.
    """
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({'content-type': mime_type}),
    )
