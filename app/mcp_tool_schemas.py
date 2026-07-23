"""MCP tool JSON-Schema definitions for the vibe_seller MCP server.

Split out of ``app/mcp_server.py`` to keep each module under the
800-line limit. The server imports ``TOOLS`` from here and adds
the matching dispatch branches in ``handle_tool_call``.
"""

TOOLS = [
    {
        'name': 'vibe_seller_list_stores',
        'description': 'List all stores in Vibe Seller',
        'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'vibe_seller_list_tasks',
        'description': (
            'List tasks, optionally filtered by store_id or parent_task_id'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'store_id': {
                    'type': 'string',
                    'description': 'Store ID to filter by (optional)',
                },
                'parent_task_id': {
                    'type': 'string',
                    'description': (
                        'Parent task ID — returns only sub-tasks '
                        'of this parent (optional)'
                    ),
                },
            },
            'required': [],
        },
    },
    {
        'name': 'vibe_seller_create_task',
        'description': 'Create a new task in Vibe Seller',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string', 'description': 'Task title'},
                'store_id': {
                    'type': 'string',
                    'description': 'Store ID (optional)',
                },
                'parent_task_id': {
                    'type': 'string',
                    'description': (
                        'Parent task ID — set this when creating '
                        'sub-tasks from an orchestrator task'
                    ),
                },
                'description': {
                    'type': 'string',
                    'description': 'Task description (optional)',
                },
            },
            'required': ['title'],
        },
    },
    {
        'name': 'vibe_seller_list_cron_jobs',
        'description': 'List all scheduled cron jobs',
        'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'vibe_seller_add_cron_job',
        'description': 'Add a new cron job that creates tasks on a schedule',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'job_id': {'type': 'string', 'description': 'Unique job ID'},
                'task_title': {
                    'type': 'string',
                    'description': 'Task title to create',
                },
                'cron_expression': {
                    'type': 'string',
                    'description': "Cron expression: 'minute hour day month day_of_week'",
                },
                'store_id': {
                    'type': 'string',
                    'description': 'Store ID for the task (optional)',
                },
            },
            'required': ['job_id', 'task_title', 'cron_expression'],
        },
    },
    {
        'name': 'vibe_seller_email_info',
        'description': (
            'Get email DB paths and schema for a store. '
            'Returns per-account: email address, SQLite DB '
            'path, attachments dir, sample sqlite3 queries. '
            'Emails are synced automatically every 5 minutes. '
            'Use sqlite3 CLI to query the DBs directly.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'store_id': {
                    'type': 'string',
                    'description': 'Store ID to get email info for',
                },
            },
            'required': ['store_id'],
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
            'Send an email via a configured email account. '
            'Looks up account by email address. '
            'Returns {ok: true, message_id: ...} on success.'
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
            'Trigger immediate IMAP sync for one email '
            'account. Blocks until sync completes. Returns '
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
        'name': 'vibe_seller_set_task_error',
        'description': (
            'Record an unrecoverable error for the current task '
            '(e.g. browser cannot start, required service down). '
            'The error message is shown to the user in a red '
            'banner. Does NOT transition task status — after you '
            'call this, end your session; the infrastructure '
            'detects the error on cleanup and transitions the '
            'task to FAILED. This preserves post-task knowledge '
            'commit and metadata sync even on failure.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'error': {
                    'type': 'string',
                    'description': ('Error message explaining the failure'),
                },
            },
            'required': ['error'],
        },
    },
    {
        'name': 'vibe_seller_get_schedule_state',
        'description': (
            'Scheduled tasks only. Read a value that a previous run '
            'of the same schedule persisted under `key` — use it to '
            'resume from where the last run left off (e.g. last '
            'processed email timestamp). Returns an object with '
            '`value` (string or null) plus metadata (`key`, '
            '`updated_at`, `updated_by_task_id`). On null, the '
            'response ALSO includes `other_known_keys` — a list of '
            'keys that ARE populated on this schedule. **If your '
            'target cursor appears in `other_known_keys` under a '
            'different name, you hallucinated the key — retry GET '
            'with the listed name instead of treating this as a '
            'first-run.** An empty `other_known_keys` list means no '
            'prior run has written any cursor yet (truly first run). '
            'Scope is resolved server-side from the current task — '
            'you never pass a schedule_id, and for fanout schedules '
            'the cursor is automatically scoped to YOUR store, so '
            'sibling tasks for other stores have their own cursors '
            "and won't clobber yours."
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'key': {
                    'type': 'string',
                    'description': (
                        'Namespaced key, e.g. "email_watermark" or '
                        '"last_order_id". Stable across runs.'
                    ),
                },
            },
            'required': ['key'],
        },
    },
    {
        'name': 'vibe_seller_set_schedule_state',
        'description': (
            'Scheduled tasks only. Persist a value under `key` for '
            'the next run of the same schedule to read. Upsert — '
            'overwrites any previous value. Use when you consumed a '
            'cursor-like resource (emails, orders, messages) and '
            'want the next run to resume cleanly.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'key': {
                    'type': 'string',
                    'description': (
                        'Same key you read with get_schedule_state.'
                    ),
                },
                'value': {
                    'type': 'string',
                    'description': (
                        'Required non-empty string — the cursor to '
                        'persist for the next run. Format depends on '
                        'the key; see the canonical-keys table in the '
                        'task system prompt (Scheduled task — cross-run '
                        'state). For example, `email_watermark` must be '
                        'a unix epoch seconds integer string (not ISO), '
                        'and for a store with linked email accounts it '
                        'must be the `next_watermark` from '
                        'vibe_seller_get_new_emails, written verbatim — '
                        'the server rejects a hand-derived value or one '
                        'below the floor that tool established. NEVER '
                        'pass null or an empty string — if you have '
                        'nothing to persist, do not call this tool.'
                    ),
                    'minLength': 1,
                },
            },
            'required': ['key', 'value'],
        },
    },
    {
        'name': 'vibe_seller_register_finalize',
        'description': (
            'PLAN PHASE, all-stores fanout schedules only. Register a '
            'parent FINALIZE step. A fanout schedule runs this plan '
            'once per store in parallel; normally each store finishes '
            'on its own and nothing happens after. Call this tool if — '
            'and only if — the task needs a SINGLE cross-store step '
            'AFTER every store finishes: e.g. combine all stores into '
            'ONE pull request / report / notification, or retry the '
            'stores that failed. Pass a natural-language `description` '
            'of what that final step should do; at run time ONE extra '
            'task runs it, handed a batch_results.json listing every '
            "store's status + result + output location. If the task is "
            'fully independent per store (no combine, no cross-store '
            'summary), do NOT call this. You never pass a schedule_id — '
            'it is resolved from the current planning task.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'description': {
                    'type': 'string',
                    'description': (
                        'Natural-language instruction for the finalize '
                        'step, e.g. "After all stores finish, bundle '
                        'every store and open ONE PR + send ONE WeCom — '
                        'never one per store." Required, non-empty.'
                    ),
                    'minLength': 1,
                },
            },
            'required': ['description'],
        },
    },
    {
        'name': 'vibe_seller_list_wecom_bots',
        'description': (
            'List configured WeChat Work (企业微信) group bots. '
            'Returns an array of {id, name} — use the id with '
            'vibe_seller_send_wecom_message. Webhook URLs are '
            'never returned (they embed secrets).'
        ),
        'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'vibe_seller_send_wecom_message',
        'description': (
            'Post a message to a WeChat Work group via its '
            'configured bot webhook. Use msgtype="markdown" for '
            'reports with headings/tables (WeCom markdown is a '
            'reduced subset: headings, **bold**, lists, links, '
            'inline code, blockquotes — no tables or images). '
            'Hard limit: 4096 bytes per message; split longer '
            'content across multiple calls.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'bot_id': {
                    'type': 'string',
                    'description': ('Bot ID from vibe_seller_list_wecom_bots.'),
                },
                'content': {
                    'type': 'string',
                    'description': 'Message body (non-empty).',
                    'minLength': 1,
                },
                'msgtype': {
                    'type': 'string',
                    'enum': ['text', 'markdown'],
                    'description': "'text' (default) or 'markdown'.",
                },
            },
            'required': ['bot_id', 'content'],
        },
    },
    {
        'name': 'vibe_seller_send_wecom_file',
        'description': (
            'Send a local file (PDF, image, xlsx, …) to a WeChat '
            'Work group via its configured bot — e.g. a payment '
            'receipt (comprovante) or a report. Pass the bot_id and '
            'an absolute path on this host (task captures under '
            '/tmp, browser downloads under ~/.vibe-seller/downloads). '
            'Limit: 20 MB. The webhook secret stays on the server.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'bot_id': {
                    'type': 'string',
                    'description': ('Bot ID from vibe_seller_list_wecom_bots.'),
                },
                'path': {
                    'type': 'string',
                    'description': ('Absolute path to the file on this host.'),
                    'minLength': 1,
                },
            },
            'required': ['bot_id', 'path'],
        },
    },
    {
        'name': 'vibe_seller_set_task_result',
        'description': (
            'Record the **final task result** shown to the user '
            'in the GUI. The result field renders as markdown.\n'
            '\n'
            'Two ways to call this:\n'
            '\n'
            '1. **Direct content** — pass the result text '
            'directly. Use when the deliverable is short '
            '(roughly < 10 KB): a one-line confirmation, a '
            'short summary, a JSON payload, a metric row.\n'
            '\n'
            '2. **File pointer** — pass a relative path to a '
            'file you already wrote in your CWD (e.g. '
            '`"./NOON_AE_ADS_AUDIT_2026-04-30.md"`). The backend '
            'detects when the value is a file path inside the '
            'task workspace, reads the file, and uses its '
            'contents as the GUI-visible result. **Use this for '
            'long-form deliverables (full reports, audits, '
            'plans).** Compose those with the built-in `Write` '
            'tool first — `Write` streams content as you '
            'compose it, which is much faster than packing a '
            '25KB payload into one MCP tool call (some '
            'providers stall on large monolithic tool inputs).\n'
            '\n'
            'Does NOT mark the task completed — completion is '
            'automatic when your agent session ends and the '
            'post-task cleanup runs. If you never call this, '
            'your last assistant message is saved as the '
            'result.\n'
            '\n'
            'Declares **success**. If the task could not '
            'complete its primary objective, do NOT call this '
            'alone — call `vibe_seller_set_task_error` so the '
            'task lands in FAILED. To preserve partial output '
            'on a failure, call both: this with the partial '
            'output (or its file path), then '
            '`vibe_seller_set_task_error` with the reason.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'result': {
                    'type': 'string',
                    'description': 'Result summary to record',
                },
            },
            'required': ['result'],
        },
    },
    {
        'name': 'vibe_seller_list_skills',
        'description': (
            'List the skills available in this workspace so you can '
            'decide whether to EXTEND an existing one or CREATE a new '
            'one. Call this FIRST when the user asks to "save this '
            'workflow as a skill" / "保存为技能". Returns an array of '
            '{slug, name, description, source, updatable}. `source` is '
            "'builtin' (maintainer-shipped, READ-ONLY — never edit; "
            "reference it instead), 'imported' (installed from a URL), "
            "or 'custom' (user-authored here). `updatable` is true only "
            'for skills you may overwrite with vibe_seller_save_skill '
            '(custom + imported). Match on the `description` — that is '
            'the skill selection signal — not just the slug.'
        ),
        'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'vibe_seller_save_skill',
        'description': (
            'Create or extend a USER-space skill in the shared '
            'workspace so it persists after this task and auto-loads '
            'for future tasks. Writing via the built-in Write tool does '
            "NOT persist — the task's .claude/ copy is discarded — so "
            'this MCP tool is the ONLY way to save a skill.\n'
            '\n'
            'Pass `slug` (lowercase letters/digits/hyphens), the full '
            '`skill_md` (SKILL.md content: YAML frontmatter with `name` '
            'and a trigger-packed third-person `description`, then a '
            'concise heuristic body), and optional `files` (a map of '
            'relative-path → content for bundled references/scripts). '
            'If `slug` already names a custom/imported skill this '
            'OVERWRITES it (extend by passing the merged full content — '
            'read the current SKILL.md first). '
            'HARD RULE: built-in (maintainer-shipped) slugs are '
            'rejected — they are read-only. If the closest match is a '
            'built-in, choose a NEW slug and create a user-space skill '
            'that may reference the built-in.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'slug': {
                    'type': 'string',
                    'description': (
                        'Skill directory slug — lowercase letters, '
                        'digits, hyphens (e.g. "revenue-report").'
                    ),
                },
                'skill_md': {
                    'type': 'string',
                    'description': (
                        'Full SKILL.md content (frontmatter + body).'
                    ),
                    'minLength': 1,
                },
                'files': {
                    'type': 'object',
                    'description': (
                        'Optional bundled files: {relative_path: '
                        'content}. Paths are relative to the skill '
                        'directory; no absolute paths or "..".'
                    ),
                    'additionalProperties': {'type': 'string'},
                },
            },
            'required': ['slug', 'skill_md'],
        },
    },
    {
        'name': 'vibe_seller_generate_image',
        'description': (
            'Generate an image from a text prompt plus optional '
            'reference images, using the configured vision model. '
            'General-purpose: product photos, marketplace listing '
            'images, infographics, banners, illustrations, or an image '
            'the user just wants for fun — any platform, any purpose. '
            'For platform-specific image work (e.g. Amazon listing '
            'images), load the matching skill first if one exists; it '
            "carries that platform's requirements and workflows. "
            "Contract: (1) Write the `prompt` in the USER's language. "
            '(2) When the image must depict a REAL product or person, '
            'pass photos of it as `reference_images` and instruct the '
            'model to replicate them faithfully — never describe its '
            'appearance (material/texture/shape) in words, and state '
            "each reference image's role by position in the prompt "
            '("image 1 is the style reference; images 2-3 are the '
            'product"). (3) This call PAUSES for the user to review and '
            'edit the prompt/model before anything is generated — that '
            'is expected; wait for the result. (4) It fails immediately '
            'if no vision key is configured — tell the user to set it '
            'in Settings → AI → Vision. On success it returns the saved '
            'workspace `path`; view the file to self-audit against the '
            'references and the requested text, and regenerate with a '
            'corrected prompt if anything differs. (5) REVISING an image you '
            'already generated — for ANY follow-up change the user asks '
            '(lighter/darker, bigger/smaller, recolour, remove or add an '
            'element, change composition, etc.): call this tool again and '
            'ALWAYS include the PREVIOUS generated image (its workspace '
            '`path`, e.g. generated_images/…) as a reference_image, with a '
            'prompt describing ONLY the change — so the model EDITS that '
            'result and preserves what the user already liked, instead of '
            'regenerating from scratch and drifting. If the user also '
            'supplies NEW photo(s) for the change (e.g. "this image is good, '
            'add the dog from this photo into the middle"), pass BOTH the '
            'previous generated image AND the new photo(s) as '
            "reference_images, and state each image's role by position in "
            'the prompt (e.g. "image 1 is the current design to keep; image '
            '2 is the dog — place it in the center").'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': (
                        "Image prompt in the user's language. Describe "
                        'composition, background, style, and any '
                        'on-image text (spelled exactly). If reference '
                        'images are passed, assign each a role by '
                        'position and do NOT describe the referenced '
                        "subject's appearance in words."
                    ),
                    'minLength': 1,
                },
                'reference_images': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        'Reference image URLs and/or workspace-relative '
                        'file paths, in the order the prompt refers to '
                        "them. Up to 14. The referenced subject's true "
                        'appearance is taken from these. When the user '
                        'provides MULTIPLE photos of the SAME product '
                        '(different angles/views/details), pass them ALL '
                        'as references for ONE generation — they are views '
                        'of a single subject, not separate products. Do '
                        'NOT generate one image per photo. Only make '
                        'separate calls if the user clearly asked for '
                        'several DISTINCT products/images; if unsure which '
                        'they meant, ask before generating.'
                    ),
                },
                'model': {
                    'type': 'string',
                    'enum': ['nano-banana-pro', 'nano-banana-2'],
                    'description': (
                        'nano-banana-pro (default): highest quality and '
                        'reliable on-image text rendering. '
                        'nano-banana-2: cheaper/faster, good for images '
                        'without text.'
                    ),
                },
                'output_name': {
                    'type': 'string',
                    'description': (
                        'Output file name, e.g. "banner.png". Saved '
                        'under the task workspace and shown inline to '
                        'the user.'
                    ),
                },
                'kind': {
                    'type': 'string',
                    'description': (
                        'Optional short label shown on the confirm card '
                        '(e.g. "main", "infographic", "banner", "fun").'
                    ),
                },
            },
            'required': ['prompt'],
        },
    },
]
