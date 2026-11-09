"""A minimal MCP client for e2e tests (JSON-RPC over HTTP)."""

import json

import httpx

from tests.e2e.conftest import BASE_URL

MCP_URL = f'{BASE_URL}/api/mcp'


class McpClient:
    """A minimal MCP client: JSON-RPC over HTTP, one document per call.

    ``/api/mcp`` keeps no session and pushes no events, so there is
    nothing to keep alive between calls and no client library to depend
    on — a POST per request is the whole protocol.
    """

    def __init__(self, token: str | None = None, timeout: float = 180.0):
        self._client = httpx.Client(timeout=timeout)
        if token:
            self._client.headers['Authorization'] = f'Bearer {token}'
        self._id = 0

    def close(self) -> None:
        self._client.close()

    def post(self, payload: dict) -> httpx.Response:
        """POST one JSON-RPC message, verbatim (no status check)."""
        return self._client.post(MCP_URL, json=payload)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return its ``result``."""
        self._id += 1
        payload = {'jsonrpc': '2.0', 'id': self._id, 'method': method}
        if params is not None:
            payload['params'] = params
        resp = self.post(payload)
        resp.raise_for_status()
        body = resp.json()
        assert 'error' not in body, f'{method} failed: {body.get("error")}'
        return body['result']

    def notify(self, method: str, params: dict | None = None) -> int:
        """Send a notification — no id, so no response. Returns the status."""
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        return self.post(payload).status_code

    def call_tool(self, tool_name: str, **arguments) -> dict:
        """Call one tool.

        Returns ``{'ok', 'data', 'text', 'images'}``: a tool that ran
        and failed answers with ``isError`` and a plain-text reason; a
        tool that succeeded answers with a JSON document in the text
        content, plus any MCP ``image`` content blocks it showed.

        The tool name is positional on purpose: several tools take an
        argument literally called ``name`` (manage_store,
        manage_skill, …), which must survive as a keyword here.
        """
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
        images = [c for c in content if c.get('type') == 'image']
        return {'ok': ok, 'data': data, 'text': text, 'images': images}

    def call_tool_rejected(self, tool_name: str, **arguments) -> dict:
        """Call a tool whose arguments the dispatcher must refuse.

        Argument validation against the advertised schema (an off-enum
        ``action``, a wrong type) happens BEFORE the tool runs and answers
        with a JSON-RPC ``INVALID_PARAMS`` error, not a tool-level
        ``isError``. Returns that ``error`` object.
        """
        self._id += 1
        resp = self.post({
            'jsonrpc': '2.0',
            'id': self._id,
            'method': 'tools/call',
            'params': {'name': tool_name, 'arguments': arguments},
        })
        resp.raise_for_status()
        body = resp.json()
        assert 'error' in body, f'expected a refusal, got {body}'
        return body['error']
