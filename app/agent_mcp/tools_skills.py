"""The skills tools: ``manage_skill``.

One tool over the user-space skills the web UI's Workspace → Skills
view manages (``app/routers/workspace.py`` ``/skills*``): list, read,
create, update (upsert), delete, and the built-in sync. Every action
calls the route the UI calls; the manager's own guards (built-in
slugs are read-only, slug shape, reserved names) are the route's
behaviour, not re-implemented here.
"""

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
from app.schemas.workspace import SkillCreateRequest, SkillSaveRequest

_ACTIONS = ('list', 'get', 'create', 'update', 'delete', 'sync', 'sync_meta')

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


@tool(
    'manage_skill',
    "Manage the server's agent skills (the instructions its task "
    'agents load): list them, read one (get), create a new user-space '
    'skill, update an existing one (full SKILL.md, optionally extra '
    'files), delete one, or run the built-in skills sync (sync / '
    'sync_meta). Built-in skills are read-only — list marks them '
    'source=builtin.',
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
                    'The skill slug (required for get, update, delete).'
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
                'description': 'create only: where the skill came from.',
            },
            'skill_md': {
                'type': ['string', 'null'],
                'description': (
                    'update only: the full new SKILL.md content '
                    '(frontmatter with name and description included).'
                ),
            },
            'files': {
                'type': 'object',
                'description': (
                    'update only: extra files in the skill dir, '
                    'relative path -> content (not SKILL.md itself).'
                ),
                'additionalProperties': {'type': 'string'},
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
        skills = await call_route(workspace_route.list_skills, _user=ctx.user)
        return {'skills': skills}

    if action == 'sync':
        return await call_route(workspace_route.sync_skills, _user=ctx.user)
    if action == 'sync_meta':
        return await call_route(
            workspace_route.skills_sync_meta, _user=ctx.user
        )

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

    slug = require(args, 'slug')
    skill_dir = _skill_dir(slug)

    if action == 'get':
        # The UI's skill editor reads the SKILL.md through the
        # workspace file route; the metadata comes from list_skills.
        skills = await call_route(workspace_route.list_skills, _user=ctx.user)
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
        skill_md = args.get('skill_md')
        if not isinstance(skill_md, str) or not skill_md.strip():
            raise ToolError('skill_md is required for action=update')
        files = args.get('files') or {}
        if not isinstance(files, dict):
            raise ToolError('files must be an object path -> content')
        return await call_route(
            workspace_route.save_skill,
            slug,
            SkillSaveRequest(skill_md=skill_md, files=files),
            _user=ctx.user,
        )

    if action == 'delete':
        return await call_route(
            workspace_route.delete_skill, slug, _user=ctx.user
        )

    raise ToolError(f'Unhandled action {action!r}')  # pragma: no cover
