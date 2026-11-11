"""The skills tools: ``manage_skill``.

One tool over the user-space skills the web UI's Workspace → Skills
view manages (``app/routers/workspace.py`` ``/skills*``): list, read,
create, update (upsert), **import** (install a whole skill from
somewhere else — via a presigned archive upload, or inline for tiny
text-only skills), delete, and the built-in sync. Every action calls
the route the UI calls; the manager's own guards (built-in slugs are
read-only, slug shape, reserved names, safe relative file paths, size
caps) are the route's behaviour, not re-implemented here, and the
archive side adds ``app.skill_uploads`` (signing, staging, strict
extraction) on top of the ONE install path.
"""

import asyncio
import base64
import re

from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    MAX_FILE_CHARS,
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.routers import sync as sync_route, workspace as workspace_route
from app.schemas.workspace import SkillCreateRequest, SkillSaveRequest
from app.skill_uploads import (
    UploadError,
    archive_path,
    consume_upload,
    create_upload,
    extract_archive,
    staged_record,
)
from app.sync import state as sync_state
from app.sync.jobs import SKILLS_JOB
from app.workspace import skills_tree
from app.workspace.skills_manager import derive_skill_slug

_ACTIONS = (
    'list',
    'get',
    'create',
    'update',
    'begin_upload',
    'import',
    'delete',
    'sync',
    'sync_meta',
)

_SKILL_MD = 'SKILL.md'


def _skill_dir(slug: str) -> str:
    """The workspace path of a skill's directory, for reads.

    Only the shape is checked here (the save route validates the full
    slug regex on write); a missing skill answers 404 from the read
    route, exactly as it does for the UI.
    """
    if not slug or slug.startswith(('.', '/')) or '/' in slug:
        raise ToolError(f'Invalid skill slug: {slug!r}')
    return f'.claude/skills/{slug}'


def _skill_md_arg(args: dict, action: str) -> str:
    """The full SKILL.md for update / import."""
    skill_md = args.get('skill_md')
    if not isinstance(skill_md, str) or not skill_md.strip():
        raise ToolError(f'skill_md is required for action={action}')
    return skill_md


def _save_request(args: dict, skill_md: str, replace: bool) -> SkillSaveRequest:
    """Build the save payload for update / import.

    Shape checks here (they name the argument); the deep rules —
    safe relative paths, base64, size caps, executables naming real
    files — stay in the manager, where every other writer is held to
    them too. ``replace`` is the difference between the two actions:
    ``import`` installs the tree wholesale (stale files are gone),
    ``update`` merges.
    """
    files = args.get('files') or {}
    files_bin = args.get('files_bin') or {}
    executables = args.get('executables') or []
    if not isinstance(files, dict):
        raise ToolError('files must be an object path -> content')
    if not isinstance(files_bin, dict):
        raise ToolError('files_bin must be an object path -> base64')
    if not isinstance(executables, list):
        raise ToolError('executables must be a list of skill file paths')
    return SkillSaveRequest(
        skill_md=skill_md,
        files=files,
        files_bin=files_bin,
        executables=executables,
        origin_url=args.get('origin_url') or '',
        replace=replace,
        overwrite=bool(args.get('overwrite')),
    )


def _public_base_url(request) -> str:
    """The origin to build ``put_url`` from — proxy headers first.

    Behind a reverse proxy the request's own base URL can be an
    internal host or the wrong scheme. ``X-Forwarded-Proto`` /
    ``X-Forwarded-Host`` (or RFC 7239 ``Forwarded``) are taken as a
    HINT only: the real contract is ``put_path`` — a client whose MCP
    origin differs from the returned ``put_url`` origin simply uses
    ITS OWN MCP origin + ``put_path``.
    """
    proto = request.headers.get('x-forwarded-proto')
    host = request.headers.get('x-forwarded-host')
    forwarded = request.headers.get('forwarded') or ''
    if not proto:
        match = re.search(r'proto=([^;,\s]+)', forwarded)
        proto = match.group(1) if match else None
    if not host:
        match = re.search(r'host=([^;,\s]+)', forwarded)
        host = match.group(1) if match else None
    if proto and host:
        return f'{proto}://{host}/'
    return str(request.base_url)


def _import_slug(args: dict, skill_md: str) -> str:
    """The slug an import installs under (frontmatter name unless given)."""
    slug = args.get('slug') or derive_skill_slug(skill_md)
    if not slug:
        raise ToolError(
            'SKILL.md carries no frontmatter name to derive the '
            'skill slug from — pass slug explicitly'
        )
    return slug


