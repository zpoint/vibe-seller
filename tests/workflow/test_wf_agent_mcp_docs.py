"""The curl examples in ``docs/agents.md`` are the ones the server
accepts.

A reader of that page copy-pastes the example. If the JSON-RPC
payload in the doc drifts from what the endpoint serves, the example
fails in the reader's hands and the doc is the only place it shows —
the server never sees the doc. So the payloads are extracted from the
doc and replayed against the real endpoint.

The ``Authorization`` header is not replayed: it carries a placeholder
token (``vst_REPLACE_WITH_YOUR_TOKEN``) that the server rightly
refuses. Auth is covered by the token tests; what this file checks is
the request shape the doc teaches — the JSON content type and the
body.
"""

import json
from pathlib import Path
import re

import pytest

from app.agent_mcp.registry import tool_definitions

pytestmark = pytest.mark.workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = REPO_ROOT / 'docs/agents.md'
MCP_PATH = '/api/mcp'


def _curl_payloads() -> list[dict]:
    """Every JSON-RPC body in the doc's curl examples."""
    text = AGENTS_MD.read_text(encoding='utf-8')
    payloads: list[dict] = []
    # DOTALL: a documented body may wrap across lines (the tools/call
    # example does); without it that example was silently skipped.
    for match in re.finditer(
        r"-d\s+'(\{.*?\})'\s*$", text, re.MULTILINE | re.DOTALL
    ):
        payloads.append(json.loads(match.group(1)))
    return payloads


def test_the_doc_has_curl_examples():
    """Every curl example in the doc is actually replayed.

    Pins the extractor itself: a body the regex fails to pick up would
    otherwise leave the replay test green while testing nothing.
    """
    methods = {p['method'] for p in _curl_payloads()}
    assert {'tools/list', 'tools/call'} <= methods, methods


async def test_documented_curl_payloads_are_accepted(
    admin_client, install_fake_agent
):
    """Each documented body is a valid JSON-RPC call."""
    payloads = _curl_payloads()
    assert payloads, 'no curl payload found in docs/agents.md'
    for payload in payloads:
        r = await admin_client.post(
            MCP_PATH,
            content=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
        )
        assert r.status_code == 200, (payload, r.text)
        body = r.json()
        assert 'error' not in body, (payload, body)
        assert 'result' in body, (payload, body)


async def test_the_documented_content_type_is_required(
    admin_client, install_fake_agent
):
    """The doc says the content type is mandatory — so it is.

    A JSON-RPC body sent as ``text/plain`` is refused with 415; the
    doc tells the reader to set the header, and this keeps that true.
    """
    payload = _curl_payloads()[0]
    r = await admin_client.post(
        MCP_PATH,
        content=json.dumps(payload).encode(),
        headers={'Content-Type': 'text/plain'},
    )
    assert r.status_code == 415, r.text
    assert 'application/json' in r.json()['error']['message']


def test_the_doc_lists_every_served_tool():
    """The tool table names every tool the endpoint serves.

    The table is the page's index; a tool missing from it is a tool
    the reader never learns about.
    """
    text = AGENTS_MD.read_text(encoding='utf-8')
    missing = sorted(
        definition['name']
        for definition in tool_definitions()
        if f'`{definition["name"]}`' not in text
    )
    assert not missing, f'docs/agents.md does not mention: {missing}'
