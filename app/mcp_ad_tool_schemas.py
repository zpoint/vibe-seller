"""MCP tool schemas for advertising work.

Split out of ``app/mcp_tool_schemas.py`` to keep that module under
the repo's 800-line cap. ``TOOLS`` splices ``AD_TOOLS`` in, so the
agent-facing tool list is unchanged.
"""

AD_TOOLS = [
    {
        'name': 'vibe_seller_declare_ad_task',
        'description': (
            'Advertising work only. BEFORE opening a browser or reading '
            'any ad data, declare what this phase of the task is for. '
            'The declaration decides how much the completeness gate asks '
            'of you and whether the user gets a review console — so a '
            'narrow request stays narrow instead of being expanded into '
            'a whole-store audit. Declaring is required: an ad report '
            'with no declaration is refused.\n\n'
            'Declare ONCE per user turn, before browsing. Within that '
            'turn you may call it again ONLY to NARROW: same kind, same '
            'combos, plus a `campaigns` list you could only learn by '
            'enumerating the account. You cannot add a marketplace, '
            'change the kind, or widen the campaign list. '
            'If the user later sends another message that changes what '
            'you are doing (e.g. "now audit the ads for the listing you '
            'just created"), declare again for that new phase — the '
            'scope may name campaigns you created earlier.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'kind': {
                    'type': 'string',
                    'enum': [
                        'audit',
                        'create',
                        'execute',
                        'investigate',
                    ],
                    'description': (
                        'audit = judge existing ads and recommend changes '
                        '(this is the only kind that opens the review '
                        'console). create = build new campaigns. execute '
                        '= apply changes the user already approved. '
                        'investigate = answer a question, change nothing. '
                        'There is no "edit": a request to change a few '
                        'bids is an AUDIT whose scope names those '
                        'campaigns.'
                    ),
                },
                'scope': {
                    'type': 'object',
                    'description': (
                        'What the user actually asked about. OMIT '
                        '`combos` ONLY when they asked about the whole '
                        'store — an absent combo list means every '
                        'marketplace, and you will be held to all of '
                        'them.'
                    ),
                    'properties': {
                        'combos': {
                            'type': 'array',
                            'description': (
                                'Marketplaces in scope, e.g. '
                                '[{"platform":"amazon","country":"AE"}].'
                            ),
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'platform': {'type': 'string'},
                                    'country': {'type': 'string'},
                                },
                                'required': ['platform', 'country'],
                            },
                        },
                        'campaigns': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': (
                                'Campaign ids in scope. Omit to mean '
                                'every campaign in the combos above. '
                                'If the user named a product rather '
                                'than a campaign, resolve it to ids '
                                'first and list them here.'
                            ),
                        },
                        'products': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': (
                                'The SKUs/products the user named, for '
                                'the human reading the review page. '
                                'Does not narrow anything by itself.'
                            ),
                        },
                    },
                },
            },
            'required': ['kind'],
        },
    },
]
