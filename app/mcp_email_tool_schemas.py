"""MCP tool schemas for the task's mailboxes.

Split out of ``app/mcp_tool_schemas.py`` to keep each module under the
repo's 800-line cap. ``TOOLS`` splices ``EMAIL_TOOLS`` in at the same
position, so the agent-facing tool list and its order are unchanged.
"""

EMAIL_TOOLS = [
    {
        'name': 'vibe_seller_email_info',
        'description': (
            "This task's mailboxes (a store task: its store's only) and "
            'what each can do: can_read / can_send, and when it cannot, '
            'why and what the user changes in Settings → Email. Also each '
            "one's SQLite DB path, attachments dir, schema and sample "
            'sqlite3 queries; mail syncs every 5 minutes. With no '
            'mailbox it says so: then this task cannot read or send mail.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'store_id': {
                    'type': 'string',
                    'description': (
                        'Optional. A task with no store may name one (id '
                        'or slug) to see only its mailboxes; a store task '
                        'always gets its own.'
                    ),
                },
            },
            'required': [],
        },
    },
    {
        'name': 'vibe_seller_get_new_emails',
        'description': (
            'Scheduled email tasks: fetch ONLY the emails that arrived '
            'since the last run. The server reads the email_watermark '
            'cursor for your schedule, filters the email DB by it, and '
            'returns {count, first_run, watermark_used, next_watermark, '
            'accounts:[{email, new_emails:[{message_id, subject, '
            'sender, date, epoch, body_text}]}]}. **This is the one '
            'call to make for a "new since last run" sweep — do NOT '
            'query the email DB with raw sqlite3 for it.** An '
            'unfiltered SELECT drags already-processed emails into your '
            'context and leaks them into this run. After reporting the '
            'returned bodies, persist the cursor verbatim: '
            "vibe_seller_set_schedule_state('email_watermark', "
            'next_watermark). On the first run (no cursor) it returns '
            'the last 24h. Scope (store + cursor) is resolved '
            'server-side from your task.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'lookback_hours': {
                    'type': 'integer',
                    'description': (
                        'First-run window when no cursor exists yet '
                        '(default 24). Ignored once a cursor is set.'
                    ),
                },
            },
            'required': [],
        },
    },
    {
        'name': 'vibe_seller_send_email',
        'description': (
            "Send email from one of this task's mailboxes (by address; "
            "a store task: its store's only). A mailbox that cannot send "
            '(sending turned off, no outgoing server) is refused with why '
            '— nothing sent; draft it for the user. Returns {ok: true, '
            'message_id: ...} on success.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'account_email': {
                    'type': 'string',
                    'description': ('Email address of the sending account'),
                },
                'to': {
                    'type': 'string',
                    'description': 'Recipient email address',
                },
                'subject': {
                    'type': 'string',
                    'description': 'Email subject',
                },
                'body': {
                    'type': 'string',
                    'description': 'Email body (plain text)',
                },
                'body_html': {
                    'type': 'string',
                    'description': 'Email body (HTML, optional)',
                },
            },
            'required': ['account_email', 'to', 'subject', 'body'],
        },
    },
    {
        'name': 'vibe_seller_sync_email_now',
        'description': (
            "Trigger immediate IMAP sync for one of this task's "
            'mailboxes. Blocks until sync completes. Returns '
            '{ok, account_email, new_emails, last_polled_at}. '
            'Has a 30s cooldown per account. '
            'Then query the email DB via sqlite3 as usual.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'account_email': {
                    'type': 'string',
                    'description': ('Email address of the account to sync'),
                },
            },
            'required': ['account_email'],
        },
    },
]
