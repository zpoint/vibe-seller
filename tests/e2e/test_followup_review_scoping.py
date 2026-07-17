"""E2E: multi-round follow-ups each get their OWN review (turn scoping).

Real agent, no browser. Pins the follow-up review-gate design:

- A task bound to a skill that declares a ``review:`` block completes its
  first deliverable through the DoD reviewer.
- A follow-up on the SAME task is a NEW turn: the prior turn's review is
  rolled aside (``.prev_turns/``) so the follow-up is reviewed on its own
  merits — it can't ride the prior turn's verdict, and a one-shot
  follow-up signs off fast instead of re-gating the earlier deliverable.
- A separate task exercises the different-task path.

Each round writes a note carrying a DISTINCTIVE marker so both the
assertions here and a ``debug-ci`` pass over the agent/reviewer thinking
log can confirm each round is scoped to its own request.

Runs in CI (real agent token); creates its own ephemeral store + a
throwaway user-space review skill, needs no real creds.
"""

import logging
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

# A user-space skill that OPTS INTO the DoD reviewer (``review:`` block)
# but whose work + verification are purely LOCAL — so the reviewer runs
# for real without a browser/seller. The verify_by explicitly scopes the
# reviewer to THIS turn's file and tells it to sign off fast (one-shot).
_SKILL_SLUG = 'e2e-followup-note'
_SKILL_MD = """---
name: e2e-followup-note
description: "Record a short note containing an exact marker string. Use \
whenever the user asks to 'record a note' / 'jot down' a marker. Pure \
local text work — never open a browser."
review:
  criteria: |
    - The note this turn asked for was actually produced: the agent's
      result contains the EXACT marker string from THIS request.
  verify_by: |
    Confirm the result echoes this turn's marker string verbatim. This is
    a one-shot local note — if the marker is present, sign off
    `Status: ok` immediately. Do NOT re-verify or re-gate any note from a
    previous request; only this turn's marker matters.
---

# Record a note

Write the requested marker string into a note and report it back in your
result verbatim. One note per request; do not touch prior notes.
"""


@pytest.fixture
def review_skill(api_client):
    """Install the throwaway review-declaring skill, remove it after."""
    resp = api_client.put(
        f'{BASE_URL}/api/workspace/skills/{_SKILL_SLUG}',
        json={'skill_md': _SKILL_MD},
    )
    resp.raise_for_status()
    yield _SKILL_SLUG
    api_client.delete(f'{BASE_URL}/api/workspace/skills/{_SKILL_SLUG}')


def _wait_for_followup_result(client, task_id, known_count):
    """Poll until a new 'result' turn appears (follow-ups keep the task
    'completed', so status polling can't be used)."""
    deadline = time.time() + PIPELINE_TIMEOUT
    while time.time() < deadline:
        msgs = get_messages(client, task_id)
        if any(m.get('role') == 'result' for m in msgs[known_count:]):
            return msgs
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'No follow-up result for {task_id[:8]}')


def _recent_text(client, task_id, since):
    """assistant/result/thinking content emitted after index `since`."""
    msgs = get_messages(client, task_id)
    return ' '.join(
        m.get('content', '')
        for m in msgs[since:]
        if m.get('role') in ('assistant', 'result', 'thinking')
    )


def _followup(client, task_id, content, marker):
    known = len(get_messages(client, task_id))
    r = client.post(
        f'{BASE_URL}/api/tasks/{task_id}/messages',
        json={'content': content},
    )
    r.raise_for_status()
    _wait_for_followup_result(client, task_id, known)
    text = _recent_text(client, task_id, known)
    assert marker in text, (
        f'follow-up result missing its own marker {marker!r}; got: {text[:400]}'
    )
    return text


class TestFollowUpReviewScoping:
    def test_multi_round_followups_each_reviewed_separately(
        self, api_client, review_skill
    ):
        tag = int(time.time())
        store = create_store(api_client, f'e2e-followup-{tag}')

        # ── Task A, round 1 ──────────────────────────
        task_a = create_task(
            api_client,
            title='Record a note (round 1)',
            store_id=store['id'],
            description=(
                'Use the e2e-followup-note skill. Record a note whose '
                'exact marker string is ALPHA-R1. Echo ALPHA-R1 back in '
                'your result. Local only — do not open a browser or ask '
                'questions.'
            ),
        )
        t = poll_task_status(
            api_client, task_a['id'], {'completed'}, fail_statuses={'failed'}
        )
        assert t['status'] == 'completed', f'round1 failed: {t.get("error")}'
        assert 'ALPHA-R1' in _recent_text(api_client, task_a['id'], 0)

        # ── Task A, round 2 (same task, one-shot follow-up) ──
        # Must complete on its OWN review — not ride round 1's verdict,
        # not re-gate round 1's note.
        _followup(
            api_client,
            task_a['id'],
            'Now record a note whose exact marker is BRAVO-R2. Echo '
            'BRAVO-R2 back. One-shot — do not revisit ALPHA-R1.',
            'BRAVO-R2',
        )

        # ── Task A, round 3 (same task) ──────────────
        _followup(
            api_client,
            task_a['id'],
            'Now record a note whose exact marker is CHARLIE-R3. Echo '
            'CHARLIE-R3 back.',
            'CHARLIE-R3',
        )

        # ── Task B (DIFFERENT task, same skill) ──────
        task_b = create_task(
            api_client,
            title='Record a note (separate task)',
            store_id=store['id'],
            description=(
                'Use the e2e-followup-note skill. Record a note whose '
                'exact marker is DELTA-T2. Echo DELTA-T2 back. Local '
                'only, no questions.'
            ),
        )
        t2 = poll_task_status(
            api_client, task_b['id'], {'completed'}, fail_statuses={'failed'}
        )
        assert t2['status'] == 'completed', f'task B failed: {t2.get("error")}'
        assert 'DELTA-T2' in _recent_text(api_client, task_b['id'], 0)
