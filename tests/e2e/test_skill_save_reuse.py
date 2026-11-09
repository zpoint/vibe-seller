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
this test pins). The server must be started with
``VIBE_ALLOW_LOOPBACK_WEBHOOKS=1`` (CI and docker/e2e do) to accept
the stub's loopback webhook URL — production never accepts loopback
webhooks, and without the flag this module SKIPS (see the wecom_stub
fixture).
"""

import http.server
import json
import logging
import re
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


@pytest.fixture(scope='module', autouse=True)
def reflection_server(tmp_path_factory):
    """Skill saving is reflection's job (knowledge/skill creation runs
    in the Stop-hook reflection turn), so this module owns a server
    with reflection ON — the shared CI e2e server runs with it off.
    Same hook, same prompts, same assertions. The loopback webhook
    stub this module posts to is the same opt-in the shared CI server
    carries.
    """
    from tests.e2e.own_server import owned_e2e_server

    with owned_e2e_server(
        tmp_path_factory.mktemp('reflection-home'),
        env={'VIBE_ALLOW_LOOPBACK_WEBHOOKS': '1'},
    ) as s:
        yield s


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


# Ownership is attributed by TASK, not by content. The namespace is
# workspace-global (every xdist worker shares it), so a raw namespace
# delta can contain skills this test did not create — a sibling test's
# install, or a skill any agent's post-task reflection minted mid-window
# (observed in CI: a `wecom-post` skill). But the server records every
# skill write an agent makes as a ``tool_use`` message on ITS task,
# carrying the full tool input (app/ai/claude_backend_stream.py):
# ``vibe_seller_save_skill`` has the slug in ``input.slug``, and any
# file write under the skills namespace shows the path. A skill belongs
# to this test iff one of THIS test's tasks wrote it — a fact recorded
# by the server, not a guess about the skill's wording (keyword-matching
# a sibling's order/export/revenue skill would wrongly adopt it, and
# cleanup would then delete it mid-run).
_SKILL_PATH_RE = re.compile(r'\.claude/skills/([a-z0-9][a-z0-9-]*)')


def _skill_slugs_in_messages(messages) -> set:
    """Slugs whose user-skill writes appear in a task's message log."""
    slugs: set[str] = set()
    for m in messages:
        if m.get('role') != 'tool_use':
            continue
        try:
            payload = json.loads(m.get('content') or '')
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        tool = str(payload.get('tool') or '')
        inp = payload.get('input')
        if not isinstance(inp, dict):
            continue
        if tool.endswith('vibe_seller_save_skill') and isinstance(
            inp.get('slug'), str
        ):
            slugs.add(inp['slug'])
        for value in inp.values():
            if isinstance(value, str):
                slugs.update(_SKILL_PATH_RE.findall(value))
    return slugs


def _skills_saved_by(client, *task_ids) -> set:
    """User-skill slugs written by the given tasks' agents."""
    slugs: set[str] = set()
    for task_id in task_ids:
        slugs |= _skill_slugs_in_messages(get_messages(client, task_id))
    return slugs


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
        f'{BASE_URL}/api/chat-bots',
        json={'name': f'e2e-stub-{port}', 'webhook_url': url},
    )
    if resp.status_code == 400 and 'Unsupported webhook URL' in resp.text:
        # Not a failure: this server (correctly) refuses loopback
        # webhook URLs — production never accepts them. Only a server
        # started with VIBE_ALLOW_LOOPBACK_WEBHOOKS=1 (CI / a local
        # e2e run) can register the stub.
        server.shutdown()
        server.server_close()
        pytest.skip(
            'server rejects the loopback stub webhook — restart it '
            'with VIBE_ALLOW_LOOPBACK_WEBHOOKS=1 to run this test'
        )
    resp.raise_for_status()
    bot_id = resp.json()['id']

    yield {'received': received, 'bot_id': bot_id}

    api_client.delete(f'{BASE_URL}/api/chat-bots/{bot_id}')
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

    The registry is filled exclusively with slugs attributed to this
    test's own tasks (``_skills_saved_by``) — never with a raw namespace
    delta: the namespace is workspace-global and shared across parallel
    xdist workers, so a delta would also capture a sibling test's skill
    and deleting it mid-run is exactly the interference that once
    stalled the follow-up review test.
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


# Runs concurrently with the other skill-mutating e2e tests: everything
# this test does to the shared namespace is task-attributed — it claims
# only skills its own tasks wrote, and cleanup deletes exactly those
# (see created_skills_cleanup / _skills_saved_by).
class TestSaveAndReuseSkill:
    @pytest.mark.long_budget(547)  # measured p50 over CI 201j + baseline
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
        # Attributed to task1, not a raw delta: a sibling worker's skill
        # can appear in the namespace inside this window and must be
        # neither owned here (cleanup would delete it mid-run) nor fail
        # the assertions below. The delta only decides created-vs-
        # pre-existing; task attribution decides WHOSE it is.
        new_slugs = _skills_saved_by(api_client, task1['id']) & (
            set(customs_after_save) - customs_start
        )
        assert new_slugs, (
            'no user-space skill was created by "save as skill" on this '
            f'task; custom skills: {sorted(customs_after_save)}'
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
        # Snapshot HERE, not after the first save. Task 2's own turn may
        # legitimately save a NEW capability while it works (observed in
        # CI: its post-task reflection saved a `wecom-post` skill for the
        # posting step it had just learned) -- that is not this request
        # duplicating anything. What this request must not do is answer
        # "update that same skill" by minting another slug.
        customs_before_update = _custom_skills(api_client)
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
        # near-duplicate. Scope the check to skills THIS test's tasks
        # wrote: the raw namespace delta would also flag a skill any
        # sibling test (or its agent's reflection) minted inside the
        # window, which is not this request duplicating anything.
        dup_slugs = _skills_saved_by(api_client, task1['id'], task2['id']) & (
            set(customs_final) - set(customs_before_update)
        )
        assert not dup_slugs, (
            'second "save as skill" duplicated instead of extending; '
            f'new slug(s) appeared: {sorted(dup_slugs)}'
        )
        # The originally-created skill now covers the WeCom step.
        assert any(
            _mentions_wecom(_skill_md(api_client, slug)) for slug in new_slugs
        ), 'extended skill does not mention the new WeCom step'
