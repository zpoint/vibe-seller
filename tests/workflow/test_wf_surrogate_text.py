"""Agent text carrying a lone surrogate must persist, not 500.

Claude's stream-json occasionally emits an unpaired surrogate
(U+D800-U+DFFF). JSON escapes them losslessly, so the MCP request body
is valid ASCII on the wire and ``json.loads`` reconstructs a ``str``
Python cannot encode to UTF-8. Every persistence path then raised
``surrogates not allowed`` — the agent saw an opaque tool error and
retried with the same text.

The guard lives at the encode (``SafeText`` for DB columns, a sanitize
inside the managers that own file writes), so these tests drive the real
HTTP endpoints an agent uses and assert the write lands. They are the
end-to-end check for ``tests/unit/test_text_utils.py``.
"""

import asyncio

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

# A body an MCP client would produce: the surrogate arrives as a JSON
# \u escape, exactly as `json.dumps(..., ensure_ascii=True)` writes it.
# Sent as raw content so the test client cannot fail on it first.
RESULT_BODY = (
    '{"result": "Weekly report \\udcad done.\\n\\n'
    '| Campaign | Spend | ROAS |\\n|---|---|---|\\n'
    '| widget-006 | 10 | 4.2 |\\n"}'
)
JSON_HEADERS = {'content-type': 'application/json'}


async def _running_task(admin_client, install_fake_agent, name):
    """A task whose fake agent stays alive, so MCP endpoints see RUNNING."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='streamed narration', gate=gate
    )
    r = await admin_client.post('/api/stores', json={'name': name})
    store_id = r.json()['id']
    r = await admin_client.post(
        '/api/tasks', json={'title': 'surrogate', 'store_id': store_id}
    )
    task_id = r.json()['id']
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    assert data['status'] == 'running'
    return task_id, gate


class TestSubmissionWithLoneSurrogate:
    async def test_set_result_persists_instead_of_crashing(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Surrogate Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            content=RESULT_BODY,
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200, r.text

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        stored = data['submitted_result']
        # The surrogate is gone, the report around it is intact.
        assert '\udcad' not in stored
        assert '�' in stored
        assert 'Weekly report' in stored
        stored.encode('utf-8')  # the thing that used to raise

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_set_error_persists_instead_of_crashing(
        self, admin_client, install_fake_agent
    ):
        """``vibe_seller_set_task_error`` is the same encode, same crash."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Surrogate Error Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            content='{"error": "login page \\udcad never loaded"}',
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200, r.text

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert '\udcad' not in (final.get('error') or '')


class TestWorkspaceWriteWithLoneSurrogate:
    async def test_write_file_persists_instead_of_crashing(self, admin_client):
        r = await admin_client.put(
            '/api/workspace/file?path=knowledge/surrogate.md',
            content='{"content": "# Notes \\udcad\\n\\nbody\\n"}',
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200, r.text

        r = await admin_client.get(
            '/api/workspace/file?path=knowledge/surrogate.md'
        )
        assert r.status_code == 200
        content = r.json()['content']
        assert '\udcad' not in content
        assert 'body' in content

    async def test_save_skill_persists_instead_of_crashing(self, admin_client):
        """``vibe_seller_save_skill`` writes SKILL.md with the same encode."""
        skill_md = (
            '---\\nname: surrogate-probe\\ndescription: probe \\udcad\\n'
            '---\\n\\n# surrogate-probe\\n'
        )
        r = await admin_client.put(
            '/api/workspace/skills/surrogate-probe',
            content='{"skill_md": "' + skill_md + '"}',
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200, r.text
