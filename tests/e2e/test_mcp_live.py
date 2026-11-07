"""Drive the hosted MCP endpoint as an external client would — live.

Unlike ``test_agent_mcp.py`` (which drives a mock-CLI server the suite
starts itself), this module talks to **whatever server the operator
points it at** and creates exactly one report-only task there. It is
skipped unless the caller supplies the endpoint and a token:

    VIBE_MCP_URL=http://127.0.0.1:7777/api/mcp   # or the base URL
    VIBE_MCP_TOKEN=vst_...                       # Settings -> API tokens
    VIBE_MCP_STORE=<store id or name>            # the store to act on
    VIBE_MCP_TIMEOUT_SECONDS=3600                # optional, whole run

``VIBE_MCP_URL`` may be the base URL or the full endpoint; the client
appends ``/api/mcp`` when it is missing.

The run is read-only apart from that one task: no settings, no stores,
no schedules, no follow-up messages, no stop/retry/delete, and nothing
is read from disk — the token comes from the environment. The task is
left on the server afterwards on purpose: deleting it would take the
evidence with it.

A task that parks on a question is answered the way a real client
would — `answer_question`, picking the first option (the agent marks
the recommended one first) — because that is part of driving the task,
not a second write to the server. Capped at
``MAX_ANSWER_ROUNDS`` questions per run; anything beyond that fails
the run with the question text rather than guessing at an open-ended
ask.
"""

import json
import os
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

MCP_URL = os.environ.get('VIBE_MCP_URL', '').strip().rstrip('/')
TOKEN = os.environ.get('VIBE_MCP_TOKEN', '').strip()
STORE_REF = os.environ.get('VIBE_MCP_STORE', '').strip()
#: The whole round trip: one report-only ad task, start to result.
BUDGET_SECONDS = int(os.environ.get('VIBE_MCP_TIMEOUT_SECONDS', '3600'))

if not MCP_URL or not TOKEN:
    pytest.skip(
        'VIBE_MCP_URL / VIBE_MCP_TOKEN not set — skipping the live MCP '
        'e2e. Point it at a running server and mint an API Key under '
        'Settings -> Integrations -> API Key.',
        allow_module_level=True,
    )

if not MCP_URL.endswith('/api/mcp'):
    MCP_URL = f'{MCP_URL}/api/mcp'

#: The toolset this run depends on. A rename on the server must fail
#: here loudly instead of leaving the client calling tools that are
#: not there. Kept to the core round trip on purpose: servers evolve
#: (the toolset grew from 18 to 32), and the contract under test is
#: create → follow → result, not the whole catalog.
REQUIRED_TOOLS = {
    'create_task',
    'get_task',
    'list_stores',
    'list_tasks',
    'wait_task',
}

TERMINAL = {'completed', 'failed'}

#: How many times the run will answer a parked question before giving
#: up. A well-behaved report-only task asks once at most; a loop of
#: questions is a different bug and must fail loudly, not be spoon-fed
#: forever.
MAX_ANSWER_ROUNDS = 3

#: What the operator types, in the operator's own words: a report-only
#: ad read, nothing that changes the store.
TASK_DESCRIPTION = (
    '帮我看看过去一周的广告花费，哪些花得多卖得少，'
    '给点优化建议。先别改任何东西，只出报告。'
)


