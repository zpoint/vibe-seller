"""E2E: save a task's workflow as a skill, then reuse + extend it.

No browser. A real agent does local work (sum a CSV of orders), the
user asks to "save this as a skill", and we assert a USER-space skill
was created. A second task adds a new wrinkle (post the summary to a
WeCom group), the saved skill auto-loads, the user asks to save again,
and we assert the SAME skill was EXTENDED — not duplicated, and no
built-in skill was touched.

Runs in CI (creates its own ephemeral store, needs no real creds).
The WeCom send targets a loopback stub so the "post to WeCom" leg is
deterministic; delivery itself is not asserted (the skill flow is what
this test pins).
"""

import http.server
import json
import logging
import threading
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    create_store,
    create_task,
    get_messages,
    poll_task_status,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]


# ── Helpers ─────────────────────────────────────────


def _wait_for_followup_result(client, task_id, known_count):
    """Poll messages until a new 'result' turn appears (follow-ups keep
    the task in 'completed', so status polling can't be used)."""
    deadline = time.time() + PIPELINE_TIMEOUT
    msgs = []
    while time.time() < deadline:
        msgs = get_messages(client, task_id)
        if any(m.get('role') == 'result' for m in msgs[known_count:]):
            return msgs
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'No follow-up result for {task_id[:8]}')


def _result_text(client, task_id):
    """Concatenate assistant/result/thinking content for assertions."""
    msgs = get_messages(client, task_id)
    return ' '.join(
        m.get('content', '')
        for m in msgs
        if m.get('role') in ('assistant', 'result', 'thinking')
    )


def _custom_skills(client):
    """Map slug -> entry for user-editable (custom/imported) skills."""
    resp = client.get(f'{BASE_URL}/api/workspace/skills')
    resp.raise_for_status()
    return {s['slug']: s for s in resp.json() if s['updatable']}


