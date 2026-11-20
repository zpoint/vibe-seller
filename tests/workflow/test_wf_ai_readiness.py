"""Workflow: ``GET /api/profiles/readiness`` — what the New Task button
asks before opening its form. One system-wide answer: not set up by
default; set up once the one-shot startup job found Claude Code logged in, or
once any other AI was added. Creating a task is never refused.

``claude`` is a stub CLI, so the tests never read the real login.
"""

import stat
import sys

import pytest
from sqlalchemy import func, select

from app.ai import ai_readiness
from app.models.task import Task

pytestmark = pytest.mark.workflow

LOGGED_OUT = 'print(\'{"loggedIn": false, "authMethod": "none"}\'); sys.exit(1)'
LOGGED_IN = 'print(\'{"loggedIn": true, "authMethod": "claude.ai"}\')'


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Install a stub ``claude`` answering with ``body``."""
    monkeypatch.delenv('MOCK_CLI', raising=False)
    ai_readiness.reset()

    def install(body: str):
        script = tmp_path / 'claude'
        script.write_text(f'#!{sys.executable}\nimport sys\n{body}\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(
            ai_readiness, 'resolve_claude_binary', lambda: str(script)
        )

    yield install
    ai_readiness.reset()


async def _ready(client) -> bool:
    r = await client.get('/api/profiles/readiness')
    assert r.status_code == 200, r.text
    return r.json()['ready']


async def test_not_logged_in_is_not_ready(admin_client, cli):
    cli(LOGGED_OUT)
    await ai_readiness.run_startup_check()
    assert await _ready(admin_client) is False


async def test_logged_in_at_startup_is_ready(admin_client, cli):
    cli(LOGGED_IN)
    await ai_readiness.run_startup_check()
    assert await _ready(admin_client) is True


async def test_adding_a_model_ends_the_prompt(admin_client, cli):
    # The guide's path: add a model. No restart, no login needed.
    cli(LOGGED_OUT)
    await ai_readiness.run_startup_check()
    assert await _ready(admin_client) is False
    r = await admin_client.post(
        '/api/profiles',
        json={
            'id': 'deepseek',
            'name': 'DeepSeek',
            'env': {
                'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9/anthropic',
                'ANTHROPIC_AUTH_TOKEN': 'sk-test',
                'ANTHROPIC_MODEL': 'deepseek-chat',
            },
        },
    )
    assert r.status_code == 200, r.text
    assert await _ready(admin_client) is True


async def test_creating_a_task_is_never_refused(
    admin_client, override_async_session, cli
):
    # Only the button asks; the create itself (UI, MCP, API) is as before.
    cli(LOGGED_OUT)
    await ai_readiness.run_startup_check()
    r = await admin_client.post(
        '/api/tasks', json={'title': 'Check this week ad spend'}
    )
    assert r.status_code == 200, r.text
    async with override_async_session() as db:
        n = (await db.execute(select(func.count(Task.id)))).scalar_one()
    assert n == 1
