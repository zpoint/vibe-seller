"""The shared INSTALL side of the skill-import feature.

Everything an archive (uploaded, packed, or fetched) needs after its
bytes are staged: install_staged_blocking / import_staged (the
ONE extract+install composition every door ends in), install_stream
(the whole mint → stage → install sequence doors call instead of
assembling it themselves), and describe_refusal (the single
(status, message) mapping for pipeline refusals).

Split out of app.skill_uploads (presign/staging/extraction) to
keep each module focused; the import direction is one-way:
skill_install -> skill_uploads.
"""

import asyncio
import base64

from app.skill_uploads import (
    UploadError,
    archive_path,
    consume_upload,
    create_upload,
    extract_archive,
    stage_stream,
    staged_record,
)
from app.workspace import skills_tree
from app.workspace.skills_manager import derive_skill_slug

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


async def install_stream(
    manager,
    user_id: str,
    chunks,
    content_type: str,
    *,
    slug: str | None = None,
    origin_url: str = '',
    overwrite: bool = False,
) -> dict:
    """The whole upload-side sequence — ONE place: mint a record →
    stream the archive body into staging (caps enforced while
    streaming, single-use claim) → :func:`import_staged`.

    Every door (``import-local`` route, ``import-url`` route,
    ``manage_skill`` upload/url imports) is a thin adapter: parse its
    own arguments, call this (or :func:`app.skill_fetch.install_url`
    for URL sources), convert errors with :func:`describe_refusal`.
    No door assembles the sequence itself any more — three copies of
    it had already drifted in what they prefixed and defaulted.
    """
    minted = await asyncio.to_thread(create_upload, user_id, '')
    upload_id = minted['upload_id']
    await stage_stream(upload_id, chunks, content_type)
    return await import_staged(
        manager,
        upload_id,
        user_id,
        slug=slug,
        origin_url=origin_url,
        overwrite=overwrite,
    )


def describe_refusal(exc: Exception) -> tuple[int, str]:
    """(status, message) for ANY pipeline refusal — one mapping
    source. ``UploadError`` carries both; skill_fetch's ``FetchError``
    (a ValueError) carries a status too; plain ValueErrors are 400.
    Doors only convert the TYPE (HTTPException / ToolError / tool
    error JSON); none re-derives codes or message shapes."""
    if isinstance(exc, UploadError):
        return exc.status_code, exc.message
    return getattr(exc, 'status_code', 400), str(exc)
