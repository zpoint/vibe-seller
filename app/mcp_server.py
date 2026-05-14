"""
MCP (Model Context Protocol) server for vibe-seller.

Provides tools that Claude Code can auto-discover when --add-dir is used
with the workspace directory. Register this in .mcp.json for tool auto-discovery.

Tools provided:
  - list_stores: List all stores
  - list_tasks: List tasks for a store
  - create_task: Create a new task
  - list_cron_jobs: List scheduled cron jobs
  - add_cron_job: Add a new cron job
  - email_info: Get email DB paths + schema for a store
  - send_email: Send email via configured account
  - sync_email_now: Trigger immediate IMAP sync for one account
  - write_workspace_file: Write file via relative path (stores/, knowledge/)
  - list_wecom_bots / send_wecom_message: Post to WeChat Work groups
"""

import asyncio
import json
import sys
from urllib.parse import quote

import httpx

from app.config import BACKEND_PORT, LOCALHOST

# MCP server runs as a standalone process, communicating via stdin/stdout JSON-RPC.
# This is a minimal implementation of the MCP protocol for tool serving.


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
                        'a unix epoch seconds integer string (not ISO). '
                        'NEVER pass null or an empty string — if you '
                        'have nothing to persist, do not call this '
                        'tool.'
                    ),
                    'minLength': 1,
                },
            },
            'required': ['key', 'value'],
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
]

# Default from config; overridden by --port CLI arg if provided
_config: dict[str, str | None] = {
    'api_base': f'http://{LOCALHOST}:{BACKEND_PORT}',
    'auth_token': None,
    'task_id': None,
}


async def call_api(method: str, path: str, body: dict | None = None) -> dict:
    """Call the vibe-seller API."""
    url = f'{_config["api_base"]}{path}'
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if _config['auth_token']:
        cookies['auth_token'] = _config['auth_token']
    async with httpx.AsyncClient(timeout=60) as client:
        if method == 'GET':
            resp = await client.get(url, headers=headers, cookies=cookies)
        elif method == 'POST':
            resp = await client.post(
                url, json=body or {}, headers=headers, cookies=cookies
            )
        elif method == 'PUT':
            resp = await client.put(
                url, json=body or {}, headers=headers, cookies=cookies
            )
        elif method == 'DELETE':
            resp = await client.delete(url, headers=headers, cookies=cookies)
        else:
            raise ValueError(f'Unknown method: {method}')
        return resp.json()


