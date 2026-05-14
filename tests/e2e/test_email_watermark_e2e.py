"""Real e2e for the schedule_state watermark flow.

Requires a running server (see ``docker/E2E_TESTING.md``) and a
provider profile via ``E2E_PROVIDER_MAP``. A live LLM actually
decides to call ``vibe_seller_get_schedule_state`` and
``vibe_seller_set_schedule_state`` — if the agent ignores the
prompt or skips the handoff, the second-run assertion fails and
we learn the prompts need tightening.

Scenario:

1. Seed the per-account sqlite with one email containing SECRET_1.
2. Trigger the schedule → agent reads the DB, reports SECRET_1,
   writes the watermark.
3. Seed a second email containing SECRET_2.
4. Trigger the schedule again → agent reads the watermark and
   must only see SECRET_2 (never SECRET_1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import sqlite3
import time

import httpx
import pytest

from app.config import DATABASE_URL
from app.email.db import init_email_db, store_emails
from tests.e2e.conftest import BASE_URL
import tests.e2e.e2e_helpers as e2e_helpers
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    get_task,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]


SECRET_1 = 'sekret-alpha-42'
SECRET_2 = 'sekret-bravo-99'


def _now_minus(hours: float) -> str:
    """ISO-8601 UTC timestamp N hours before now.

    Dates are computed at fixture runtime (not module import) so the
    seeded emails stay inside the default "24-hour lookback" window
    the scheduled-pretask prompt tells agents to use on first run.
    Hard-coding e.g. 2026-04-15 would let agents correctly decide
    the email is stale and skip reporting its body.
    """
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


TASK_DESCRIPTION = (
    'Every day, check the linked email inbox for new messages that '
    'have arrived since the last time this task ran. For each new '
    'email, include its body verbatim in the report so I can see '
    'what is new. If nothing is new, say so.\n'
    '\n'
    'Do not open the browser — this is an email-only task.'
)


@pytest.fixture(scope='module')
def watermark_env(api_client: httpx.Client):
    """Provision store + email account + schedule; seed SECRET_1."""
    ts = int(time.time())
    store = create_store(
        api_client,
        f'watermark-e2e-{ts}',
        browser_backend='chrome',
    )

    account_email = f'watermark-{ts}@example.test'
    acct_resp = api_client.post(
        f'{BASE_URL}/api/email-accounts',
        json={
            'email': account_email,
            'password': 'dummy-not-used',
            'imap_host': 'imap.invalid',
            'imap_port': 993,
            'use_ssl': True,
            'smtp_host': 'smtp.invalid',
            'smtp_port': 587,
            'smtp_use_tls': True,
        },
    )
    acct_resp.raise_for_status()
    account = acct_resp.json()

    link_resp = api_client.post(
        f'{BASE_URL}/api/stores/{store["id"]}/emails',
        json={'email_account_id': account['id']},
    )
    link_resp.raise_for_status()

    init_email_db(account['id'])
    date_1 = _now_minus(2)
    store_emails(
        account['id'],
        [
            {
                'message_id': f'msg-1-{ts}@example.test',
                'folder': 'INBOX',
                'subject': 'Run1 inbound',
                'sender': 'partner@example.test',
                'date': date_1,
                'body_text': f'The value is {SECRET_1}. Please confirm.',
            }
        ],
    )

    sched_resp = api_client.post(
        f'{BASE_URL}/api/schedules',
        json={
            'title': f'Watermark e2e {ts}',
            'description': TASK_DESCRIPTION,
            'store_id': store['id'],
            'schedule_type': 'days',
            'schedule_time': '09:00',
            # Resolve at fixture runtime — the session-scoped
            # e2e conftest fixture sets e2e_helpers.DEFAULT_PROFILE_ID
            # AFTER this module is imported, so importing the
            # symbol directly would bind to None.
            'ai_profile_id': e2e_helpers.DEFAULT_PROFILE_ID,
        },
    )
    sched_resp.raise_for_status()
    schedule = sched_resp.json()

    # User-created schedules are plan-mode (see
    # docs/subsystems.md#plan-at-creation-lifecycle). Having a live
    # agent author + approve a plan would double the test runtime, so
    # we short-circuit: stop the spawned plan-only task, skip the
    # plan-authoring roundtrip entirely, and seed plan_status='ready'
    # with a trivial plan text so the fire-gate passes. This keeps
    # the test focused on the watermark handoff, not the lifecycle.
    _seed_ready_plan_for_e2e(api_client, schedule['id'])

    # Pause immediately so the APScheduler cron tick does not
    # race with our manual triggers.
    api_client.post(
        f'{BASE_URL}/api/schedules/{schedule["id"]}/pause'
    ).raise_for_status()

    yield {
        'store': store,
        'account': account,
        'schedule': schedule,
        'date_1': date_1,
    }

    try:
        api_client.delete(f'{BASE_URL}/api/schedules/{schedule["id"]}')
    except Exception:
        logger.exception('schedule cleanup failed')


def _seed_ready_plan_for_e2e(
    api_client: httpx.Client,
    schedule_id: str,
) -> None:
    """Bypass the plan-at-creation agent roundtrip for e2e tests.

    Reads the planning-task pointer via the HTTP API, stops the
    planner via HTTP, then writes a trivial plan straight to the
    SQLite file via raw sqlite3 — the fire-gate only checks
    ``plan_status=='ready'``, the plan text is not exercised by this
    test.

    Uses raw sqlite3 (not SQLAlchemy) for two reasons: the test
    process shares the DB file with the separately-running server
    but not the engine pool, and pytest-asyncio already owns the
    current event loop so we can't spin up an async session here.

    Without this, ``/trigger`` returns 409 because the fire-gate
    refuses schedules whose plan is still being authored.
    """
    # DATABASE_URL is like 'sqlite+aiosqlite:///path/to/db'. Strip
    # the driver prefix to get the file path for raw sqlite3.
    db_path = DATABASE_URL.split('///', 1)[1]

    # Poll briefly for the planning-task pointer to appear in the DB.
    # (POST /schedules returns as soon as the row is committed, so
    # usually it's already there.)
    planning_task_id: str | None = None
    for _ in range(100):
        resp = api_client.get(f'{BASE_URL}/api/schedules/{schedule_id}')
        if resp.status_code == 200:
            planning_task_id = resp.json().get('current_planning_task_id')
            if planning_task_id:
                break
        time.sleep(0.05)

    # Stop the agent session via HTTP so we hit the same agent_manager
    # singleton the server owns. If we skip this, the cancelled
    # planner can race back and clobber our seeded plan.
    if planning_task_id:
        try:
            api_client.post(
                f'{BASE_URL}/api/tasks/{planning_task_id}/agent/stop'
            )
        except Exception:
            logger.debug('stop planner raised', exc_info=True)

    # Force-seed plan_status='ready' so the fire-gate lets /trigger
    # pass. Raw sqlite3 — no SQLAlchemy engine and no event loop.
    # Using a short-lived connection with IMMEDIATE mode to avoid
    # collisions with the server's writers.
    conn = sqlite3.connect(db_path, isolation_level='IMMEDIATE', timeout=10)
    try:
        conn.execute(
            (
                "UPDATE schedules SET plan = ?, plan_status = 'ready',"
                ' plan_version = COALESCE(plan_version, 0) + 1,'
                ' plan_error = NULL, current_planning_task_id = NULL'
                ' WHERE id = ?'
            ),
            ('e2e-test-plan (seeded, fire-gate bypass)', schedule_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_watermark(
    api_client: httpx.Client,
    task_id: str,
) -> int | None:
    """Return the stored email_watermark as a unix epoch int, or None.

    Server-side validation rejects anything that isn't a digit-only
    string for this key, so if the agent stored the watermark
    correctly, ``int(value)`` will always succeed.
    """
    resp = api_client.get(
        f'{BASE_URL}/api/tasks/{task_id}/schedule-state/email_watermark'
    )
    resp.raise_for_status()
    raw = resp.json()['value']
    return int(raw) if raw is not None else None


def _collect_task_text(
    api_client: httpx.Client,
    task_id: str,
) -> str:
    """Return every assistant/tool/result message body concatenated.

    What this test actually validates is "did the agent SEE / REPORT
    the expected SECRET_x content". Task-messages are persisted
    mid-stream (via ``_stream_output``) regardless of whether the
    agent's session terminates cleanly, so they're the right
    observable — unlike ``task.result``, which is only populated on
    clean session exit and disappears if the upstream SSE stream
    stalls (a known GLM-4.7 / Z.AI pattern — see issue #141).
    """
    resp = api_client.get(f'{BASE_URL}/api/tasks/{task_id}/messages')
    resp.raise_for_status()
    parts: list[str] = []
    for msg in resp.json():
        content = msg.get('content')
        if content is None:
            continue
        # content may be str or list-of-blocks JSON — the router
        # returns the raw column, so stringify defensively.
        parts.append(content if isinstance(content, str) else str(content))
    return '\n'.join(parts)


def _trigger_and_wait_for_watermark(
    api_client: httpx.Client,
    schedule_id: str,
    label: str,
    *,
    min_watermark: int | None = None,
    timeout: int = PIPELINE_TIMEOUT,
) -> tuple[dict, int]:
    """Trigger schedule + wait for the watermark to satisfy the test.

    The PR-functional observable is "did the agent read + persist the
    cursor", not "did the agent session terminate cleanly". We poll
    both the task status AND the schedule_state endpoint, and return
    as soon as either:

    * the task reaches ``completed`` / ``failed`` (normal path), or
    * the watermark has been set to a value > ``min_watermark``
      (degraded path — the agent did the essential work but its
      session stalled upstream before emitting a stop event).

    On the degraded path we call ``/agent/stop`` to free the
    stream + CDP resources before the caller moves on. This keeps
    the test honest (we still assert what the agent reported via
    ``_collect_task_text``) without masking PR-relevant failures:
    if the agent never writes the watermark or writes the wrong
    value, this function times out exactly like before.
    """
    resp = api_client.post(f'{BASE_URL}/api/schedules/{schedule_id}/trigger')
    resp.raise_for_status()
    task_id = resp.json()['task_id']
    logger.info('[%s] triggered task %s', label, task_id[:8])

    deadline = time.time() + timeout
    last_status = ''
    while time.time() < deadline:
        task = get_task(api_client, task_id)
        status = task.get('status', '')
        if status != last_status:
            logger.info('[%s] task=%s status=%s', label, task_id[:8], status)
            last_status = status

        if status in {'completed', 'failed'}:
            watermark = _get_watermark(api_client, task_id)
            if watermark is None:
                pytest.fail(
                    f'[{label}] task {task_id[:8]} ended in {status!r}'
                    ' but no watermark was persisted'
                )
            return task, watermark

        watermark = _get_watermark(api_client, task_id)
        if watermark is not None and (
            min_watermark is None or watermark > min_watermark
        ):
            # Agent did its job. Stop the session so the stalled
            # stream doesn't hog resources while the rest of the
            # test runs. Best-effort — if /agent/stop 404s because
            # the session already exited, that's fine too.
            logger.info(
                '[%s] watermark=%d captured; stopping agent %s',
                label,
                watermark,
                task_id[:8],
            )
            try:
                api_client.post(f'{BASE_URL}/api/tasks/{task_id}/agent/stop')
            except Exception:
                logger.debug('[%s] agent/stop raised', label, exc_info=True)
            return get_task(api_client, task_id), watermark

        time.sleep(3)

    raise TimeoutError(
        f'[{label}] task {task_id[:8]} did not persist a watermark'
        f' within {timeout}s (last status={last_status})'
    )


def _epoch_of(iso: str) -> int:
    """Parse an ISO date string and return unix epoch seconds."""
    return int(datetime.fromisoformat(iso).timestamp())


class TestEmailWatermarkE2E:
    """Real agent drives get/set_schedule_state across two runs.

    Kept as a single test method because the two runs share a
    schedule + per-account sqlite, and pytest-xdist happily splits
    separate test methods across workers (each worker gets its own
    fixture instance, which would make run 2 start from null).
    """

    def test_watermark_handoff_across_two_runs(
        self,
        api_client: httpx.Client,
        watermark_env: dict,
    ):
        schedule = watermark_env['schedule']
        account = watermark_env['account']
        date_1 = watermark_env['date_1']

        # ── Run 1: DB has only SECRET_1, no prior watermark ──
        task1, watermark1 = _trigger_and_wait_for_watermark(
            api_client, schedule['id'], 'run1'
        )
        text1 = _collect_task_text(api_client, task1['id'])
        assert SECRET_1 in text1, (
            f'run1 messages missing SECRET_1={SECRET_1!r}. '
            f'agent transcript was: {text1!r}'
        )
        # Numeric compare — watermark is an epoch int, so the run-1
        # email's date must be at or before it.
        epoch_1 = _epoch_of(date_1)
        assert watermark1 >= epoch_1, (
            f'watermark epoch {watermark1} should be >= email1 epoch '
            f'{epoch_1} (email1 date={date_1!r})'
        )

        # ── Between runs: SECRET_2 arrives in the same sqlite. ──
        # Strictly newer than SECRET_1 (run-1 watermark) but also
        # within the 24-hour default window that the scheduled
        # pretask prompt tells agents to fall back to.
        date_2 = _now_minus(0.5)
        store_emails(
            account['id'],
            [
                {
                    'message_id': (f'msg-2-{int(time.time())}@example.test'),
                    'folder': 'INBOX',
                    'subject': 'Run2 inbound',
                    'sender': 'partner@example.test',
                    'date': date_2,
                    'body_text': f'Follow-up: {SECRET_2}. Thanks.',
                }
            ],
        )

        # ── Run 2: must read watermark and only see SECRET_2 ──
        # Require the watermark to ADVANCE past run-1's value — a
        # stale or re-asserted watermark would be indistinguishable
        # from the "agent ignored the cursor" failure mode.
        task2, watermark2 = _trigger_and_wait_for_watermark(
            api_client,
            schedule['id'],
            'run2',
            min_watermark=watermark1,
        )
        text2 = _collect_task_text(api_client, task2['id'])

        assert SECRET_2 in text2, (
            f'run2 messages missing SECRET_2={SECRET_2!r}. '
            f'agent transcript was: {text2!r}'
        )
        assert SECRET_1 not in text2, (
            'run2 leaked SECRET_1 — the agent either ignored the '
            'watermark or re-read emails before it. This is the '
            'primary failure mode this test is designed to catch. '
            f'agent transcript was: {text2!r}'
        )

        epoch_2 = _epoch_of(date_2)
        assert watermark2 >= epoch_2, (
            f'watermark epoch {watermark2} should have advanced to '
            f'>= email2 epoch {epoch_2} (email2 date={date_2!r}) '
            'after processing SECRET_2'
        )
