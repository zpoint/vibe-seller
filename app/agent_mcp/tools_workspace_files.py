"""The workspace-file tools: ``workspace_file``.

One tool over the workspace the web UI's Workspace view edits:
the tree, file read/write/delete, per-file git history / version /
reset, the structured view, store-profile scaffolding, and the
knowledge sync. Every action calls the REST route the UI calls
(``app/routers/workspace.py``) — the L1 read-only guard, the
catalog-stub validation and the git auto-commit are the route's own
behaviour, so an MCP write lands in the same audited history a UI
write does.

``read_knowledge`` (tools_workspace.py) stays as the read-only
knowledge tool; this one is the write-capable superset over the roots
the UI may edit.
"""

import posixpath
from urllib.parse import quote

from fastapi import HTTPException

from app.agent_mcp.files import file_result
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    MAX_FILE_CHARS,
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.routers import workspace as workspace_route
from app.schemas.workspace import (
    FileResetRequest,
    FileWriteRequest,
    StoreProfileCreateRequest,
)

# The roots the UI's workspace editor may touch — read_knowledge's
# two read-only roots, plus the skills tree (the skills tools and the
# UI's skill editor live under .claude/skills/). Everything else in
# the workspace (task dirs, .venv, settings) is not UI-editable and
# not tool-editable either.
_EDITABLE_ROOTS = ('knowledge/', 'stores/', '.claude/skills/')

_PATH_RULE = (
    'path must be relative, without "..", under knowledge/, stores/ '
    'or .claude/skills/ (the workspace roots the web UI edits)'
)

_ACTIONS = (
    'tree',
    'read',
    'write',
    'delete',
    'history',
    'version',
    'reset',
    'structured',
    'store_profile',
    'knowledge_sync',
    'knowledge_status',
    'knowledge_meta',
)


