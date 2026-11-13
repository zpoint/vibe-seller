"""E2E: a stale ads client refreshes on its next call, not tomorrow.

The client used to learn about a new skill from the ``ads_skill`` job's
daily check alone, so a document the service had already corrected kept
its agents guessing at paths for another day. The service now names its
skill version on every answer it gives (vibe-ads-server PR #19), and
this module asks the two questions that matter about that:

1. **Does one ordinary call turn into a refresh?** No model, no agent —
   the mechanism on its own: the header is read, the job is triggered
   with a reason of its own, the bundle is checked and pulled, the bytes
   on disk change, the state row moves, and the next call downloads
   nothing.
2. **Does a real agent end up working from the new document?** Task one
   runs on the stale skill and its first call is what notices; task two,
   launched after the refresh, answers a question only the new skill
   knows how to ask.

**Ground truth, never prose.** The two bundles point at different
answers: the fake's ``/facts/rollup`` says 123.45, and only the newer
document names ``/facts/e2e-pulse``, which answers 424.24. A task result
carrying one of those figures, and the service's own log of the call
that produced it under this store's key, are the evidence. What an agent
writes around a figure is never asserted.

**This module owns its server and its fake service**, like
``test_ads_mixed_fanout``: ``POST /api/ads/stores/sync`` rewrites the
ads binding of every store row on the server it hits, and the bundle a
fake publishes is state of that service. On the shared server this test
would plan against other workers' stores, and one module's publish would
refresh another module's client.

Everything here is invented: placeholder store names, round figures,
``e2e-*`` version tags.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

import pytest

from app import ads_skill
from tests.e2e import fake_ads_service
from tests.e2e.e2e_helpers import (
    create_store,
    create_task,
    poll_task_status,
)
from tests.e2e.own_server import (
    SYNC_SWITCHES_ON,
    owned_agent_stack,
    pick_free_port,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]

TERMINAL = {'completed', 'failed', 'cancelled'}
RUN_RE = re.compile(r'sync:\S+ reason=\S+ status=\S+')

# Set by the module fixture (own_stack): this module's own server and
# the fake ads service it was booted against.
BASE_URL = ''
ADS_URL = ''

#: While this module runs, no job ticks on its own: every ``ads_skill``
#: line in the log is one this test caused, which is what lets it count
#: lines before and after a call and read the difference as news.
LONG = (
    'skills=3600,knowledge=3600,presets=3600,ads_skill=3600,'
    'email=3600,daemon_reaper=3600'
)


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """A server of this module's own, pointed at a fake it owns too."""
    global BASE_URL, ADS_URL
    port = pick_free_port()
    ADS_URL = f'http://127.0.0.1:{port}'
    with owned_agent_stack(
        tmp_path_factory,
        'ads-skill-refresh',
        env={
            'VIBE_ADS_SERVICE_URL': ADS_URL,
            # A released client pulls from outside; this test is about
            # the pull, so the switches are the product's, not CI's.
            **SYNC_SWITCHES_ON,
            'VIBE_SYNC_CADENCE_OVERRIDE': LONG,
            'VIBE_SYNC_JITTER': '0',
        },
    ) as server:
        BASE_URL = server.base_url
        yield server


@pytest.fixture(scope='module')
def service(own_stack):
    """The fake ads service THIS module's server calls."""
    srv = fake_ads_service.serve(port=urlparse(ADS_URL).port)
    yield srv
    srv.shutdown()
    srv.server_close()


# ── reading what happened ─────────────────────────────────────────


def skill_file(server) -> Path:
    """The installed document, on this server's own home."""
    return (
        Path(server.home)
        / '.claude'
        / 'skills'
        / ads_skill.SKILL_NAME
        / 'SKILL.md'
    )


def run_lines(server, name: str) -> list[str]:
    lines = [
        ln
        for ln in server.log.read_text(errors='replace').splitlines()
        if RUN_RE.search(ln)
    ]
    return [ln for ln in lines if f'sync:{name} ' in ln]


def job_row(client, name: str) -> dict:
    body = client.get(f'{BASE_URL}/api/sync/status', timeout=30).json()
    return next(job for job in body['jobs'] if job['name'] == name)


