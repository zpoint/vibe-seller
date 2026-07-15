"""Opt-in E2E: save + reuse a skill against a REAL Noon store.

This is the live counterpart to ``test_skill_save_reuse.py``. It uses
a real, already-bound store (its slug comes from ``VIBE_E2E_STORE``),
a real browser to export orders from the seller center, and a real
configured WeCom bot to post the summary. It is SKIPPED unless
``VIBE_E2E_STORE`` is set, so it never runs in CI.

Run locally, e.g.::

    VIBE_E2E_STORE=<your-store-slug> \
    E2E_PROVIDER_MAP=<provider> \
    pytest --e2e tests/e2e/test_skill_save_reuse_noon.py

The task wording is pure human intent — the *how* (which report to
export, how to post to WeCom) lives in the skills, not the prompt.
"""

import logging
import os
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    create_task,
    get_messages,
    poll_task_status,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

_STORE_SLUG = os.environ.get('VIBE_E2E_STORE', '').strip()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _STORE_SLUG,
        reason='set VIBE_E2E_STORE=<store-slug> to run the live Noon test',
    ),
]

# Live browser export + report generation is slow; give it headroom.
LIVE_TIMEOUT = max(PIPELINE_TIMEOUT, 1800)


def _wait_for_followup_result(client, task_id, known_count):
    deadline = time.time() + LIVE_TIMEOUT
    while time.time() < deadline:
        msgs = get_messages(client, task_id)
        if any(m.get('role') == 'result' for m in msgs[known_count:]):
            return msgs
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'No follow-up result for {task_id[:8]}')


def _custom_skills(client):
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


@pytest.fixture
def live_store(api_client):
    resp = api_client.get(f'{BASE_URL}/api/stores')
    resp.raise_for_status()
    for s in resp.json():
        if _STORE_SLUG in (s.get('slug'), s.get('name')):
            return s
    pytest.skip(f'store {_STORE_SLUG!r} not found on this server')


@pytest.fixture
def has_wecom_bot(api_client):
    resp = api_client.get(f'{BASE_URL}/api/wecom-bots')
    resp.raise_for_status()
    if not resp.json():
        pytest.skip('no WeCom bot configured; add one in Settings first')
    return True


class TestSaveAndReuseSkillLive:
    def test_create_then_extend_live(
        self, api_client, live_store, has_wecom_bot
    ):
        store_id = live_store['id']
        customs_start = set(_custom_skills(api_client))

        # ── Task 1: export + total (real browser) ──
        task1 = create_task(
            api_client,
            title='How much did we make on Noon last month?',
            store_id=store_id,
            description=(
                'Pull up last month’s orders on Noon and add up the '
                'total revenue for me. Just tell me the number.'
            ),
        )
        t1 = poll_task_status(
            api_client,
            task1['id'],
            {'completed'},
            fail_statuses={'failed'},
            timeout=LIVE_TIMEOUT,
        )
        assert t1['status'] == 'completed', f'task1 failed: {t1.get("error")}'

        # ── Follow-up 1: save as skill ──
        count1 = len(get_messages(api_client, task1['id']))
        api_client.post(
            f'{BASE_URL}/api/tasks/{task1["id"]}/messages',
            json={
                'content': (
                    'Save this whole workflow as a skill so you can do '
                    'it the same way next month.'
                )
            },
        ).raise_for_status()
        _wait_for_followup_result(api_client, task1['id'], count1)

        customs_after_save = _custom_skills(api_client)
        new_slugs = set(customs_after_save) - customs_start
        assert new_slugs, 'no user-space skill created'
        for slug in new_slugs:
            assert customs_after_save[slug]['source'] == 'custom'

        # ── Task 2: reuse skill + new wrinkle (WeCom) ──
        task2 = create_task(
            api_client,
            title='Last month’s Noon revenue, and send it to the group',
            store_id=store_id,
            description=(
                'Get last month’s total Noon revenue again, then post a '
                'short summary to our WeCom group.'
            ),
        )
        t2 = poll_task_status(
            api_client,
            task2['id'],
            {'completed'},
            fail_statuses={'failed'},
            timeout=LIVE_TIMEOUT,
        )
        assert t2['status'] == 'completed', f'task2 failed: {t2.get("error")}'

        # ── Follow-up 2: save again → EXTEND, not duplicate ──
        count2 = len(get_messages(api_client, task2['id']))
        api_client.post(
            f'{BASE_URL}/api/tasks/{task2["id"]}/messages',
            json={
                'content': (
                    'Update that same skill so it also posts the summary '
                    'to WeCom next time.'
                )
            },
        ).raise_for_status()
        _wait_for_followup_result(api_client, task2['id'], count2)

        customs_final = _custom_skills(api_client)
        assert set(customs_final) == set(customs_after_save), (
            'second save duplicated instead of extending: '
            f'{sorted(customs_after_save)} -> {sorted(customs_final)}'
        )
        assert any(
            'wecom' in _skill_md(api_client, slug).lower()
            or '企业微信' in _skill_md(api_client, slug)
            for slug in new_slugs
        ), 'extended skill does not mention the WeCom step'
