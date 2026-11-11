"""MCP tool schemas for advertising work.

Split out of ``app/mcp_tool_schemas.py`` to keep that module under
the repo's 800-line cap. ``TOOLS`` splices ``AD_TOOLS`` in, so the
agent-facing tool list is unchanged.

**Why the audit/investigate discriminator is written out here.** It used
to live only in the ad skills' kind table. But a skill loads
conditionally — a store that isn't Amazon or noon, a console on some
other host, a task that never trips the skill's triggers — while THIS
description is in context every time the tool is, which is every time
the decision is made. A contract stated only where the agent might not
be reading it is a contract that gets guessed at: observed in CI, "review
the keyword bids on <campaign>" was declared ``investigate`` twice in one
run, which is the kind that owes no coverage — so its bid
recommendations faced no audit obligation at all. The
skills still carry the longer version with worked examples; the line
itself belongs at the call site.

**Why the ``change``/``audit`` line is written out here for the same
reason.** A change the user named themselves used to be declared
``audit``, the kind that owes a marketplace enumerated and drilled. On
CI (PR #232) that one-row instruction burned 108k output tokens against
the 27k the same task used when it finished, and then hit the 1200 s
turn limit: the agent was computing an audit, and arguing with itself
over whether ``acos_no_lower`` would refuse the number the user had
written. Who decided the value is exactly the fact only the agent can
state and exactly the fact that changes what the phase owes, so the
declaration says it and the description has to teach it.
"""

# Per-tool ``_meta`` key read by the CLI: the tool's schema stays in
# the prompt, never deferred behind tool search. The tools that must
# be reached at a FIXED point of every task carry it — the ending
# tools (mcp_tool_schemas) and, here, the ad flow's required-first
# declaration and its sanctioned data tool. Observed in CI: a model
# ToolSearch-loaded ``vibe_seller_declare_ad_task`` five times and
# still never called it — the deferred dance broke the declaration
# flow for the e2e profile's model.
_ALWAYS_LOADED = {'_meta': {'anthropic/alwaysLoad': True}}

AD_TOOLS = [
    {
        'name': 'vibe_seller_declare_ad_task',
        **_ALWAYS_LOADED,
        'description': (
            'Advertising work only. BEFORE opening a browser or reading '
            'any ad data, declare what this phase of the task is for. '
            'The declaration decides how much coverage the result check '
            'asks of you, and whose judgement the store rules are applied '
            'to — so a '
            'narrow request stays narrow instead of being expanded into '
            'a whole-store audit, and a change the user named stays one '
            'row. Declaring is required: ad data with no '
            'declaration is refused at submit.\n\n'
            'Declare ONCE per user turn, before browsing. Within that '
            'turn you may call it again for three reasons only: to '
            'NARROW (same kind, same combos, plus a `campaigns` list you '
            'could only learn by enumerating the account); to correct '
            'kind="investigate" to kind="audit" when the work turned out '
            'to produce decisions (same combos or fewer, same campaigns '
            'or fewer); or to declare kind="execute" again (same scope or '
            'narrower) to re-freeze the approval from CHANGES.tsv, which '
            'works only until the first change is logged. You cannot add '
            'a marketplace, widen the campaign list, or move to a kind '
            'that owes LESS than the one you declared. '
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
                        'change',
                        'create',
                        'execute',
                        'investigate',
                    ],
                    'description': (
                        'audit = judge existing ads and recommend changes '
                        '(the only kind that owes marketplace '
                        'coverage). '
                        'change = the USER named the change — the row '
                        'and the value, in their message ("lower '
                        'widget-006 to 1.00"). Write those rows in '
                        'CHANGES.tsv and end your turn showing them. You '
                        'owe NOTHING else for it: no CAMPAIGNS.tsv, no '
                        'per-campaign drill, no search-term '
                        'reconciliation, and no store-rule argument with '
                        'their number — their instruction beats a '
                        'default. It is still confirmed: their reply is '
                        'what opens `execute`. '
                        'create = build new campaigns. execute '
                        '= apply changes the user already approved — '
                        'accepted only after an audit or change phase '
                        'whose list the user has answered. '
                        'investigate = answer a question, change nothing. '
                        '\n\nNUMBERS OR CHANGES is the line between '
                        'investigate and audit, and it decides more than '
                        'any other field here. If what they want back is '
                        'a FIGURE — what did we spend, what is our ROAS, '
                        'how did last month go — that is `investigate`, '
                        'however many marketplaces it spans: you are '
                        'reading, not proposing. If they want to know '
                        'what to CHANGE, asked you to review/check '
                        'something, or asked "is this right?", that is '
                        '`audit`, and it is the kind whose data the '
                        'result check holds to its declared '
                        'marketplaces. '
                        '"Review the bids on <campaign>" is an audit. '
                        'When you cannot tell, ask yourself whether your '
                        'answer will contain a recommendation; if it '
                        'will, declare `audit`. '
                        '\n\nWHOSE JUDGEMENT is the line between change '
                        'and audit. They said the row AND the number → '
                        '`change`. They said which rows to look at and '
                        'left the value to you ("lower the bids that '
                        'waste money", "is 1.20 the right bid?") → '
                        '`audit` scoped to those campaigns, because '
                        'judging needs the data. "I know it converts '
                        'well, do it anyway" is still `change` — the '
                        'insistence is the user deciding, not you. There '
                        'is no "edit" kind.'
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
                                'first and list them here. Ids only — '
                                'never exclusion text ("not X" is '
                                'refused); what is out of scope is '
                                'implied by omission.'
                            ),
                        },
                        'products': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': (
                                'The SKUs/products the user named, for '
                                'the human reading the report. Each '
                                'entry NAMES one thing IN scope — '
                                'exclusions are implied by omission, '
                                'never written ("not X", "except X", '
                                '"X out of scope" is refused). Does '
                                'not narrow anything by itself.'
                            ),
                        },
                    },
                },
            },
            'required': ['kind'],
        },
    },
]
