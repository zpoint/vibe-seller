"""
E2E test: verify agent sandbox — only project MCP servers and
built-in workspace skills are visible to the agent.

Creates a task that asks the agent to list its available MCP
servers and skills as JSON, then asserts the exact expected sets.

Requires: real ``claude`` CLI + LLM API key + running server.
"""

import json
import logging
import re
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    get_messages,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

# What our project provides — update these when adding new
# built-in skills or MCP servers.
# ticktick may not be configured on CI, so it's allowed but not
# required; anything outside the allowed set is a real isolation leak.
REQUIRED_MCP_SERVERS = {'vibe-seller'}
ALLOWED_MCP_SERVERS = {'vibe-seller', 'ticktick'}
EXPECTED_SKILLS = {'browser-use', 'amazon-invoice'}

# MCP servers injected by the CI model-provider GATEWAY (the GLM /
# MiniMax proxy used so e2e doesn't need a real Claude key), NOT by our
# project. They surface as `mcp__4_5v_mcp__*` / `mcp__web_reader__*`
# tools the upstream proxy attaches to every session — our
# `--strict-mcp-config` cannot remove provider-side tools. They are an
# environment artifact, so we ignore them when checking for leaks.
GATEWAY_INJECTED_SERVERS = {'4.5v_mcp', 'web_reader'}


@pytest.fixture(scope='module')
def api_client():
    client = httpx.Client(timeout=30)
    login(client)
    yield client
    client.close()


@pytest.fixture(scope='module')
def sandbox_store(api_client: httpx.Client) -> dict:
    ts = int(time.time())
    return create_store(
        api_client,
        f'sandbox-test-{ts}',
        browser_backend='chrome',
    )


def _extract_json(text: str) -> dict | None:
    """Regex-extract first JSON object from text.

    Handles markdown code blocks, commentary before/after.
    """
    # Try to find ```json ... ``` block first
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Fall back to first { ... } in text
    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Try nested braces (for arrays inside)
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


class TestAgentSandbox:
    """Verify the agent only sees project MCP servers and skills."""

    def test_agent_sees_only_project_mcp_and_skills(
        self,
        api_client: httpx.Client,
        sandbox_store: dict,
    ):
        """Ask the agent to list its MCP servers and skills.

        Assert the exact expected sets — nothing more, nothing
        less than what our project provides.
        """
        task = create_task(
            api_client,
            title='List MCP servers and skills',
            store_id=sandbox_store['id'],
            description=(
                'If you have a WaitForMcpServers tool, call it first and '
                'wait until all MCP servers report connected — they '
                'attach asynchronously and may still be "pending" at '
                'startup. If you do NOT have that tool, just proceed — '
                'do not fail or stop, you can still answer. '
                'THEN list all your available MCP server names and all '
                'your available skill names. You can tell which MCP '
                'servers you have from your own tools: a tool named '
                '"mcp__<server>__..." means the "<server>" MCP server is '
                'available (e.g. "mcp__vibe-seller__..." → "vibe-seller"). '
                'Output ONLY a JSON object like this: '
                '{"mcp_servers": ["name1"], "skills": ["name2"]}. '
                'No other text. No explanation.'
            ),
        )
        task_id = task['id']
        logger.info('Created sandbox task %s', task_id[:8])

        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={'completed'},
            fail_statuses={'failed'},
            timeout=PIPELINE_TIMEOUT,
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error", "unknown")}'
        )

        # Extract JSON from task messages or result
        messages = get_messages(api_client, task_id)
        all_text = ' '.join(
            m.get('content', '')
            for m in messages
            if m.get('role') in ('assistant', 'agent')
        )
        # Also check the task result field
        task_result = result.get('result', '') or ''
        all_text = f'{all_text} {task_result}'

        data = _extract_json(all_text)
        assert data is not None, (
            f'Could not extract JSON from agent output: {all_text[:500]}'
        )

        raw_mcp = set(data.get('mcp_servers', []))
        skills = set(data.get('skills', []))

        logger.info('MCP servers (raw): %s', raw_mcp)
        logger.info('Skills: %s', skills)

        # Drop the CI gateway's own injected servers — they're an
        # environment artifact, not part of our project's MCP config.
        # Normalize separators ('.' and '-' → '_') on BOTH sides so the
        # gateway server matches however the model renders it: `4.5v_mcp`,
        # `4.5v-mcp`, `mcp__4_5v_mcp__analyze_image` all reduce to the
        # `4_5v_mcp` token. (The REQUIRED/leaked checks below already
        # normalize '-'; this matcher must too, or a dash-rendered gateway
        # name falsely counts as a leak.)
        def _norm(x: str) -> str:
            return x.lower().replace('.', '_').replace('-', '_')

        def _is_gateway(name: str) -> bool:
            n = _norm(name)
            return any(_norm(g) in n for g in GATEWAY_INJECTED_SERVERS)

        project_mcp = {m for m in raw_mcp if not _is_gateway(m)}
        logger.info('MCP servers (project, gateway-filtered): %s', project_mcp)

        # REQUIRED: the project's vibe-seller MCP must be connected and
        # visible to the agent (the WaitForMcpServers step ensures it
        # finished its async connect before the agent reported).
        # LLMs may report a tool name (vibe_seller_list_stores) instead
        # of the server name (vibe-seller) — match by substring + _/-.
        all_mcp_text = ' '.join(project_mcp).lower()
        for required in REQUIRED_MCP_SERVERS:
            variants = {required, required.replace('-', '_')}
            found = any(v in all_mcp_text for v in variants)
            assert found, (
                f'Required MCP server {required!r} not visible to the '
                f'agent. Reported (raw): {raw_mcp}. If this is empty or '
                'only gateway servers, either the vibe-seller MCP failed '
                'to attach (check the init event mcp_servers) or the '
                'model did not enumerate its mcp__<server>__ tools.'
            )

        # ISOLATION: no project-level MCP server may leak in beyond the
        # allowed set (gateway artifacts already filtered out above).
        leaked = {
            m
            for m in project_mcp
            if not any(
                a in m.lower() or a.replace('-', '_') in m.lower()
                for a in ALLOWED_MCP_SERVERS
            )
        }
        assert not leaked, (
            f'Unexpected MCP servers leaked into the agent sandbox: '
            f'{leaked} (allowed: {ALLOWED_MCP_SERVERS}, '
            f'gateway-ignored: {GATEWAY_INJECTED_SERVERS})'
        )

        assert EXPECTED_SKILLS <= skills, (
            f'Skills missing: expected {EXPECTED_SKILLS} '
            f'to be subset of {skills}'
        )
