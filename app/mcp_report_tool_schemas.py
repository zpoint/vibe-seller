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
            'Declare a MONTHLY report-download run — the full month-end '
            'set for one store — and name the month it covers. The '
            'declaration is what makes the result checked against the file '
            "list the server derives from this store's declared "
            'capabilities and the calendar. No declaration, no check: '
            'nothing you downloaded is held to anything.\n\n'
            'DECLARE ONLY IF THE WHOLE TASK IS THAT RUN. You make the '
            'call — nothing on the server can tell a monthly run from a '
            'lookup. One report ("pull last month\'s transaction report '
            'for AE"), a question about numbers you already have, a '
            'partial fetch, a retry that re-pulls the one file that '
            'failed: those are lookups, do NOT declare them. A declared '
            "run owes every file in the month's set, so declaring a "
            'lookup gets you refused for reports nobody asked for — and '
            'an undeclared run is not checked at all. Filing CSVs under a '
            '`reports_*` folder is not a declaration either: reading one '
            'from the folder names was the inference this tool replaced.'
            '\n\n'
            'Declare ONCE when the run starts, before the downloads, and '
            'declare the MONTH only, never the files: the expected list is '
            "the server's, not yours, and the answer gives it back to you "
            'so you know what this run owes. A month must have ended — '
            'the previous calendar month, or an earlier one for a '
            'back-fill the user asked for.'
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
