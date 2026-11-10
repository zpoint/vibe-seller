"""MCP tool schemas for editing workspace notes in place.

Split out of ``app/mcp_tool_schemas.py`` (800-line cap); ``TOOLS``
splices ``WORKSPACE_EDIT_TOOLS`` in, so the agent-facing list is one.

**Why a delete tool and not "write the file back".** Removing passages
from a long note by rewriting the whole file makes the model retype
hundreds of lines; observed on a 640-line store note, every retype
slipped somewhere (a line split in two, a phrase dropped), the
deletion-only check refused each attempt, and the run looped until it
was stopped. Naming the lines to drop leaves nothing to retype, and the
server applies the deletion itself.
"""

WORKSPACE_EDIT_TOOLS = [
    {
        'name': 'vibe_seller_write_workspace_file',
        'description': (
            'Write a file to the shared workspace using a '
            'relative path (e.g. "stores/my-store/CATALOG.md" '
            'or "knowledge/CATALOG.md"). This is the ONLY '
            'reliable way to write to stores/ and knowledge/ '
            'directories — the built-in Write tool cannot '
            'write through workspace symlinks. Creates parent '
            'directories automatically.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': (
                        'Relative path from workspace root '
                        '(e.g. "stores/my-store/notes.md")'
                    ),
                },
                'content': {
                    'type': 'string',
                    'description': 'File content to write',
                },
            },
            'required': ['path', 'content'],
        },
    },
    {
        'name': 'vibe_seller_delete_workspace_lines',
        'description': (
            'Delete whole lines from a workspace file (e.g. '
            '"stores/my-store/notes.md") without rewriting it. Give the '
            "file's current number of lines and the 1-based, inclusive "
            'line ranges to remove, as numbered when you read it; the '
            'server deletes exactly those lines and refuses the call if '
            'the file has a different number of lines by then (re-read '
            'it and try again).'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': (
                        'Relative path from workspace root '
                        '(e.g. "stores/my-store/notes.md")'
                    ),
                },
                'line_count': {
                    'type': 'integer',
                    'description': 'Number of lines the file has now',
                },
                'ranges': {
                    'type': 'array',
                    'description': (
                        'Line ranges to delete, each [first, last], '
                        '1-based and inclusive'
                    ),
                    'items': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                        'minItems': 2,
                        'maxItems': 2,
                    },
                },
            },
            'required': ['path', 'line_count', 'ranges'],
        },
    },
]
