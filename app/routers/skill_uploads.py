"""Presigned skill-archive upload endpoint.

The raw-``PUT`` half of the skill import flow (see
``app/skill_uploads.py``): the agent's own shell streams a ``tar.gz`` /
``tar`` / ``zip`` of its local skill directory to a one-shot signed
URL, so the file BYTES never pass through the agent's LLM.

No session and no API token: the HMAC signature in the URL IS the
authorization (it binds upload id + user + expiry), which is exactly
why the ``vst_`` token never has to appear in a shell command. Every
body is BOUNDED before it can reach disk: the raw path is capped
while streaming, and multipart REQUIRES a declared ``Content-Length``
(411 without one — Starlette's parser spools file parts unbounded, so
a chunked multipart body must never be trusted to it) and is refused
with 413 when that length passes the ceiling
(``app/skill_uploads`` owns the rules).
"""

from fastapi import APIRouter, HTTPException, Request

from app import skill_uploads
from app.skill_uploads import (
    UPLOAD_CHUNK_BYTES,
    UploadError,
    check_upload_request,
    stage_stream,
)

router = APIRouter(prefix='/api', tags=['skill-uploads'])

# Multipart bodies carry boundary markers and part headers around the
# file itself; the body-level ceiling leaves room for those while the
# FILE stays bounded by the same MAX_ARCHIVE_UPLOAD_BYTES cap.
MULTIPART_OVERHEAD = 1024 * 1024


def _body_ceiling() -> int:
    return skill_uploads.MAX_ARCHIVE_UPLOAD_BYTES + MULTIPART_OVERHEAD


async def _raw_chunks(request: Request):
    async for chunk in request.stream():
        yield chunk


async def _multipart_chunks(file_obj):
    while True:
        chunk = await file_obj.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            return
        yield chunk


def _oversize_multipart(exc: HTTPException) -> bool:
    return 'exceeded maximum size' in str(exc.detail).lower()


@router.api_route('/skill-uploads/{upload_id}', methods=['PUT', 'POST'])
async def put_skill_archive(upload_id: str, request: Request):
    """Accept the archive bytes (raw ``PUT``; ``POST`` multipart).

    Raw ``PUT`` with the archive as the body is the documented
    default; ``multipart/form-data`` with a single ``file`` field is
    accepted too (the standard browser/HTML upload shape) — both land
    in the same staging handler and both are size-bounded.
    """
    try:
        check_upload_request(
            upload_id,
            request.query_params.get('uid'),
            int(request.query_params.get('exp') or 0),
            request.query_params.get('sig'),
        )
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    ceiling = _body_ceiling()
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > ceiling:
        raise HTTPException(
            status_code=413,
            detail=f'Archive exceeds the {ceiling} byte upload limit',
        )

    content_type = request.headers.get('content-type', '')
    if content_type.split(';')[0].strip().lower() == 'multipart/form-data':
        # Multipart bodies MUST declare their length: Starlette's
        # parser spools file-part bytes to disk with NO size check
        # (max_part_size bounds text fields only), so a chunked body
        # would be unbounded before any route code ran. The declared
        # length is refused above when it passes the ceiling; the
        # parser's max_files/max_fields keep the part count at one
        # file.
        if not declared.isdigit():
            raise HTTPException(
                status_code=411,
                detail='Content-Length is required for multipart uploads',
            )
        try:
            form = await request.form(
                max_files=1, max_fields=1, max_part_size=ceiling
            )
        except HTTPException as exc:
            if _oversize_multipart(exc):
                raise HTTPException(
                    status_code=413,
                    detail=f'Archive exceeds the {ceiling} byte upload limit',
                )
            raise
        uploads = [
            value
            for key, value in form.multi_items()
            if key == 'file' and hasattr(value, 'read')
        ]
        if len(uploads) != 1:
            raise HTTPException(
                status_code=415,
                detail='multipart upload must carry exactly one `file` field',
            )
        file_obj = uploads[0]
        chunks = _multipart_chunks(file_obj)
        part_type = getattr(file_obj, 'content_type', '') or (
            'application/octet-stream'
        )
    else:
        chunks = _raw_chunks(request)
        part_type = content_type or 'application/octet-stream'

    try:
        return await stage_stream(upload_id, chunks, part_type)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