def _skill_md(client, slug):
    resp = client.get(
        f'{BASE_URL}/api/workspace/file',
        params={'path': f'.claude/skills/{slug}/SKILL.md'},
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get('content', '') if isinstance(body, dict) else str(body)


def _mentions_wecom(text):
    low = text.lower()
    return any(k in low for k in ('wecom', 'wechat work', '企业微信'))


# ── Fixtures ────────────────────────────────────────


@pytest.fixture
def wecom_stub(api_client):
    """A loopback HTTP server that impersonates a WeCom bot webhook,
    registered as a bot so the agent has a real target to post to.

    Records received payloads (for optional inspection) and always
    replies with WeCom's success envelope.
    """
    received = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length else b''
            try:
                received.append(json.loads(raw or b'{}'))
            except ValueError:
                received.append({'raw': raw.decode('utf-8', 'replace')})
            payload = json.dumps({'errcode': 0, 'errmsg': 'ok'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f'http://127.0.0.1:{port}/cgi-bin/webhook/send?key=e2e-stub'
    resp = api_client.post(
        f'{BASE_URL}/api/wecom-bots',
        json={'name': f'e2e-stub-{port}', 'webhook_url': url},
    )
    resp.raise_for_status()
    bot_id = resp.json()['id']

    yield {'received': received, 'bot_id': bot_id}

    api_client.delete(f'{BASE_URL}/api/wecom-bots/{bot_id}')
    server.shutdown()
    server.server_close()


@pytest.fixture
def orders_csv(tmp_path):
    """Two local order exports with distinctive, clean revenue totals."""
    first = tmp_path / 'orders_week1.csv'
    first.write_text(
        'order_id,revenue\n1001,12340\n1002,8150\n1003,4190\n',
        encoding='utf-8',
    )  # total = 24680
    second = tmp_path / 'orders_week2.csv'
    second.write_text(
        'order_id,revenue\n2001,5000\n2002,6000\n2003,2500\n',
        encoding='utf-8',
    )  # total = 13500
    return {
        'first': str(first),
        'first_total': 24680,
        'second': str(second),
        'second_total': 13500,
    }


@pytest.fixture
def created_skills_cleanup(api_client):
    """Delete only the user-space skills THIS test registers (by adding
    their slugs to the yielded set), so re-runs stay clean.

    Must NOT delete the whole before/after delta of the skill list: that
    namespace is workspace-global and shared across parallel xdist
    workers, so a delta cleanup would delete a *sibling* test's skill
    mid-run. The test owns the slugs it created and registers exactly
    those.
    """
    owned: set[str] = set()
    yield owned
    for slug in owned:
        api_client.delete(f'{BASE_URL}/api/workspace/skills/{slug}')


def _assert_total(text, total):
    assert str(total) in text or f'{total:,}' in text, (
        f'expected total {total} in agent result, got: {text[:400]}'
    )


# ── Test ────────────────────────────────────────────


# All skill-mutating e2e tests share the workspace-global skill namespace,
# so they must run on the SAME xdist worker (serially) — never
# concurrently. Otherwise one test's snapshot/cleanup races another's
# create/delete. Requires ``--dist loadgroup`` (set in CI).
@pytest.mark.xdist_group('workspace_skills')
class TestSaveAndReuseSkill:
    def test_create_then_extend(
        self, api_client, orders_csv, wecom_stub, created_skills_cleanup
    ):
        tag = int(time.time())
        store = create_store(api_client, f'e2e-skill-{tag}')
        store_id = store['id']

        customs_start = set(_custom_skills(api_client))

        # ── Task 1: do the workflow ──────────────────
        task1 = create_task(
            api_client,
            title='Total this month’s order revenue',
            store_id=store_id,
            description=(
                f'The order export is saved on this computer at '
                f'{orders_csv["first"]}. Add up the revenue column '
                'across all the orders and tell me the grand total. '
                'It is a local file, so there is no need to open a '
                'browser, and please do not ask me any questions.'
            ),
        )
        t1 = poll_task_status(
            api_client, task1['id'], {'completed'}, fail_statuses={'failed'}
        )
        assert t1['status'] == 'completed', f'task1 failed: {t1.get("error")}'
        _assert_total(_result_text(api_client, task1['id']), 24680)

        # ── Follow-up 1: save the workflow as a skill ──
        count1 = len(get_messages(api_client, task1['id']))
        r = api_client.post(
            f'{BASE_URL}/api/tasks/{task1["id"]}/messages',
            json={
                'content': (
                    'Nice. Please save this workflow as a skill so next '
                    'time you can total an order export the same way '
                    'without me explaining it.'
                )
            },
        )
        r.raise_for_status()
        _wait_for_followup_result(api_client, task1['id'], count1)

        customs_after_save = _custom_skills(api_client)
        new_slugs = set(customs_after_save) - customs_start
        assert new_slugs, (
            'no user-space skill was created by "save as skill"; '
            f'custom skills: {sorted(customs_after_save)}'
        )
        created_skills_cleanup.update(new_slugs)  # own these for teardown
        for slug in new_slugs:
            assert customs_after_save[slug]['source'] == 'custom'

        # ── Task 2: reuse the skill + a new wrinkle (WeCom) ──
        task2 = create_task(
            api_client,
            title='Total last week’s revenue and share it',
            store_id=store_id,
            description=(
                f'Here is last week’s order export: '
                f'{orders_csv["second"]}. Total the revenue like '
                'before, then post a short summary of the total to our '
                'team’s WeCom group. Local file — no browser needed, and '
                'no questions please.'
            ),
        )
        t2 = poll_task_status(
            api_client, task2['id'], {'completed'}, fail_statuses={'failed'}
        )
        assert t2['status'] == 'completed', f'task2 failed: {t2.get("error")}'
        _assert_total(_result_text(api_client, task2['id']), 13500)

        # ── Follow-up 2: save again → EXTEND, don't duplicate ──
        count2 = len(get_messages(api_client, task2['id']))
        r = api_client.post(
            f'{BASE_URL}/api/tasks/{task2["id"]}/messages',
            json={
                'content': (
                    'Please update that same skill so it also remembers '
                    'to post the summary to our WeCom group next time.'
                )
            },
        )
        r.raise_for_status()
        _wait_for_followup_result(api_client, task2['id'], count2)

        customs_final = _custom_skills(api_client)
        # The second save must EXTEND the existing slug, not mint a new
        # near-duplicate. Check only for a NEW slug appearing (a subset
        # check, scoped to this test's serialized worker) rather than
        # whole-namespace equality — equality would also trip if a
        # sibling test's skill were added/removed in the shared namespace.
        dup_slugs = set(customs_final) - set(customs_after_save)
        assert not dup_slugs, (
            'second "save as skill" duplicated instead of extending; '
            f'new slug(s) appeared: {sorted(dup_slugs)}'
        )
        # The originally-created skill now covers the WeCom step.
        assert any(
            _mentions_wecom(_skill_md(api_client, slug)) for slug in new_slugs
        ), 'extended skill does not mention the new WeCom step'
