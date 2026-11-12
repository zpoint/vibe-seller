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
  - list_chat_bots / send_chat_message: Post to a group chat via a
    chat bot webhook (WeCom / DingTalk / Feishu / Slack)
  - list_skills / save_skill / import_skill: the workspace's agent
    skills (import_skill installs a whole skill DIRECTORY from a local
    path this process packs, or a URL the server fetches — bytes
    never through the LLM)
"""

import asyncio
import json
import sys
from urllib.parse import quote, urlencode

import httpx

from app import vision
from app.config import BACKEND_PORT, LOCALHOST
from app.mcp_tool_schemas import TOOLS
from app.notifiers.base import PLATFORMS
from app.skill_pack import pack_skill_dir
from app.text_utils import sanitize_json

# ``--tools`` sets a hard allowlist for non-task sessions: the
# workspace assistant gets the skill tools and NOTHING task-only
# (see WorkspaceAgentSession.start and main()). Applied twice — to
# ``tools/list`` (the agent never sees the rest) and to dispatch (a
# call for a hidden tool is refused here, not merely undiscovered).
_ALLOWED_TOOLS: set[str] | None = None


# Tools that require a configured capability and are hidden from the
# tool list until that capability is set up — so the agent never sees
# (or dead-end-calls) a tool it cannot use, and the tool list stays
# clean. Industry practice: conditionally register rather than
# advertise-then-error. When hidden, a one-line breadcrumb in the base
# system prompt tells the agent to guide the user to configure it.
def _visible_tools() -> list:
    """The tool list for this session, minus capability-gated tools
    whose prerequisite is not configured, minus anything outside the
    ``--tools`` allowlist when one is set. Read at ``tools/list`` time
    from the local config — the MCP process is per-session, so the set
    is stable within a session (no ``list_changed`` needed)."""
    tools = TOOLS
    if not (vision.get_kie_api_key() or vision.is_fake()):
        tools = [
            t for t in tools if t.get('name') != 'vibe_seller_generate_image'
        ]
    if _ALLOWED_TOOLS is not None:
        tools = [t for t in tools if t.get('name') in _ALLOWED_TOOLS]
    return tools


# MCP server runs as a standalone process, communicating via stdin/stdout JSON-RPC.
# This is a minimal implementation of the MCP protocol for tool serving.


# Default from config; overridden by --port CLI arg if provided
_config: dict[str, str | None] = {
    'api_base': f'http://{LOCALHOST}:{BACKEND_PORT}',
    'auth_token': None,
    'task_id': None,
}


def _platform_limit(platform: str | None) -> int:
    """The message size limit for a chat platform.

    Read from the same registry the API uses, so the agent's idea of
    the limit can never drift from the server's.
    """
    plat = PLATFORMS.get(platform or '')
    return plat.max_message if plat else 4096


async def call_api(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float | None = 60,
    *,
    content: bytes | None = None,
    content_type: str = 'application/octet-stream',
) -> dict:
    """Call the vibe-seller API.

    ``timeout`` defaults to 60s; image generation passes ``None`` (no
    timeout) because that endpoint blocks awaiting a human confirmation
    before it calls out to the image model. ``content`` sends RAW
    BYTES as the body instead of JSON (the packed skill archive) —
    the whole point of that path is that file bytes reach the server
    without ever being serialized into a tool argument.
    """
    url = f'{_config["api_base"]}{path}'
    # Sanitize BEFORE httpx serializes: it dumps with
    # ``ensure_ascii=False`` and encodes UTF-8, so a lone surrogate in
    # agent text raises HERE, in this process, and the server-side
    # SafeStr fields never see the request. See app.text_utils.
    body = sanitize_json(body) if body else body
    headers: dict[str, str] = {}
    if content is not None:
        headers['Content-Type'] = content_type
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
            if content is not None:
                resp = await client.post(
                    url, content=content, headers=headers, cookies=cookies
                )
            else:
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


async def _import_skill(arguments: dict) -> dict:
    """``vibe_seller_import_skill``: one install, two sources.

    ``path`` — this process (it runs on the agent's machine) reads
    and packs the directory, and POSTs the archive: the file bytes
    never pass through the LLM, and the SERVER never reads a
    caller-named path (the route receives an archive exactly like a
    presigned upload, guarded the same way). ``url`` — the SERVER
    fetches it (guards in app.skill_fetch). Both land on the same
    staged extraction + install path as the external archive import.
    """
    path = arguments.get('path')
    url = arguments.get('url')
    if bool(path) == bool(url):
        raise ValueError(
            'exactly one of path / url is required — path names a '
            "skill directory on this machine, url is the skill's URL"
        )
    params: dict[str, str] = {
        'overwrite': 'true' if arguments.get('overwrite') else 'false',
    }
    if arguments.get('slug'):
        params['slug'] = str(arguments['slug'])
    if path:
        data = await asyncio.to_thread(pack_skill_dir, str(path))
        params['origin_url'] = str(arguments.get('origin_url') or path)
        return await call_api(
            'POST',
            f'/api/workspace/skills/import-local?{urlencode(params)}',
            content=data,
            content_type='application/gzip',
        )
    body: dict = {
        'url': str(url),
        'overwrite': bool(arguments.get('overwrite')),
    }
    if arguments.get('slug'):
        body['slug'] = str(arguments['slug'])
    # A server-side fetch is bounded by its own timeouts; this only
    # waits for it.
    return await call_api(
        'POST', '/api/workspace/skills/import-url', body, timeout=180
    )


async def handle_tool_call(name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""
    if _ALLOWED_TOOLS is not None and name not in _ALLOWED_TOOLS:
        # The session was started with a tool allowlist (the workspace
        # assistant gets the skill tools only). Refuse on dispatch too,
        # not just in tools/list — defense in depth, and the message
        # says so instead of pretending the tool does not exist.
        return json.dumps({
            'error': f'tool {name!r} is not available in this session'
        })
    try:
        if name == 'vibe_seller_list_stores':
            result = await call_api('GET', '/api/stores')
        elif name == 'vibe_seller_ads_call':
            result = await call_api(
                'POST',
                '/api/ads/call',
                {
                    'path': arguments['path'],
                    'method': arguments.get('method', 'GET'),
                    'params': arguments.get('params'),
                    'body': arguments.get('body'),
                    'store': arguments.get('store'),
                    'marketplace': arguments.get('marketplace'),
                    'task_id': _config['task_id'],
                },
            )
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
            # The task id lets the server hold a knowledge-cleanup run
            # to deletion-only edits (app/knowledge_contract.py).
            tid = _config['task_id']
            result = await call_api(
                'PUT',
                f'/api/workspace/file?path={quote(arguments["path"], safe="/")}'
                + (f'&task_id={tid}' if tid else ''),
                {'content': arguments['content']},
            )
        elif name == 'vibe_seller_delete_workspace_lines':
            tid = _config['task_id']
            result = await call_api(
                'POST',
                f'/api/workspace/file/delete-lines?path={quote(arguments["path"], safe="/")}'
                + (f'&task_id={tid}' if tid else ''),
                {
                    'line_count': arguments.get('line_count'),
                    'ranges': arguments.get('ranges') or [],
                },
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
        elif name == 'vibe_seller_declare_ad_task':
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/ad-declaration',
                {
                    'kind': arguments.get('kind'),
                    'scope': arguments.get('scope') or {},
                },
            )
        elif name == 'vibe_seller_declare_report_run':
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/report-declaration',
                {'month': arguments.get('month')},
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
        elif name == 'vibe_seller_list_chat_bots':
            # API returns masked URLs and no secrets; keep it that way
            # and add the platform's size limit, which the agent needs
            # to know before it writes a long report.
            bots = await call_api('GET', '/api/chat-bots')
            result = [
                {
                    'id': b['id'],
                    'name': b['name'],
                    'platform': b.get('platform', 'wecom'),
                    'can_send_file': b.get('can_send_file', False),
                    'max_message': _platform_limit(b.get('platform')),
                }
                for b in (bots if isinstance(bots, list) else [])
            ]
        elif name == 'vibe_seller_send_chat_message':
            send_body = {
                'content': arguments['content'],
                'msgtype': arguments.get('msgtype', 'text'),
            }
            result = await call_api(
                'POST',
                f'/api/chat-bots/{arguments["bot_id"]}/send',
                send_body,
            )
        elif name == 'vibe_seller_send_chat_file':
            result = await call_api(
                'POST',
                f'/api/chat-bots/{arguments["bot_id"]}/send-file',
                {'path': arguments['path']},
            )
        elif name == 'vibe_seller_set_task_result':
            # Save-result-only. Does NOT transition task status —
            # status is managed by auto_run_task cleanup after the
            # agent session ends. See app/routers/tasks.py:set_task_result.
            result = await call_api(
                'POST',
                f'/api/tasks/{_config["task_id"]}/result',
                {
                    # Pass both through as the agent sent them: exactly
                    # one must be non-null, and the ROUTE enforces that.
                    # An absent key is None, not a KeyError, so the
                    # agent gets the readable 422 instead of a crash in
                    # this process.
                    'result': arguments.get('result'),
                    'result_file': arguments.get('result_file'),
                    'incomplete': arguments.get('incomplete') or [],
                },
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
            # task_id scopes the list (ads routing); a session without
            # one (the workspace assistant) gets the full list.
            task = _config['task_id']
            path = (
                f'/api/workspace/skills?task_id={quote(task, safe="")}'
                if task
                else '/api/workspace/skills'
            )
            result = await call_api('GET', path)
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
        elif name == 'vibe_seller_import_skill':
            result = await _import_skill(arguments)
        else:
            return json.dumps({'error': f'Unknown tool: {name}'})

        return json.dumps(result, ensure_ascii=False, indent=2)
    except KeyError as e:
        # ``arguments['path']`` on a call without it: say what is missing
        # (a bare ``"'path'"`` named nothing the model could act on).
        return json.dumps({'error': f'missing required argument {e}'})
    except Exception as e:
        return json.dumps({'error': str(e)})


def _configure(args: list[str]) -> None:
    """Apply the launching server's CLI args (and enforce them).

    Split from ``main()`` so the rules — an unknown ``--tools`` name
    kills the session, ``--task-id`` is required unless the session is
    a restricted one — are unit-testable without running the stdio
    loop.
    """
    global _ALLOWED_TOOLS
    for i, arg in enumerate(args):
        if arg == '--token' and i + 1 < len(args):
            _config['auth_token'] = args[i + 1]
        elif arg == '--port' and i + 1 < len(args):
            _config['api_base'] = f'http://{LOCALHOST}:{args[i + 1]}'
        elif arg == '--task-id' and i + 1 < len(args):
            _config['task_id'] = args[i + 1]
        elif arg == '--tools' and i + 1 < len(args):
            names = {n.strip() for n in args[i + 1].split(',') if n.strip()}
            unknown = names - {t['name'] for t in TOOLS}
            if unknown:
                # A typo'd allowlist would silently strip every tool;
                # fail at launch, loudly, naming the bad entries.
                sys.stderr.write(
                    f'ERROR: --tools names unknown tools: '
                    f'{sorted(unknown)}\n'
                )
                sys.exit(1)
            _ALLOWED_TOOLS = names

    if not _config['task_id'] and _ALLOWED_TOOLS is None:
        # Task-id keyed: the default full tool set. A restricted
        # session (skill tools only) needs none — those tools work
        # without task context, exactly like the external MCP's.
        sys.stderr.write('ERROR: --task-id is required\n')
        sys.exit(1)


def main():
    """Run MCP server using stdio transport."""
    _configure(sys.argv[1:])

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