def wait_until(what: str, predicate, timeout: float = 120) -> None:
    """Poll for a thing a background task does on its own time."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(1)
    raise AssertionError(f'{what} did not happen within {timeout}s')


def read(path: Path) -> str:
    """The bytes of a file that may not be there yet."""
    return path.read_text() if path.is_file() else ''


def ads_call(client, path: str, store_name: str, task_id: str) -> Any:
    """One pass-through call, sent exactly as the agent's tool sends it.

    ``vibe_seller_ads_call`` posts here with its own task id, and the
    server turns the store NAME into the service's key — so this is the
    same door, with the same body, and the same reason the header gets
    read.
    """
    resp = client.post(
        f'{BASE_URL}/api/ads/call',
        json={
            'path': path,
            'method': 'GET',
            'params': {},
            'store': store_name,
            'task_id': task_id,
        },
        timeout=120,
    )
    assert resp.status_code < 400, f'{path} -> {resp.status_code} {resp.text}'
    return resp.json()


def create_deferred_task(client, store: dict, title: str) -> dict:
    """A task row that is never started.

    ``/api/ads/call`` refuses a call with no task by design — the
    agent's tool always sends one — so test 1 needs a real task, and
    needs it not to run: this test is about the mechanism, and a model
    in it would measure the model. ``defer_start`` is the product's own
    door for that (the attachment upload uses it to put files in the
    workspace before the agent reads its prompt), so the row is made
    through the API rather than written into the database here.
    """
    return create_task(
        client,
        title,
        store_id=store['id'],
        description=title,
        defer_start=True,
    )


def run_agent_task(client, store: dict, question: str) -> dict:
    """A real agent task on this store, waited out to its end."""
    task = create_task(
        client, question, store_id=store['id'], description=question
    )
    task = poll_task_status(client, task['id'], TERMINAL)
    assert task['status'] == 'completed', (
        f'{task["id"][:8]} {task["status"]}: {task.get("error")}'
    )
    return task


def bind_store(client, srv, server, store: dict) -> None:
    """Authorize this store with the fake and get the skill it ships.

    ``POST /api/ads/stores/sync`` is what a person presses in Settings;
    the fake authorizes a store by the marker in its name. The
    authorization runs the ``ads_skill`` job itself (``reason=event``),
    which is how the machine comes to hold the version a test starts
    from — installed through the product, not by writing files here.
    """
    client.post(
        f'{BASE_URL}/api/ads/stores/sync', timeout=120
    ).raise_for_status()
    wanted = srv.served()
    path = skill_file(server)
    wait_until(
        f'the ads skill bundle installed at {path}',
        lambda: read(path) == wanted,
    )
    row = job_row(client, 'ads_skill')
    assert row['version'] == srv.served_version(), row


def skill_paths(srv, since: int) -> list[str]:
    """The ``/skill/*`` calls the service answered since a marker."""
    return [p for _, p, _ in srv.calls_since(since) if p.startswith('/skill/')]


# ── 1. the mechanism, without a model ─────────────────────────────


def test_one_ordinary_call_notices_a_new_skill_and_refreshes_it(
    api_client, service, own_stack
):
    ts = int(time.time())
    store = create_store(
        api_client, f'acme{fake_ads_service.AUTHORIZED_MARK}{ts}'
    )
    try:
        # ── What the service ships is this test's precondition, not an
        # assumption about whoever ran before it. Under xdist both tests
        # in this module can land on one worker in either order, and CI
        # reruns a test that hit a transient provider error — the second
        # run would otherwise inherit the bundle the first left
        # published. Publishing the baseline makes each test its own.
        service.publish(
            version=fake_ads_service.VERSION,
            files={'SKILL.md': fake_ads_service.SKILL_MD},
        )

        # ── A machine holding the current bundle ───────────────────
        bind_store(api_client, service, own_stack, store)
        path = skill_file(own_stack)
        assert read(path) == fake_ads_service.SKILL_MD

        # ── The service ships a change ─────────────────────────────
        logged = service.log_index()
        service.publish(
            version=fake_ads_service.VERSION_V2,
            files={'SKILL.md': fake_ads_service.SKILL_MD_V2},
        )
        before = len(run_lines(own_stack, 'ads_skill'))

        # ── One call, the kind an agent makes for a report ─────────
        task = create_deferred_task(api_client, store, 'spend, no model')
        answer = ads_call(
            api_client, '/facts/rollup', store['name'], task['id']
        )
        # ``{'result': ...}`` is the envelope this door answers with —
        # what the agent's tool hands back verbatim.
        assert answer['result']['total']['spend'] == (
            fake_ads_service.API_SPEND
        ), answer

        # ── That call ran the job, with a reason of its own ────────
        wait_until(
            'a stale-reason ads_skill run',
            lambda: len(run_lines(own_stack, 'ads_skill')) > before,
        )
        fresh = run_lines(own_stack, 'ads_skill')[before:]
        assert any('reason=stale' in ln for ln in fresh), fresh

        # ── In the service's own log: asked, then downloaded ───────
        # The order is the contract. A client that downloaded without
        # asking, or asked and never pulled, would end up with the same
        # bytes on disk and be a different machine.
        asked = skill_paths(service, logged)
        assert asked[:2] == ['/skill/version', '/skill/bundle'], asked

        # ── The new bytes are what the service serves ──────────────
        wait_until(
            'the new SKILL.md on disk',
            lambda: read(path) == service.served(),
        )
        assert service.served() != fake_ads_service.SKILL_MD
        assert job_row(api_client, 'ads_skill')['version'] == (
            fake_ads_service.VERSION_V2
        )

        # ── And the next call downloads nothing ────────────────────
        # The refresh's own two calls must not read as news, and a
        # current client must not ask at all: if it did, every call
        # after a deploy would start another download.
        logged = service.log_index()
        ads_call(api_client, '/facts/rollup', store['name'], task['id'])
        time.sleep(3)
        assert skill_paths(service, logged) == [], service.calls_since(logged)
        assert len(run_lines(own_stack, 'ads_skill')) == before + 1, run_lines(
            own_stack, 'ads_skill'
        )[-3:]
    finally:
        api_client.delete(f'{BASE_URL}/api/stores/{store["id"]}', timeout=120)


# ── 2. a real agent, on the skill it started with ─────────────────


# No ``long_budget`` marker: two real agent tasks on this fake measured
# 100s end to end here (qwen), under the 240s the marker is for. CI
# measures it into durations.json, and the marker gets added from that
# number, not from a guess.
def test_the_next_agent_task_works_from_the_new_skill(
    api_client, service, own_stack
):
    ts = int(time.time())
    store = create_store(
        api_client, f'acme{fake_ads_service.AUTHORIZED_MARK}{ts}'
    )
    try:
        # ── A stale client, the way one really gets stale ──────────
        # The service publishes the OLD document under a version nobody
        # holds, so the machine installs a skill that does not name the
        # pulse path and has no idea a newer one is coming.
        service.publish(
            version='e2e-4', files={'SKILL.md': fake_ads_service.SKILL_MD}
        )
        bind_store(api_client, service, own_stack, store)
        path = skill_file(own_stack)
        assert fake_ads_service.PULSE_PATH not in read(path)

        # ── The service ships the change ───────────────────────────
        service.publish(
            version='e2e-5',
            files={'SKILL.md': fake_ads_service.SKILL_MD_V2},
        )

        # ── Task 1: an ordinary spend question, on the old skill ───
        # Its first ads call is what carries the new version. The
        # answer comes from the rollup, whose figure exists nowhere
        # else — proof the API path was taken, by an agent whose own
        # document said nothing about pulse.
        logged = service.log_index()
        spend = run_agent_task(
            api_client, store, '这个店铺最近 30 天的广告花费是多少？'
        )
        assert fake_ads_service.API_SPEND in (spend.get('result') or ''), (
            spend.get('result')
        )
        assert '/skill/bundle' in skill_paths(service, logged), skill_paths(
            service, logged
        )
        wait_until(
            'the new skill installed after the first task',
            lambda: read(path) == service.served(),
        )
        assert fake_ads_service.PULSE_PATH in read(path)

        # ── Task 2: a question the old document cannot answer ──────
        # Nothing on this machine named the pulse path until the
        # refresh, so this call happens only if the second task's copy
        # of the skills tree came from the new bundle.
        pulse = run_agent_task(
            api_client, store, '这个店铺的广告 pulse 指标是多少？'
        )
        key = fake_ads_service.store_key(store['id'])
        asked = [
            q.get('store_key')
            for q in service.calls_to(fake_ads_service.PULSE_PATH)
        ]
        assert key in asked, f'pulse never asked for with {key}; saw {asked}'
        assert fake_ads_service.PULSE in (pulse.get('result') or ''), pulse.get(
            'result'
        )
    finally:
        api_client.delete(f'{BASE_URL}/api/stores/{store["id"]}', timeout=120)
