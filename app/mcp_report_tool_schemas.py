"""MCP tool schemas for monthly report-download runs.

Split out of ``app/mcp_tool_schemas.py`` the way the ad tools were, to
keep each module under the repo's 800-line cap. ``TOOLS`` splices
``REPORT_TOOLS`` in, so the agent-facing tool list is unchanged.

**Why this tool is always loaded, like the ad declaration.** Without the
declaration the deliverable check does not run at all — a declared run is
the only way a monthly download gets verified. A tool the model has to
remember to search for before it can comply is a tool it will sometimes
not find: in CI a model ``ToolSearch``-loaded
``vibe_seller_declare_ad_task`` five times and still never called it,
which is why that one carries the ``_meta`` marker. This flow has the
same shape, and the cost is one short schema in the prompt rather than a
silent month of unverified numbers.
"""

from app.mcp_ad_tool_schemas import _ALWAYS_LOADED

REPORT_TOOLS = [
    {
        'name': 'vibe_seller_declare_report_run',
        **_ALWAYS_LOADED,
        'description': (
            'Declare a monthly report-download run — the full month-end '
            'set for one store — and name the month it covers. The '
            'declaration is what makes the result checked against the file '
            "list the server derives from this store's declared "
            'capabilities and the calendar. No declaration, no check: '
            'nothing you downloaded is held to anything.\n\n'
            'Declare it only if the whole task is that run, and you are '
            'the one who can tell: nothing on the server distinguishes a '
            "monthly run from a lookup. One report (last month's "
            'transactions for AE), a question about numbers you already '
            'have, a partial fetch, a retry that re-pulls the one file '
            'that failed — those are lookups, and a declared run owes '
            'every file in that month, so declaring one of them gets you '
            'refused for reports nobody asked for.\n\n'
            'Declare once, when the run starts, before the downloads. '
            'Declare the month only, never the files: the expected list '
            "is the server's, and the answer gives it back to you so you "
            'know what this run owes. A month must have ended — the '
            'previous calendar month, or an earlier one for a back-fill '
            'the user asked for.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'month': {
                    'type': 'string',
                    'description': (
                        'The calendar month this run covers, `YYYY-MM` — '
                        'usually the month before the one you are in. It '
                        'decides which files are owed, so name the month '
                        'the user asked about, not the one you happen to '
                        'have downloaded.'
                    ),
                },
            },
            'required': ['month'],
        },
    },
]
