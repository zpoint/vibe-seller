"""The workspace-facing tools: ``list_stores`` and ``read_knowledge``.

Split out of ``tools.py`` when that module passed the repo's 800-line
limit; the contract they follow is documented there.
"""

import posixpath

from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    MAX_FILE_CHARS,
    McpContext,
    ToolError,
    call_route,
    store_slug_for,
    trim,
)
from app.routers import stores as stores_route
from app.workspace.manager import workspace_manager

# The only subtrees read_knowledge may touch.
_KNOWLEDGE_ROOTS = ('knowledge/', 'stores/')

_PATH_RULE = (
    'path must be relative, without "..", under knowledge/ or '
    'stores/ (this tool is read-only over the knowledge base)'
)


def _validated_path(raw: object) -> str:
    """Confinement for a read_knowledge path, enforced up front.

    A prefix check alone is not confinement: ``knowledge/../x``
    STARTS with ``knowledge/`` and then climbs out of it, and the
    workspace manager's own guard only confines to the workspace
    ROOT — so without this the tool could read any workspace file,
    not just the knowledge base. The contract is enforced BEFORE any
    resolution:

    * no backslash — the path is POSIX-style. On native Windows a
      ``\\`` is a separator, so ``knowledge/..\\data\\jwt_secret``
      would pass a ``/``-only ``..`` check and then climb out;
    * no absolute path, and
    * no ``..`` segment ANYWHERE — even one that would land back
      inside the allowed roots (``knowledge/a/../b``). From the
      string alone an escape attempt and a harmless hop are
      indistinguishable, and this tool has exactly two legal roots;
      a caller that means ``b`` can say ``b``.

    Then the separators that are safe to collapse (``./``, ``//``,
    trailing ``/``) are normalized, and the result must sit under
    one of the roots.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError('path must be a non-empty string')
    candidate = raw.strip()
    if '\\' in candidate or candidate.startswith('/'):
        raise ToolError(_PATH_RULE)
    if any(part == '..' for part in candidate.split('/')):
        raise ToolError(_PATH_RULE)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith(_KNOWLEDGE_ROOTS):
        raise ToolError(_PATH_RULE)
    return normalized


@tool(
    'list_stores',
    'List the stores configured on this server. Use the store id when '
    'creating a task that must act on a particular store.',
    schema(),
)
async def list_stores(ctx: McpContext, args: dict) -> dict:
    stores = await call_route(
        stores_route.list_stores, db=ctx.db, _user=ctx.user
    )
    return {
        'stores': [
            {
                'id': store['id'],
                'name': store['name'],
                'browser_backend': store['browser_backend'],
                'platforms': store['platforms'],
                'countries': store['countries'],
            }
            for store in stores
        ]
    }


@tool(
    'read_knowledge',
    "Read the server's knowledge base: shared platform notes under "
    'knowledge/ and per-store notes under stores/. Read-only. Call it '
    'with no path to list what is there.',
    schema({
        'path': {
            'type': ['string', 'null'],
            'description': (
                'File to read, relative to the workspace root '
                '(e.g. "knowledge/catalog.md"). Omit to list.'
            ),
        },
        'store_id': {
            'type': ['string', 'null'],
            'description': (
                "Restrict the listing to one store's notes (stores/<slug>/)."
            ),
        },
        'section': {
            'type': ['string', 'null'],
            'enum': ['knowledge', 'stores', None],
            'description': 'Restrict the listing to one section.',
        },
        'limit': {
            'type': 'integer',
            'minimum': 1,
            'maximum': 200,
            'description': 'Maximum number of files to list.',
        },
    }),
)
async def read_knowledge(ctx: McpContext, args: dict) -> dict:
    path = args.get('path')
    section = args.get('section')

    if path:
        path = _validated_path(path)
        try:
            content = await workspace_manager.read_file(path)
        except FileNotFoundError as exc:
            raise ToolError(f'File not found: {path}') from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        text, truncated = trim(content, MAX_FILE_CHARS)
        return {
            'path': path,
            'content': text,
            'truncated': truncated,
        }

    limit = args.get('limit') or 50
    limit = max(1, min(int(limit), 200))
    tree = await workspace_manager.list_tree()
    files = [
        item
        for item in tree
        if not item.get('is_dir')
        and str(item.get('path', '')).startswith(_KNOWLEDGE_ROOTS)
    ]
    if section == 'knowledge':
        files = [
            item
            for item in files
            if str(item.get('path', '')).startswith('knowledge/')
        ]
    elif section == 'stores':
        files = [
            item
            for item in files
            if str(item.get('path', '')).startswith('stores/')
        ]
    store_id = args.get('store_id')
    if store_id:
        prefix = f'stores/{await store_slug_for(ctx, store_id)}/'
        files = [
            item
            for item in files
            if str(item.get('path', '')).startswith(prefix)
        ]
    listed = files[:limit]
    return {
        'files': [
            {
                'path': item.get('path'),
                'size': item.get('size'),
            }
            for item in listed
        ],
        'total': len(files),
        'truncated': len(files) > limit,
    }