async def _import_upload(ctx: McpContext, args: dict, upload_id: str) -> dict:
    """Import the staged archive: extract and install as ONE hop.

    The archive is read from staging and the tree is swapped into
    ``.claude/skills`` — one logical operation — so both run in ONE
    thread hop inside the skills-tree lock (the shape
    ``app/ads_skill.py`` uses for its bundle install). The install
    rules live in one place (``manager.save_skill_blocking``), shared
    with the inline save path; nothing here blocks the event loop.
    The upload is consumed only after the install succeeded, so a
    refused install can be retried without re-uploading.
    """
    if args.get('files') or args.get('files_bin'):
        raise ToolError(
            'upload_id imports the staged archive — do not also '
            'send files / files_bin'
        )
    manager = workspace_route.workspace_manager
    async with skills_tree.hold():
        try:
            result, existed = await asyncio.to_thread(
                _extract_and_install_blocking,
                manager,
                upload_id,
                str(ctx.user.id),
                args,
            )
        except UploadError as exc:
            raise ToolError(exc.message) from exc
        except ValueError as exc:
            raise ToolError(f'Archive refused: {exc}') from exc
    await manager._auto_commit(
        f'{"Update" if existed else "Create"} skill: {result["slug"]}'
    )
    return result


def _extract_and_install_blocking(
    manager, upload_id: str, user_id: str, args: dict
) -> tuple[dict, bool]:
    """Sync target: staging reads, strict extraction, shared install.

    Everything the archive import touches on disk happens here, in
    the caller's single thread hop — the record read, the strict
    extraction, the install core and the upload consumption.
    """
    record = staged_record(upload_id, user_id)
    extracted = extract_archive(archive_path(upload_id), record['format'])
    slug = _import_slug(args, extracted.skill_md)
    result, existed = manager.save_skill_blocking(
        slug,
        extracted.skill_md,
        files_bin={
            rel: base64.b64encode(data).decode('ascii')
            for rel, data in extracted.files.items()
        },
        executables=sorted(extracted.executables),
        origin_url=args.get('origin_url') or '',
        replace=True,
        overwrite=bool(args.get('overwrite')),
    )
    consume_upload(upload_id)
    # macOS packaging junk is skipped at extraction — say so, never
    # silently hand back a different tree than the one packed.
    result['ignored'] = extracted.ignored
    return result, existed


