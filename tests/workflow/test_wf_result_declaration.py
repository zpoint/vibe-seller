"""A declared result file, or plain text — never a guess from the text.

``set_task_result`` used to read the SHAPE of ``result`` to decide
whether the agent meant a file: a value with no spaces ending in a
document extension, or starting with ``./`` or ``/``, that resolved to
nothing was refused with a 400 — unlimited rounds, because a shape guess
has no "what is still missing" to put a round limit on. A task whose
answer genuinely WAS ``report.md`` (a filename as the deliverable, e.g.
"which file did you write?") or ``/2025-Q3`` could never finish.

The mode is now declared: ``result_file`` says "my deliverable is this
file" (missing file → refused, because the agent already stated its
intent), and ``result`` is always text. The legacy call form every skill
doc shows — a real workspace path passed as ``result`` — still resolves,
so no run in flight changes behaviour.
"""

import asyncio

import pytest

from app.config import VIBE_SELLER_DIR
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

REPORT = '# Audit\n\n| Campaign | ROAS |\n|---|---|\n| widget-006 | 4.2 |\n'


async def _running_task(admin_client, install_fake_agent, name):
    """A task whose fake agent stays alive, so /result sees RUNNING."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='streamed narration from the agent',
        gate=gate,
    )
    r = await admin_client.post('/api/stores', json={'name': name})
    store_id = r.json()['id']
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'result declaration', 'store_id': store_id},
    )
    task_id = r.json()['id']
    data = {}
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    assert data['status'] == 'running', data
    return task_id, gate


def _task_dir(task_id: str):
    d = VIBE_SELLER_DIR / 'tasks' / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestTextIsNeverAPointer:
    async def test_filename_answer_is_accepted_as_text(
        self, admin_client, install_fake_agent
    ):
        """THE REGRESSION: the answer itself was a plausible filename."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store A'
        )
        # Precondition: no file of that name exists in the workspace, so
        # this is text, not a pointer that failed to resolve.
        assert not (_task_dir(task_id) / 'report.md').exists()

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': 'report.md'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['resolved_from_file'] is False
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == 'report.md'

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_slash_prefixed_answer_is_accepted_as_text(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store B'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': '/2025-Q3'}
        )
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == '/2025-Q3'

        gate.set()
        await wait_for_task(admin_client, task_id)


class TestDeclaredResultFile:
    async def test_declared_file_is_read_into_the_result(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store C'
        )
        (_task_dir(task_id) / 'AD_AUDIT_2099-01-01.md').write_text(
            REPORT, encoding='utf-8'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result_file': './AD_AUDIT_2099-01-01.md'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['resolved_from_file'] is True
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == REPORT

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_declared_missing_file_is_refused_once_with_a_fix(
        self, admin_client, install_fake_agent
    ):
        """The declaration is enforced — but writing the file fixes it."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store D'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result_file': 'gone.md'}
        )
        assert r.status_code == 400, r.text
        assert 'result_file' in r.text

        # The agent does the one thing it claimed to have done…
        (_task_dir(task_id) / 'gone.md').write_text(REPORT, encoding='utf-8')
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result_file': 'gone.md'}
        )
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == REPORT

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_declared_file_outside_the_workspace_is_refused(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store E'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result_file': './../../etc/passwd'},
        )
        assert r.status_code == 400, r.text

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_sending_neither_deliverable_is_refused(
        self, admin_client, install_fake_agent
    ):
        """400, naming both fields — the endpoint's own contract error,
        not a pydantic 422: the request model stays constructible without
        a deliverable so partial bodies stay testable."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store F'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'incomplete': ['x']}
        )
        assert r.status_code == 400, r.text
        assert 'exactly one' in r.text

        gate.set()
        await wait_for_task(admin_client, task_id)


class TestLegacyPointerCallStillWorks:
    async def test_real_workspace_path_as_result_still_resolves(
        self, admin_client, install_fake_agent
    ):
        """Every skill doc shows this call form; it must keep working."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Decl Store G'
        )
        (_task_dir(task_id) / 'REVIEW_COLLECT_2099-01-01.md').write_text(
            REPORT, encoding='utf-8'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './REVIEW_COLLECT_2099-01-01.md'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['resolved_from_file'] is True
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == REPORT

        gate.set()
        await wait_for_task(admin_client, task_id)
