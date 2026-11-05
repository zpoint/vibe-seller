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
from pathlib import Path
import posixpath

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.agent_mcp.support import McpContext, ToolError
from app.uploads import ALLOWED_UPLOAD_TYPES

# Rule 4 ceilings, one definition each.
# Text at or under this size is returned inline.
MAX_INLINE_BYTES = 256 * 1024
# A binary at or under this size also rides along as a base64 blob.
MAX_BLOB_BYTES = 5 * 1024 * 1024
# A decoded upload may not exceed this (base64 inflates ~4/3, so the
# ENCODED argument is rejected proportionally earlier).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

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


def _decode_text(data: bytes) -> str | None:
    """The file's text, if it decodes as one of the known text encodings.

    The workspace reader tries UTF-8 then GB18030 (platform exports);
    the same pair decides "text" here so a tool and the UI agree about
    what a file is.
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
            text = _decode_text(data)
            if text is not None:
                payload['encoding'] = 'text'
                payload['content'] = text
                return payload
    # Too large for inline, or binary: the URL is always there; a
    # small binary also rides along as a blob.
    payload['encoding'] = 'url'
    payload['url'] = absolute_url(ctx, download_path)
    if size <= MAX_BLOB_BYTES:
        data = abs_path.read_bytes()
        payload['blob'] = base64.b64encode(data).decode('ascii')
    else:
        payload['note'] = (
            f'File is larger than the {MAX_BLOB_BYTES // (1024 * 1024)} MB '
            'in-tool limit — download it from the URL instead.'
        )
    return payload


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