@tool(
    'manage_skill',
    "Manage the server's agent skills (the instructions its task "
    'agents load): list them, read one (get), create a new user-space '
    'skill, update an existing one (full SKILL.md, optionally extra '
    'files; merges — files it does not carry stay), import a whole '
    'skill from this machine, delete one, or run the built-in skills '
    'sync (sync / sync_meta). TO IMPORT A LOCAL SKILL (a codex or '
    'Claude skill directory): action=begin_upload gives a presigned '
    'put_url and a ready-to-run curl; run it in YOUR shell to upload '
    "the skill's archive (tar.gz/tar/zip of the skill dir — the "
    'bytes go straight to the server, never through you), then '
    "action=import with the upload_id. If put_url's origin differs "
    'from the MCP URL you are configured with (a reverse proxy), PUT '
    'to <your MCP origin> + put_path instead. The inline files/files_bin '
    'arguments remain only for tiny text-only skills — do NOT send '
    'large or binary content through tool arguments. import installs '
    'the skill wholesale, so it REFUSES an existing user skill unless '
    'overwrite=true — confirm that with your user first (it deletes '
    'their current copy). Built-in skills are read-only — list marks '
    'them source=builtin.',
    schema(
        {
            'action': {
                'type': 'string',
                'enum': list(_ACTIONS),
                'description': 'What to do.',
            },
            'slug': {
                'type': ['string', 'null'],
                'description': (
                    'The skill slug (required for get, update, '
                    'delete; optional for import, which derives it '
                    "from SKILL.md's frontmatter name)."
                ),
            },
            'name': {
                'type': ['string', 'null'],
                'description': (
                    'create only: the new skill name (letters, digits, '
                    'hyphens; must not start with _).'
                ),
            },
            'description': {
                'type': ['string', 'null'],
                'description': 'create only: a one-line description.',
            },
            'origin_url': {
                'type': ['string', 'null'],
                'description': (
                    'create / import only: where the skill came from '
                    '(a URL or a local path).'
                ),
            },
            'upload_id': {
                'type': ['string', 'null'],
                'description': (
                    'import only: the id from begin_upload — the '
                    'staged archive is extracted and installed. This '
                    'is how a real (binary-carrying) skill is '
                    'imported; mutually exclusive with files / '
                    'files_bin.'
                ),
            },
            'skill_md': {
                'type': ['string', 'null'],
                'description': (
                    'update / import only (inline): the full new '
                    'SKILL.md content (frontmatter with name and '
                    'description included).'
                ),
            },
            'files': {
                'type': 'object',
                'description': (
                    'update / import only (inline): extra files in '
                    'the skill dir, relative path -> TEXT content '
                    '(not SKILL.md itself). Tiny text edits only.'
                ),
                'additionalProperties': {'type': 'string'},
            },
            'files_bin': {
                'type': 'object',
                'description': (
                    'update only (inline): extra files that are not '
                    'text, relative path -> base64 content. Small '
                    'binaries only — a real skill import goes through '
                    'begin_upload + import(upload_id), because base64 '
                    'tool arguments pass every byte through the model.'
                ),
                'additionalProperties': {'type': 'string'},
            },
            'executables': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'update / inline import only: which of the files '
                    'above are installed with the executable bit. '
                    'An archive import carries the bits itself.'
                ),
            },
            'overwrite': {
                'type': 'boolean',
                'description': (
                    'import only: needed to REPLACE a user skill that '
                    'already exists under this slug. The call is '
                    'refused without it — confirm with your user '
                    'first, then re-call with overwrite=true.'
                ),
            },
        },
        required=['action'],
    ),
)
async def manage_skill(ctx: McpContext, args: dict) -> dict:
    action = require(args, 'action')
    if action not in _ACTIONS:
        raise ToolError(
            f'Unknown action {action!r} — accepted: {", ".join(_ACTIONS)}'
        )

    if action == 'list':
        skills = await call_route(
            workspace_route.list_skills, task_id=None, _user=ctx.user
        )
        return {'skills': skills}

    if action == 'sync':
        # A manual sync is the scheduler's manual run (force: pressing
        # Sync means "stop trusting what we have"), not a private path.
        return await call_route(sync_route.run_sync, SKILLS_JOB, _user=ctx.user)
    if action == 'sync_meta':
        return await sync_state.get_dict(SKILLS_JOB) or {}

    if action == 'create':
        name = require(args, 'name')
        description = args.get('description') or ''
        result = await call_route(
            workspace_route.create_skill,
            SkillCreateRequest(
                name=name,
                description=description,
                origin_url=args.get('origin_url') or '',
            ),
            _user=ctx.user,
        )
        return result

    if action == 'begin_upload':
        # Minting runs its sweep and state writes in one thread hop;
        # the loop only assembles the response.
        return await asyncio.to_thread(
            create_upload, str(ctx.user.id), _public_base_url(ctx.request)
        )

    if action == 'import':
        upload_id = args.get('upload_id')
        if upload_id:
            return await _import_upload(ctx, args, upload_id)
        skill_md = _skill_md_arg(args, 'import')
        slug = _import_slug(args, skill_md)
        return await call_route(
            workspace_route.save_skill,
            slug,
            _save_request(args, skill_md, replace=True),
            _user=ctx.user,
        )

    slug = require(args, 'slug')
    skill_dir = _skill_dir(slug)

    if action == 'get':
        # The UI's skill editor reads the SKILL.md through the
        # workspace file route; the metadata comes from list_skills.
        skills = await call_route(
            workspace_route.list_skills, task_id=None, _user=ctx.user
        )
        meta = next((s for s in skills if s.get('slug') == slug), None)
        if meta is None:
            raise ToolError(f'Skill not found: {slug}')
        read = await call_route(
            workspace_route.read_file,
            path=f'{skill_dir}/{_SKILL_MD}',
            _user=ctx.user,
        )
        content, truncated = trim(read['content'], MAX_FILE_CHARS)
        tree = await call_route(workspace_route.list_tree, _user=ctx.user)
        files = [
            item['path']
            for item in tree
            if str(item.get('path', '')).startswith(f'{skill_dir}/')
            and not item.get('is_dir')
        ]
        return {
            'slug': slug,
            'name': meta.get('name'),
            'description': meta.get('description'),
            'source': meta.get('source'),
            'updatable': meta.get('updatable'),
            'skill_md': content,
            'truncated': truncated,
            'files': files,
        }

    if action == 'update':
        skill_md = _skill_md_arg(args, 'update')
        return await call_route(
            workspace_route.save_skill,
            slug,
            _save_request(args, skill_md, replace=False),
            _user=ctx.user,
        )

    if action == 'delete':
        return await call_route(
            workspace_route.delete_skill, slug, _user=ctx.user
        )

    raise ToolError(f'Unhandled action {action!r}')  # pragma: no cover
