"""One schedule, every store: each store reads its ads its own way.

Some stores are authorized for the Ads API and some are not. A fanout
schedule's plan is written ONCE, by a planner with no store, and handed
verbatim to every store's task — so the question this answers is the
one a seller asks: "read all my ad spend" — does the API store use the
API and the console store use the browser?

Two things had to hold, and only a real agent shows both:

- the planner, seeing which stores are bound, writes a plan that leaves
  the path to each store rather than picking one for all;
- each store's task, told its own path, takes it even where the shared
  plan names the other.

**Ground truth, not prose.** The two paths serve different figures: the
fake service's rollup says 123.45, the console says 300.00, and neither
appears anywhere else. A report carrying a figure proves which path it
came from. The service also logs every rollup with the store key it was
asked for, and the console store's transcript is searched for an API
call — refused calls never reach the service, so the transcript is the
only place one would show.

Fanout covers every store on the server, so the test cancels the
children of stores it did not create. It needs the server started with
``VIBE_ADS_SERVICE_URL`` pointing at the fake (``fake_ads_service``),
and skips otherwise: syncing stores against the real service would
register this test server with it.
"""

import json
import logging
import os
import time

import pytest

from tests.e2e import fake_ads_service
from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    create_store,
    get_messages,
)
from tests.e2e.fake_ads_console import serve as serve_console

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]

TERMINAL = {'completed', 'failed', 'cancelled'}
CONSOLE_SPEND = '300'


@pytest.fixture
def ads_service():
    if os.environ.get('VIBE_ADS_SERVICE_URL') != fake_ads_service.URL:
        pytest.skip(
            'server not started against the fake ads service '
            f'(VIBE_ADS_SERVICE_URL={fake_ads_service.URL})'
        )
    service = fake_ads_service.serve()
    yield service
    service.shutdown()
    service.server_close()


@pytest.fixture
def console():
    server = serve_console()
    yield server
    server.shutdown()
    server.server_close()


def _ads_calls(messages: list[dict]) -> list[dict]:
    """Every ``vibe_seller_ads_call`` the agent made, from its transcript."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'tool_use' and str(
                node.get('name', '')
            ).endswith('vibe_seller_ads_call'):
                found.append(node.get('input') or {})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for message in messages:
        try:
            walk(json.loads(message.get('content') or ''))
        except (TypeError, ValueError):
            continue
    return found


def _wait_for_plan(client, schedule_id: str) -> dict:
    deadline = time.time() + PIPELINE_TIMEOUT
    while time.time() < deadline:
        schedule = client.get(f'{BASE_URL}/api/schedules/{schedule_id}')
        schedule.raise_for_status()
        body = schedule.json()
        if body['plan_status'] in ('ready', 'failed'):
            return body
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'schedule {schedule_id[:8]} was never planned')


def _children(client, schedule_id: str) -> list[dict]:
    tasks = client.get(f'{BASE_URL}/api/tasks')
    tasks.raise_for_status()
    return [
        t
        for t in tasks.json()
        if t.get('schedule_id') == schedule_id and t.get('store_id')
    ]


def _run_fanout(client, schedule_id: str, ours: set[str]) -> dict[str, dict]:
    """Fire once; stop every child not on our stores; wait for ours."""
    client.post(
        f'{BASE_URL}/api/schedules/{schedule_id}/trigger'
    ).raise_for_status()
    stopped: set[str] = set()
    deadline = time.time() + PIPELINE_TIMEOUT
    while time.time() < deadline:
        mine: dict[str, dict] = {}
        for task in _children(client, schedule_id):
            if task['store_id'] in ours:
                mine[task['store_id']] = task
            elif task['id'] not in stopped:
                client.post(f'{BASE_URL}/api/tasks/{task["id"]}/agent/stop')
                stopped.add(task['id'])
        if len(mine) == len(ours) and all(
            t['status'] in TERMINAL for t in mine.values()
        ):
            return mine
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'fanout children never finished: {mine}')


@pytest.mark.e2e
class TestMixedFanout:
    def test_each_store_reads_its_ads_its_own_way(
        self, api_client, ads_service, console
    ):
        ts = int(time.time())
        api_store = create_store(
            api_client, f'e2e{fake_ads_service.AUTHORIZED_MARK}{ts}'
        )
        console_store = create_store(api_client, f'e2e-console-{ts}')
        schedule_id = None
        try:
            # ── Bind: the service says which store is authorized ──────
            api_client.post(
                f'{BASE_URL}/api/ads/stores/sync'
            ).raise_for_status()
            stores = {
                s['id']: s
                for s in api_client.get(f'{BASE_URL}/api/stores').json()
            }
            assert 'SA' in stores[api_store['id']]['countries'], stores[
                api_store['id']
            ]

            # ── Plan once, for every store ────────────────────────────
            r = api_client.post(
                f'{BASE_URL}/api/schedules',
                json={
                    'title': 'Ad spend, every store',
                    'description': (
                        # Written the way a seller who only knows the
                        # console would: it names the console for every
                        # store. The bound store must still use the API.
                        'Open our seller ad console at '
                        f'{console.base}/ with the browser-use CLI, read '
                        'the last 30 days of advertising spend and ad '
                        'sales for this store, and report the two numbers.'
                    ),
                    'schedule_type': 'days',
                    'schedule_time': '09:00',
                    'plan_mode': True,
                    'phase_mode': 'fanout',
                },
            )
            r.raise_for_status()
            schedule_id = r.json()['id']
            schedule = _wait_for_plan(api_client, schedule_id)
            assert schedule['plan_status'] == 'ready', schedule.get(
                'plan_error'
            )
            logger.info('shared plan:\n%s', schedule['plan'])

            # ── Fire: one child per store, the same plan for both ─────
            children = _run_fanout(
                api_client,
                schedule_id,
                {api_store['id'], console_store['id']},
            )
            via_api = children[api_store['id']]
            via_console = children[console_store['id']]
            for child in (via_api, via_console):
                assert child['status'] == 'completed', (
                    f'{child["id"][:8]} {child["status"]}: {child.get("error")}'
                )

            # ── The API store read the API, and only the API ─────────
            api_report = via_api.get('result') or ''
            assert fake_ads_service.API_SPEND in api_report, api_report
            assert CONSOLE_SPEND not in api_report, (
                f'the API store reported the console figure: {api_report}'
            )
            keys = {
                q.get('store_key')
                for q in ads_service.calls_to('/facts/rollup')
            }
            assert keys == {fake_ads_service.store_key(api_store['id'])}, (
                f'rollups were asked for {keys}'
            )

            # ── The console store read the console, never the API ────
            console_report = via_console.get('result') or ''
            assert CONSOLE_SPEND in console_report, console_report
            assert fake_ads_service.API_SPEND not in console_report
            assert '/' in console.served(), console.served()
            attempted = _ads_calls(get_messages(api_client, via_console['id']))
            assert not attempted, (
                f'the console store tried the API first: {attempted}'
            )
        finally:
            if schedule_id:
                api_client.delete(f'{BASE_URL}/api/schedules/{schedule_id}')
            # Deleting the bound store leaves no store authorized, so no
            # later test hears about ads paths.
            for store in (api_store, console_store):
                api_client.delete(f'{BASE_URL}/api/stores/{store["id"]}')
