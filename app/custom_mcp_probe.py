"""Connect to one custom MCP server and list its tools — the "Test" button.

A task is the wrong place to learn a token is wrong: the agent sees
an MCP server fail and carries on without its tools. This probe does
the same handshake the task's Claude Code does — ``initialize``,
``notifications/initialized``, ``tools/list`` — on the server's own
transport, and answers with the server's name and its tools, or with
what failed.

Minimal client on purpose: three JSON-RPC calls over the transports
:func:`app.custom_mcp.normalize_server` admits (Streamable HTTP, the
legacy SSE transport, stdio). The ``mcp`` SDK is not a declared
dependency, and the probe needs nothing beyond those calls.
"""

import asyncio
import contextlib
import json
import os
import shutil
from urllib.parse import urljoin

import httpx

from app.url_redact import mask_url

PROTOCOL_VERSION = '2025-06-18'
PROBE_TIMEOUT_S = 30.0
# A local server started through npx/uvx may download its package on
# first start.
STDIO_TIMEOUT_S = 90.0
_MAX_TOOLS = 200


class ProbeError(Exception):
    """The server could not be reached, refused us, or spoke nonsense."""


def _request(req_id: int, method: str, params: dict | None = None) -> dict:
    msg: dict = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
    if params is not None:
        msg['params'] = params
    return msg


_INITIALIZE = _request(
    1,
    'initialize',
    {
        'protocolVersion': PROTOCOL_VERSION,
        'capabilities': {},
        'clientInfo': {'name': 'vibe-seller', 'version': '1'},
    },
)
_INITIALIZED = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
_LIST_TOOLS = _request(2, 'tools/list')


def _result(msg: dict, what: str) -> dict:
    if 'error' in msg:
        err = msg['error'] or {}
        raise ProbeError(f'{what} failed: {err.get("message") or err}')
    result = msg.get('result')
    if not isinstance(result, dict):
        raise ProbeError(f'{what}: the server sent no result')
    return result


def _summary(init: dict, tools: dict) -> dict:
    info = init.get('serverInfo') or {}
    listed = tools.get('tools') or []
    return {
        'server_name': info.get('name') or '',
        'server_version': info.get('version') or '',
        'tool_count': len(listed),
        'tools': [
            {
                'name': t.get('name', ''),
                'description': (t.get('description') or '')[:200],
            }
            for t in listed[:_MAX_TOOLS]
            if isinstance(t, dict)
        ],
    }


def _sse_messages(text: str) -> list[dict]:
    """The JSON-RPC messages in an SSE body (``data:`` lines)."""
    out = []
    for block in text.replace('\r\n', '\n').split('\n\n'):
        data = '\n'.join(
            line[5:].lstrip()
            for line in block.split('\n')
            if line.startswith('data:')
        )
        if data:
            with contextlib.suppress(ValueError):
                out.append(json.loads(data))
    return out


def _http_error(resp: httpx.Response) -> ProbeError:
    if resp.status_code in (401, 403):
        return ProbeError(
            f'HTTP {resp.status_code}: the server refused the credentials '
            '— check the token / headers'
        )
    if resp.status_code == 404:
        return ProbeError('HTTP 404: no MCP server at this address')
    body = resp.text[:200].strip()
    return ProbeError(f'HTTP {resp.status_code}: {body}')


# ── Streamable HTTP ─────────────────────────────────────────────


async def _http_call(
    client: httpx.AsyncClient, url: str, headers: dict, msg: dict
) -> tuple[dict | None, httpx.Response]:
    resp = await client.post(url, headers=headers, json=msg)
    if resp.status_code == 202 or 'id' not in msg:
        if resp.status_code >= 400:
            raise _http_error(resp)
        return None, resp
    if resp.status_code >= 400:
        raise _http_error(resp)
    ctype = resp.headers.get('content-type', '')
    if 'text/event-stream' in ctype:
        msgs = _sse_messages(resp.text)
    else:
        try:
            body = resp.json()
        except ValueError:
            raise ProbeError('The server did not answer with JSON') from None
        msgs = body if isinstance(body, list) else [body]
    for m in msgs:
        if isinstance(m, dict) and m.get('id') == msg['id']:
            return m, resp
    raise ProbeError(f'No answer to {msg["method"]}')