class McpClient:
    """One JSON-RPC document per POST — the endpoint keeps no session."""

    def __init__(self, url: str, token: str):
        self._http = httpx.Client(
            timeout=httpx.Timeout(180.0, connect=15.0),
            headers={'Authorization': f'Bearer {token}'},
        )
        self._url = url
        self._id = 0

    def close(self) -> None:
        self._http.close()

    def rpc(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return its ``result``."""
        self._id += 1
        payload: dict = {'jsonrpc': '2.0', 'id': self._id, 'method': method}
        if params is not None:
            payload['params'] = params
        resp = self._http.post(self._url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        assert 'error' not in body, f'{method} failed: {body.get("error")}'
        return body['result']

    def call_tool(self, tool_name: str, **arguments) -> dict:
        """Call one tool; return ``{'ok', 'data', 'text'}``."""
        result = self.rpc(
            'tools/call', {'name': tool_name, 'arguments': arguments}
        )
        content = result.get('content') or []
        text = content[0].get('text', '') if content else ''
        ok = not result.get('isError')
        data = None
        if ok:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return {'ok': ok, 'data': data, 'text': text}


def _assert_ok(reply: dict, what: str) -> dict:
    """The tool ran and answered with a JSON document."""
    assert reply['ok'], f'{what} errored: {reply["text"][:500]}'
    assert reply['data'] is not None, (
        f'{what} answered without a JSON body: {reply["text"][:500]}'
    )
    return reply['data']


def test_report_only_ad_task_round_trip() -> None:
    """initialize -> tools -> stores -> one task -> follow -> result."""
    if not STORE_REF:
        pytest.fail(
            'VIBE_MCP_STORE is not set — name the store the task should '
            'act on (its id or its name).'
        )
    client = McpClient(MCP_URL, TOKEN)
    try:
        # 1. Handshake, exactly as a client library would.
        init = client.rpc(
            'initialize',
            {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'vibe-seller-live-e2e', 'version': '0'},
            },
        )
        assert init.get('protocolVersion'), f'no protocol: {init}'
        assert init.get('serverInfo'), f'no serverInfo: {init}'

        # 2. The tools this run depends on are all advertised.
        listed = client.rpc('tools/list')
        names = {t['name'] for t in listed.get('tools', [])}
        missing = REQUIRED_TOOLS - names
        assert not missing, f'server does not advertise: {sorted(missing)}'

        # 3. The store to act on: id or name, whichever the operator
        #    had handy.
        stores = _assert_ok(client.call_tool('list_stores'), 'list_stores')[
            'stores'
        ]
        store = next(
            (s for s in stores if STORE_REF in (s.get('id'), s.get('name'))),
            None,
        )
        if store is None:
            pytest.fail(
                f'no store matches VIBE_MCP_STORE={STORE_REF!r}; this '
                f'token sees: {sorted(s.get("name", "?") for s in stores)}'
            )

        # 4. ONE task, report-only, running straight through: a plan
        #    checkpoint would park it waiting for a review this test
        #    refuses to give (that would be one more write).
        created = _assert_ok(
            client.call_tool(
                'create_task',
                description=TASK_DESCRIPTION,
                store_id=store['id'],
                plan_mode=False,
            ),
            'create_task',
        )
        task_id = created['task_id']
        assert created['status'], f'no status in create: {created}'

        # 5. Follow it. wait_task is the blocking "what happened next"
        #    call; get_task is the "what is it doing right now" call.
        deadline = time.monotonic() + BUDGET_SECONDS
        current = created
        answered = 0
        while True:
            assert time.monotonic() < deadline, (
                f'task {task_id} still {current.get("status")} after '
                f'{BUDGET_SECONDS}s; last seen: '
                f'{json.dumps(current, ensure_ascii=False)[:800]}'
            )
            current = _assert_ok(
                client.call_tool('get_task', task_id=task_id), 'get_task'
            )
            status = current.get('status')
            pending = current.get('pending_question')
            if pending:
                assert answered < MAX_ANSWER_ROUNDS, (
                    f'task {task_id} is still asking after '
                    f'{MAX_ANSWER_ROUNDS} answered rounds: {pending}'
                )
                answered += 1
                answers = {}
                for question in pending.get('questions') or []:
                    options = question.get('options') or []
                    answers[question['question']] = (
                        options[0]['label'] if options else '按你推荐的来'
                    )
                _assert_ok(
                    client.call_tool(
                        'answer_question',
                        task_id=task_id,
                        answers=answers,
                        request_id=pending.get('request_id'),
                    ),
                    'answer_question',
                )
                continue
            if status in TERMINAL:
                break
            waited = _assert_ok(
                client.call_tool(
                    'wait_task', task_id=task_id, timeout_seconds=120
                ),
                'wait_task',
            )
            assert waited['task_id'] == task_id
            # The status view must stay honest while work is in flight.
            assert 'status_meaning' in waited, f'no guidance: {waited}'

        # 6. The run finished and produced something to read.
        assert current.get('status') == 'completed', (
            f'task {task_id} ended {current.get("status")}: '
            f'{(current.get("error") or "")[:500]}'
        )
        result = (current.get('result') or '').strip()
        assert result, f'task {task_id} completed with an empty result'

        # 7. The output is reachable the way a client would fetch it —
        #    where the server is new enough to carry the tool at all.
        if 'task_files' in names:
            files = _assert_ok(
                client.call_tool('task_files', task_id=task_id, action='list'),
                'task_files',
            )
            assert isinstance(files.get('files'), list), (
                f'unexpected task_files shape: {json.dumps(files)[:400]}'
            )
    finally:
        client.close()
