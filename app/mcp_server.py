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
  - get_new_emails: Scheduled sweep — only emails since the cursor
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

from app import vision
from app.config import BACKEND_PORT, LOCALHOST
from app.mcp_tool_schemas import TOOLS


# Tools that require a configured capability and are hidden from the
# tool list until that capability is set up — so the agent never sees
# (or dead-end-calls) a tool it cannot use, and the tool list stays
# clean. Industry practice: conditionally register rather than
# advertise-then-error. When hidden, a one-line breadcrumb in the base
# system prompt tells the agent to guide the user to configure it.
def _visible_tools() -> list:
    """The tool list for this task, minus capability-gated tools whose
    prerequisite is not configured. Read at ``tools/list`` time from the
    local config — the MCP process is per-task, so the set is stable
    within a task (no ``list_changed`` needed)."""
    if vision.get_kie_api_key() or vision.is_fake():
        return TOOLS
    return [t for t in TOOLS if t.get('name') != 'vibe_seller_generate_image']


# MCP server runs as a standalone process, communicating via stdin/stdout JSON-RPC.
# This is a minimal implementation of the MCP protocol for tool serving.


# Default from config; overridden by --port CLI arg if provided
_config: dict[str, str | None] = {
    'api_base': f'http://{LOCALHOST}:{BACKEND_PORT}',
    'auth_token': None,
    'task_id': None,
}


async def call_api(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float | None = 60,
) -> dict:
    """Call the vibe-seller API.

    ``timeout`` defaults to 60s; image generation passes ``None`` (no
    timeout) because that endpoint blocks awaiting a human confirmation
    before it calls out to the image model.
    """
    url = f'{_config["api_base"]}{path}'
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if _config['auth_token']:
        cookies['auth_token'] = _config['auth_token']
    # ``trust_env=False`` is load-bearing: this client ONLY ever talks to
    # the local backend over loopback (``api_base`` is 127.0.0.1:<port>).
    # httpx honours ``HTTP_PROXY``/``ALL_PROXY`` by default, and a machine
    # running a proxy (e.g. clash) typically exports those with an EMPTY
    # ``NO_PROXY`` — so without this, every internal call is routed through
    # the proxy. When the proxy is down or can't serve loopback, the call
    # dies with "All connection attempts failed" even though the backend
    # is up (agents then misread it and fall back to local tools). Internal
    # traffic must never depend on the user's proxy.
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
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
        elif name == 'vibe_seller_get_new_emails':
            qs = ''
            if arguments.get('lookback_hours') is not None:
                qs = f'?lookback_hours={int(arguments["lookback_hours"])}'
            result = await call_api(
                'GET',
                f'/api/tasks/{_config["task_id"]}/new-emails{qs}',
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
        elif name == 'vibe_seller_register_finalize':
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/register-finalize',
                {'description': arguments['description']},
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
        elif name == 'vibe_seller_send_wecom_file':
            result = await call_api(
                'POST',
                f'/api/wecom-bots/{arguments["bot_id"]}/send-file',
                {'path': arguments['path']},
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
        elif name == 'vibe_seller_generate_image':
            # Blocks server-side awaiting the user's confirm/edit, then
            # generates. NO timeout — like AskUserQuestion, it waits for
            # the user however long they take.
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/image/generate',
                {
                    'prompt': arguments['prompt'],
                    'model': arguments.get('model'),
                    'reference_images': arguments.get('reference_images', []),
                    'output_name': arguments.get('output_name'),
                    'kind': arguments.get('kind'),
                },
                timeout=None,
            )
        elif name == 'vibe_seller_list_skills':
            result = await call_api('GET', '/api/workspace/skills')
        elif name == 'vibe_seller_save_skill':
            # Upsert a user-space skill in the SHARED workspace. The
            # backend hard-rejects built-in (maintainer-synced) slugs.
            slug = quote(arguments['slug'], safe='')
            save_body: dict = {'skill_md': arguments['skill_md']}
            if arguments.get('files'):
                save_body['files'] = arguments['files']
            result = await call_api(
                'PUT',
                f'/api/workspace/skills/{slug}',
                save_body,
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
                    'result': {'tools': _visible_tools()},
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
