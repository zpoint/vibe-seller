"""One definition of what may be uploaded, and how big.

Split out because this contract lived in three places that had already
drifted: create-task attachments capped at 10MB
(``routers/attachments.py``), chat staging at 15MB
(``routers/tasks_files.py``), and the browser hardcoding a third copy.
Same allow-list, different ceilings — so the same image was refused in
the New Task dialog and accepted in the chat box of the task it just
created. Import from here; do not re-declare.

The cap is served to the frontend via ``GET /api/settings``
(``max_upload_size``) so the dialog's hint text and its rejection
message are derived from this number rather than repeating it.

**Size is enforced while streaming, never after.** The previous
``content = await file.read()`` then ``len(content) > CAP`` order made
the cap useless as protection: the whole body was materialised in
memory *before* anything decided it was too big, so a client could
spend 1GB of RAM to earn a 400. Peak memory here is one chunk.
"""

from pathlib import Path

from fastapi import HTTPException, UploadFile

ALLOWED_UPLOAD_TYPES = frozenset({
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'application/pdf',
})
ALLOWED_UPLOAD_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf')

# Product detail images ("详情图") routinely run 10-30MB, which the old
# 10MB ceiling refused outright. These files are pass-through material:
# the agent receives an absolute path (see task_runner_context), not the
# bytes, so a large upload does not enter the model context.
MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# Read granularity, and therefore the memory ceiling per upload.
_CHUNK_SIZE = 1024 * 1024

# Multipart framing (boundaries, part headers) rides along with the
# payload, so a body at exactly the cap is legitimately a little larger.
_MULTIPART_SLACK = 1024 * 1024


def human_size(num_bytes: int) -> str:
    """`104857600` -> `'100MB'`. Used in API error detail."""
    mb = num_bytes / (1024 * 1024)
    return f'{mb:.0f}MB' if mb >= 1 else f'{num_bytes}B'


def reject_disallowed_type(file: UploadFile) -> None:
    """Refuse a content type outside the allow-list."""
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f'File type not allowed: {file.content_type}',
        )


def declared_body_too_large(content_length: str | None) -> bool:
    """True when a request announces a body past the cap.

    Checked in middleware, before the multipart parser runs: by the time
    a route's dependencies are solved Starlette has already spooled the
    whole body to a temp file, so a guard at that point saves neither
    memory nor disk. A missing or unparseable header (chunked transfer)
    returns False — ``save_upload`` is the backstop.
    """
    if content_length is None:
        return False
    try:
        declared = int(content_length)
    except ValueError:
        return False
    return declared > MAX_UPLOAD_SIZE + _MULTIPART_SLACK


async def save_upload(file: UploadFile, dest: Path) -> int:
    """Stream `file` to `dest`, aborting past the cap. Returns bytes written.

    Raises 413 the moment the running total crosses the limit, and
    removes the partial file — the caller never sees a truncated
    attachment on disk.
    """
    total = 0
    try:
        with dest.open('wb') as out:
            while chunk := await file.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            'File too large '
                            f'(max {human_size(MAX_UPLOAD_SIZE)})'
                        ),
                    )
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail='Empty file')
    return total