async def handle_tool_call(name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""
    try:
        if name == 'vibe_seller_list_stores':
            result = await call_api('GET', '/api/stores')
        elif name == 'vibe_seller_list_tasks':
            params = []
            if arguments.get('store_id'):
                params.append(f'store_id={arguments["store_id"]}')
            if arguments.get('parent_task_id'):
                params.append(f'parent_task_id={arguments["parent_task_id"]}')
            qs = '&'.join(params)
            path = f'/api/tasks?{qs}' if qs else '/api/tasks'
            result = await call_api('GET', path)
        elif name == 'vibe_seller_create_task':
            result = await call_api(
                'POST',
                '/api/tasks',
                {
                    'title': arguments['title'],
                    'store_id': arguments.get('store_id'),
                    'parent_task_id': arguments.get('parent_task_id'),
                    'description': arguments.get('description'),
                },
            )
        elif name == 'vibe_seller_list_cron_jobs':
            result = await call_api('GET', '/api/cron/jobs')
        elif name == 'vibe_seller_add_cron_job':
            result = await call_api(
                'POST',
                '/api/cron/jobs',
                {
                    'job_id': arguments['job_id'],
                    'task_title': arguments['task_title'],
                    'cron_expression': arguments['cron_expression'],
                    'store_id': arguments.get('store_id'),
                },
            )
        elif name == 'vibe_seller_email_info':
            store_id = arguments['store_id']
            result = await call_api(
                'GET',
                f'/api/email-accounts/info-by-store/{store_id}',
            )
        elif name == 'vibe_seller_send_email':
            account_email = arguments['account_email']
            # Look up account by email to get its ID
            all_accounts = await call_api('GET', '/api/email-accounts')
            account_id = None
            for acct in all_accounts:
                if acct.get('email') == account_email:
                    account_id = acct['id']
                    break
            if not account_id:
                result = {'error': (f'No account found for {account_email}')}
            else:
                send_body: dict[str, str] = {
                    'to': arguments['to'],
                    'subject': arguments['subject'],
                    'body': arguments['body'],
                }
                if arguments.get('body_html'):
                    send_body['body_html'] = arguments['body_html']
                result = await call_api(
                    'POST',
                    f'/api/email-accounts/{account_id}/send',
                    send_body,
                )
        elif name == 'vibe_seller_sync_email_now':
            result = await call_api(
                'POST',
                '/api/email-accounts/sync-now',
                {'account_email': arguments['account_email']},
            )
        elif name == 'vibe_seller_write_workspace_file':
            result = await call_api(
                'PUT',
                f'/api/workspace/file?path={arguments["path"]}',
                {'content': arguments['content']},
            )
        elif name == 'vibe_seller_set_task_error':
            # Annotate-only. Saves error + category, does NOT
            # transition task status — status is managed by
            # auto_run_task cleanup after the agent session
            # ends. See app/routers/tasks.py:set_task_error.
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/error',
                {'error': arguments['error']},
            )
        elif name == 'vibe_seller_get_schedule_state':
            key = quote(arguments['key'], safe='')
            result = await call_api(
                'GET',
                f'/api/tasks/{_config["task_id"]}/schedule-state/{key}',
            )
        elif name == 'vibe_seller_set_schedule_state':
            key = quote(arguments['key'], safe='')
            result = await call_api(
                'PUT',
                f'/api/tasks/{_config["task_id"]}/schedule-state/{key}',
                {'value': arguments.get('value')},
            )
        elif name == 'vibe_seller_list_wecom_bots':
            # API returns masked URLs; strip them so the agent only
            # sees {id, name} and never a webhook secret.
            bots = await call_api('GET', '/api/wecom-bots')
            result = [
                {'id': b['id'], 'name': b['name']}
                for b in (bots if isinstance(bots, list) else [])
            ]
        elif name == 'vibe_seller_send_wecom_message':
            send_body = {
                'content': arguments['content'],
                'msgtype': arguments.get('msgtype', 'text'),
            }
            result = await call_api(
                'POST',
                f'/api/wecom-bots/{arguments["bot_id"]}/send',
                send_body,
            )
        elif name == 'vibe_seller_set_task_result':
            # Save-result-only. Does NOT transition task status —
            # status is managed by auto_run_task cleanup after the
            # agent session ends. See app/routers/tasks.py:set_task_result.
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/result',
                {'result': arguments['result']},
            )
        else:
            return json.dumps({'error': f'Unknown tool: {name}'})

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': str(e)})


def main():
    """Run MCP server using stdio transport."""
    # Parse CLI args passed by the launching server
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--token' and i + 1 < len(args):
            _config['auth_token'] = args[i + 1]
        elif arg == '--port' and i + 1 < len(args):
            _config['api_base'] = f'http://{LOCALHOST}:{args[i + 1]}'
        elif arg == '--task-id' and i + 1 < len(args):
            _config['task_id'] = args[i + 1]

    if not _config['task_id']:
        sys.stderr.write('ERROR: --task-id is required\n')
        sys.exit(1)

    async def run():
        while True:
            line = sys.stdin.readline()
            if not line:
                break

            try:
                request = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            method = request.get('method', '')
            req_id = request.get('id')

            if method == 'initialize':
                response = {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'protocolVersion': '2024-11-05',
                        'capabilities': {'tools': {}},
                        'serverInfo': {
                            'name': 'vibe-seller',
                            'version': '0.1.0',
                        },
                    },
                }
            elif method == 'tools/list':
                response = {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {'tools': TOOLS},
                }
            elif method == 'tools/call':
                tool_name = request.get('params', {}).get('name', '')
                arguments = request.get('params', {}).get('arguments', {})
                result_text = await handle_tool_call(tool_name, arguments)
                response = {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{'type': 'text', 'text': result_text}],
                    },
                }
            elif method == 'notifications/initialized':
                continue  # No response needed
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'error': {
                        'code': -32601,
                        'message': f'Method not found: {method}',
                    },
                }

            sys.stdout.write(json.dumps(response) + '\n')
            sys.stdout.flush()

    asyncio.run(run())


if __name__ == '__main__':
    main()