async def _probe_http(config: dict) -> dict:
    url = config['url']
    headers = {
        **config.get('headers', {}),
        'Accept': 'application/json, text/event-stream',
    }
    async with httpx.AsyncClient(
        timeout=PROBE_TIMEOUT_S, follow_redirects=True
    ) as client:
        init_msg, resp = await _http_call(client, url, headers, _INITIALIZE)
        init = _result(init_msg or {}, 'initialize')
        session = resp.headers.get('mcp-session-id')
        if session:
            headers['Mcp-Session-Id'] = session
        version = init.get('protocolVersion')
        if isinstance(version, str):
            headers['MCP-Protocol-Version'] = version
        await _http_call(client, url, headers, _INITIALIZED)
        tools_msg, _ = await _http_call(client, url, headers, _LIST_TOOLS)
        tools = _result(tools_msg or {}, 'tools/list')
        if session:
            with contextlib.suppress(httpx.HTTPError):
                await client.delete(url, headers=headers)
        return _summary(init, tools)


# ── legacy SSE ──────────────────────────────────────────────────


async def _probe_sse(config: dict) -> dict:
    """GET opens the stream; its ``endpoint`` event names where to POST;
    answers come back on the stream."""
    url = config['url']
    headers = dict(config.get('headers', {}))
    async with (
        httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client,
        client.stream(
            'GET',
            url,
            headers={**headers, 'Accept': 'text/event-stream'},
        ) as stream,
    ):
        if stream.status_code >= 400:
            await stream.aread()
            raise _http_error(stream)
        events = _sse_events(stream)
        endpoint = None
        async for event, data in events:
            if event == 'endpoint':
                endpoint = urljoin(url, data.strip())
                break
        if endpoint is None:
            raise ProbeError('The SSE stream never named its endpoint')

        async def call(msg: dict) -> dict | None:
            resp = await client.post(endpoint, headers=headers, json=msg)
            if resp.status_code >= 400:
                raise _http_error(resp)
            if 'id' not in msg:
                return None
            async for event, data in events:
                if event not in ('message', ''):
                    continue
                with contextlib.suppress(ValueError):
                    m = json.loads(data)
                    if isinstance(m, dict) and m.get('id') == msg['id']:
                        return m
            raise ProbeError(f'No answer to {msg["method"]}')

        init = _result(await call(_INITIALIZE) or {}, 'initialize')
        await call(_INITIALIZED)
        tools = _result(await call(_LIST_TOOLS) or {}, 'tools/list')
        return _summary(init, tools)


async def _sse_events(stream: httpx.Response):
    event, data = '', []
    async for line in stream.aiter_lines():
        if not line:
            if data:
                yield event, '\n'.join(data)
            event, data = '', []
        elif line.startswith('event:'):
            event = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())


# ── stdio ───────────────────────────────────────────────────────


async def _probe_stdio(config: dict) -> dict:
    env = {**os.environ, **config.get('env', {})}
    command = await asyncio.to_thread(
        shutil.which, config['command'], path=env.get('PATH')
    )
    if command is None:
        raise ProbeError(
            f'"{config["command"]}" was not found on this machine — '
            'install it first'
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *config.get('args', []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as e:
        raise ProbeError(f'Could not start the server: {e}') from e
    assert proc.stdin and proc.stdout and proc.stderr

    async def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + '\n').encode())
        await proc.stdin.drain()

    async def answer(req_id: int) -> dict:
        while True:
            line = await proc.stdout.readline()
            if not line:
                err = (await proc.stderr.read(2000)).decode(errors='replace')
                raise ProbeError(
                    'The server exited before answering'
                    + (f': {err.strip()}' if err.strip() else '')
                )
            with contextlib.suppress(ValueError):
                m = json.loads(line)
                if isinstance(m, dict) and m.get('id') == req_id:
                    return m

    try:
        await send(_INITIALIZE)
        init = _result(await answer(1), 'initialize')
        await send(_INITIALIZED)
        await send(_LIST_TOOLS)
        tools = _result(await answer(2), 'tools/list')
        return _summary(init, tools)
    finally:
        if proc.returncode is None:
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()


async def probe(config: dict) -> dict:
    """List the tools of the server *config* describes.

    Returns ``{server_name, server_version, tool_count, tools}``;
    raises :class:`ProbeError` with a message meant for the user.
    """
    kind = config.get('type', 'stdio')
    runner = {
        'http': _probe_http,
        'sse': _probe_sse,
        'stdio': _probe_stdio,
    }[kind]
    timeout = STDIO_TIMEOUT_S if kind == 'stdio' else PROBE_TIMEOUT_S
    try:
        return await asyncio.wait_for(runner(config), timeout)
    except TimeoutError:
        raise ProbeError(f'No answer within {int(timeout)} seconds') from None
    except httpx.HTTPError as e:
        # httpx messages can embed the request URL — and the URL can
        # carry the server's token.
        detail = str(e) or type(e).__name__
        if config.get('url'):
            detail = detail.replace(config['url'], mask_url(config['url']))
        raise ProbeError(f'Could not connect: {detail}') from e