def _validated_workspace_path(raw: object) -> str:
    """Confinement for a workspace path, enforced up front.

    The same contract as read_knowledge's ``_validated_path``
    (tools_workspace.py) with the roots widened to what the UI may
    EDIT: no backslash, no absolute path, no ``..`` anywhere — then
    normalized, and the result must sit under an editable root. The
    route's own ``_safe_path`` / L1 guards stay as the second line.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError('path must be a non-empty string')
    candidate = raw.strip()
    if '\\' in candidate or candidate.startswith('/'):
        raise ToolError(_PATH_RULE)
    if any(part == '..' for part in candidate.split('/')):
        raise ToolError(_PATH_RULE)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith(_EDITABLE_ROOTS):
        raise ToolError(_PATH_RULE)
    return normalized


def _validated_prefix(raw: object) -> str:
    """Confinement for a tree-scope prefix — a DIRECTORY name.

    A bare root (``knowledge``) is legal as a prefix even though it
    is not a file path under ``knowledge/``; anything deeper goes
    through the same validation as a path.
    """
    if isinstance(raw, str) and raw.strip() in (
        'knowledge',
        'stores',
        '.claude/skills',
    ):
        return raw.strip()
    return _validated_workspace_path(raw)


@tool(
    'workspace_file',
    'Read and edit the workspace knowledge the web UI edits: shared '
    'notes under knowledge/, per-store notes under stores/, and '
    'skills under .claude/skills/. Actions: tree (list), read, '
    'write, delete, history (git log for one file), version (a file '
    'at a commit), reset (restore a file to a commit), structured '
    '(the grouped view), store_profile (scaffold a store folder), '
    'knowledge_sync / knowledge_status / knowledge_meta (the '
    'knowledge git sync). Writes are committed to the workspace git '
    'history, exactly like a UI write.',
    schema(
        {
            'action': {
                'type': 'string',
                'enum': list(_ACTIONS),
                'description': 'What to do.',
            },
            'path': {
                'type': ['string', 'null'],
                'description': (
                    'Workspace-relative path (e.g. '
                    '"stores/acme/notes.md"). Required for read, '
                    'write, delete, history, version, reset.'
                ),
            },
            'content': {
                'type': ['string', 'null'],
                'description': 'write only: the full new file content.',
            },
            'commit': {
                'type': ['string', 'null'],
                'description': (
                    'version/reset only: the commit sha (from history).'
                ),
            },
            'max_count': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 200,
                'description': 'history only: how many commits (max 200).',
            },
            'prefix': {
                'type': ['string', 'null'],
                'description': (
                    'tree only: only paths under this prefix '
                    '(e.g. "stores/acme/").'
                ),
            },
            'limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 500,
                'description': 'tree only: how many entries (max 500).',
            },
            'slug': {
                'type': ['string', 'null'],
                'description': 'store_profile only: the store slug.',
            },
            'name': {
                'type': ['string', 'null'],
                'description': 'store_profile only: the store name.',
            },
            'platform': {
                'type': ['string', 'null'],
                'description': 'store_profile only (e.g. "amazon").',
            },
            'country': {
                'type': ['string', 'null'],
                'description': 'store_profile only (e.g. "SA").',
            },
            'backend': {
                'type': ['string', 'null'],
                'description': (
                    'store_profile only: "chrome" (default) or "ziniao".'
                ),
            },
        },
        required=['action'],
    ),
)
async def workspace_file(ctx: McpContext, args: dict) -> dict:
    action = require(args, 'action')

    if action not in _ACTIONS:
        raise ToolError(
            f'Unknown action {action!r} — accepted: {", ".join(_ACTIONS)}'
        )

    if action == 'tree':
        prefix = None
        if args.get('prefix'):
            prefix = _validated_prefix(args.get('prefix'))
        limit = max(1, min(int(args.get('limit') or 200), 500))
        tree = await call_route(workspace_route.list_tree, _user=ctx.user)
        entries = [
            item
            for item in tree
            if str(item.get('path', '')).startswith(_EDITABLE_ROOTS)
            and (
                prefix is None
                or str(item.get('path', '')).startswith(f'{prefix}/')
            )
        ]
        return {
            'files': [
                {
                    'path': item.get('path'),
                    'is_dir': item.get('is_dir'),
                    'size': item.get('size'),
                }
                for item in entries[:limit]
            ],
            'total': len(entries),
            'truncated': len(entries) > limit,
        }

    if action == 'structured':
        return await call_route(workspace_route.get_structured, _user=ctx.user)

    if action == 'store_profile':
        slug = require(args, 'slug')
        name = require(args, 'name')
        result = await call_route(
            workspace_route.create_store_profile,
            StoreProfileCreateRequest(
                slug=slug,
                name=name,
                platform=args.get('platform') or '',
                country=args.get('country') or '',
                backend=args.get('backend') or 'chrome',
            ),
            _user=ctx.user,
        )
        return result

    if action == 'knowledge_sync':
        return await call_route(workspace_route.sync_knowledge, _user=ctx.user)
    if action == 'knowledge_status':
        return await call_route(
            workspace_route.knowledge_sync_status, _user=ctx.user
        )
    if action == 'knowledge_meta':
        return await call_route(
            workspace_route.knowledge_sync_meta, _user=ctx.user
        )

    # Every remaining action is about ONE path.
    path = _validated_workspace_path(args.get('path'))

    if action == 'read':
        try:
            result = await call_route(
                workspace_route.read_file, path=path, _user=ctx.user
            )
        except HTTPException as exc:
            # The JSON route refuses binary files by design; the UI
            # opens them through /file/raw, and so does this tool —
            # same shape as task_files.read (URL + optional blob).
            if 'Binary file' not in str(exc.detail):
                raise
            raw = await call_route(
                workspace_route.read_file_raw, path=path, _user=ctx.user
            )
            return file_result(
                ctx,
                path=path,
                abs_path=raw.path,
                mime_type=raw.media_type,
                download_path=(f'/api/workspace/file/raw?path={quote(path)}'),
            )
        content, truncated = trim(result['content'], MAX_FILE_CHARS)
        return {'path': path, 'content': content, 'truncated': truncated}

    if action == 'write':
        content = args.get('content')
        if not isinstance(content, str) or not content.strip():
            raise ToolError('content is required for action=write')
        result = await call_route(
            workspace_route.write_file,
            path=path,
            body=FileWriteRequest(content=content),
            _user=ctx.user,
        )
        return result

    if action == 'delete':
        return await call_route(
            workspace_route.delete_file, path=path, _user=ctx.user
        )

    if action == 'history':
        max_count = max(1, min(int(args.get('max_count') or 50), 200))
        return await call_route(
            workspace_route.file_history,
            path=path,
            max_count=max_count,
            _user=ctx.user,
        )

    if action == 'version':
        commit = require(args, 'commit')
        result = await call_route(
            workspace_route.file_version,
            path=path,
            commit=commit,
            _user=ctx.user,
        )
        content, truncated = trim(result['content'], MAX_FILE_CHARS)
        return {
            'path': path,
            'commit': commit,
            'content': content,
            'truncated': truncated,
        }

    if action == 'reset':
        commit = require(args, 'commit')
        return await call_route(
            workspace_route.reset_file,
            body=FileResetRequest(path=path, commit=commit),
            _user=ctx.user,
        )

    raise ToolError(f'Unhandled action {action!r}')  # pragma: no cover
