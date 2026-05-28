"""E2E test: Context compaction on profile switch.

Verifies that when switching profiles (which forces a new CLI
session), chat history is externalized to a JSON file and the
new agent can recover context from it.

Requires: running server with TWO different LLM provider credentials.
Providers are derived from the first two distinct values in
E2E_PROVIDER_MAP (e.g. ``deepseek,deepseek,minimax`` → A=deepseek,
B=minimax).

Marked with @pytest.mark.e2e so they run only on demand.
"""

import json
import logging
import time

import pytest

from app.ai.compaction import HISTORY_DIR
from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    build_profile_env,
    fetch_presets,
    get_distinct_providers,
    get_messages,
    get_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

PROF_A = 'e2e-compact-a'
PROF_B = 'e2e-compact-b'

pytestmark = [pytest.mark.e2e]


# ── Helpers ─────────────────────────────────────────


def _resolve_ab() -> tuple[str, str]:
    """Resolve provider A/B names."""
    try:
        name_a, name_b = get_distinct_providers()
    except ValueError:
        pytest.skip('E2E_PROVIDER_MAP missing or <2 distinct providers')
    return name_a, name_b


def _wait_for_session_id(
    client,
    task_id: str,
    timeout: int = 30,
) -> str | None:
    """Poll until `task.session_id` becomes non-null.

    `_save_result` (claude_backend_stream.py) persists session_id
    AFTER the process exits, which can lag the final `result` event
    that `_wait_for_result_message` observes by 100ms – several
    seconds. Without this helper, a GET right after the result
    message races the async persist and sees None.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = get_task(client, task_id)
        sid = data.get('session_id')
        if sid:
            return sid
        time.sleep(POLL_INTERVAL)
    return None


def _wait_for_result_message(
    client,
    task_id: str,
    known_count: int,
    timeout: int = PIPELINE_TIMEOUT,
) -> list[dict]:
    """Poll until a result message appears beyond known_count.

    Waits for the final ``result`` message (agent pipeline done),
    not just any assistant turn — agents like GLM send intermediate
    assistant messages while using tools.
    """
    msgs: list[dict] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = get_messages(client, task_id)
        new_msgs = msgs[known_count:]
        has_result = any(m.get('role') == 'result' for m in new_msgs)
        if has_result:
            return msgs
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f'No result message for task {task_id[:8]} '
        f'within {timeout}s (known={known_count}, '
        f'current={len(msgs)})'
    )


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def providers():
    """Resolve and validate both providers."""
    return _resolve_ab()


@pytest.fixture(scope='module')
def test_store(api_client):
    tag = int(time.time())
    resp = api_client.post(
        f'{BASE_URL}/api/stores',
        json={'name': f'e2e-compact-{tag}'},
    )
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope='module')
def presets(api_client):
    """Fetch provider presets from server."""
    return fetch_presets(api_client)


@pytest.fixture(scope='module')
def profile_a(api_client, providers, presets):
    name_a, _ = providers
    profile = {
        'id': PROF_A,
        'name': 'E2E Compact A',
        'env': build_profile_env(name_a, presets),
    }
    resp = api_client.post(f'{BASE_URL}/api/profiles', json=profile)
    resp.raise_for_status()
    return profile


@pytest.fixture(scope='module')
def profile_b(api_client, providers, presets):
    _, name_b = providers
    profile = {
        'id': PROF_B,
        'name': 'E2E Compact B',
        'env': build_profile_env(name_b, presets),
    }
    resp = api_client.post(f'{BASE_URL}/api/profiles', json=profile)
    resp.raise_for_status()
    return profile


# ── Test ────────────────────────────────────────────


# Unique code the agent must recall after profile switch
_SECRET_CODE = 'ALPHA-7742'


class TestCompactionOnProfileSwitch:
    def test_profile_switch_preserves_context_via_history_file(
        self,
        api_client,
        test_store,
        profile_a,
        profile_b,
    ):
        """Create task with a secret code on profile A, switch
        to profile B, verify agent can recall the code (proving
        history was externalized and recovered).

        Uses the continue path (not retry — retry deletes
        messages).
        """
        tag = int(time.time())

        # Step 1: Create task with profile A — simple prompt
        # to minimize LLM turns and execution time.
        # The title intentionally avoids the word "code" + a numeric
        # tag: weaker models (MiniMax-M2.7 observed) treat
        # ``Remember code {tag}`` in the title as a competing
        # instruction with ``Remember this code: {_SECRET_CODE}``
        # in the description and reply with the wrong value.
        resp = api_client.post(
            f'{BASE_URL}/api/tasks',
            json={
                'title': f'Memory test {tag}',
                'store_id': test_store['id'],
                'profile_id': PROF_A,
                'description': (
                    f'Remember this code: {_SECRET_CODE}. '
                    'Reply with ONLY the code, nothing else. '
                    'Do not use the browser. '
                    'Do not ask questions.'
                ),
            },
        )
        resp.raise_for_status()
        task_id = resp.json()['id']

        # Step 2: Wait for completion
        data = poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert data['status'] == 'completed', (
            f'Task failed: {data.get("error")}'
        )

        # Record message count before the switch
        msgs_before = get_messages(api_client, task_id)
        assert len(msgs_before) > 0
        count_before = len(msgs_before)

        # Step 3: Send a chat message with profile B
        # This triggers a profile switch → new session with
        # history reconstruction (the code path we're testing).
        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/messages',
            json={
                'content': (
                    'What was the secret code from our '
                    'earlier discussion? Reply with ONLY '
                    'the code.'
                ),
                'profile_id': PROF_B,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['profile_switched'] is True

        # Wait for the agent to respond with an assistant
        # message. Can't poll task status — it stays
        # 'completed' throughout.
        msgs_after = _wait_for_result_message(api_client, task_id, count_before)

        # Step 4: Verify history file was created
        history_file = HISTORY_DIR / f'{task_id}.json'
        assert history_file.exists(), (
            f'History file not found at {history_file}'
        )

        # Step 5: Verify file contains the secret code
        file_content = history_file.read_text(encoding='utf-8')
        history_data = json.loads(file_content)
        assert isinstance(history_data, list)
        assert len(history_data) > 0

        all_content = ' '.join(
            entry.get('content', '') for entry in history_data
        )
        assert _SECRET_CODE in all_content, (
            f'{_SECRET_CODE} not found in history file'
        )

        # Step 6: Verify agent recovered context.
        # Include 'thinking' role: some providers (e.g. glm-4.7
        # under interleaved-thinking mode) sometimes return the
        # final answer in a thinking block with no text block. The
        # contract is "the agent's response contains the code" —
        # which is demonstrated equally by either content kind.
        switch_msgs = [
            m
            for m in msgs_after[count_before:]
            if m.get('role') in ('assistant', 'result', 'thinking')
        ]
        if switch_msgs:
            response_text = ' '.join(m.get('content', '') for m in switch_msgs)
            assert _SECRET_CODE in response_text, (
                'Agent did not recall secret code after '
                'profile switch. Response: '
                f'{response_text[:500]}'
            )

        # Step 7: Verify profile was switched
        final = get_task(api_client, task_id)
        assert final.get('ai_profile_id') == PROF_B

        # Step 8: Second follow-up on the SAME profile — exercises
        # the --resume path (has_resumable_session=True). Regression
        # guard: claude_backend overwrote task.session_id with each
        # resume's new id, pointing future follow-ups at a transcript
        # that only held the latest turn-pair and silently dropping
        # the original context.
        #
        # Poll because `_save_result` persists session_id AFTER the
        # result event is streamed — reading once races that write.
        session_id_before_fu2 = _wait_for_session_id(api_client, task_id)
        assert session_id_before_fu2, (
            'Expected task.session_id to be set after the first '
            'follow-up (it seeds the --resume chain).'
        )
        count_before_fu2 = len(msgs_after)

        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/messages',
            json={
                'content': (
                    'Repeat the secret code from our earlier '
                    'exchange. Reply with ONLY the code.'
                ),
                'profile_id': PROF_B,
            },
        )
        assert resp.status_code == 200
        body2 = resp.json()
        # No switch this time — same profile as the prior run.
        assert body2.get('profile_switched') is False

        msgs_after_fu2 = _wait_for_result_message(
            api_client, task_id, count_before_fu2
        )

        # Behavior: agent recalls the secret across the --resume.
        # Accept 'thinking' content for the same reason as Step 6 —
        # some providers omit the text block when the answer is
        # short and produce only a thinking block.
        fu2_msgs = [
            m
            for m in msgs_after_fu2[count_before_fu2:]
            if m.get('role') in ('assistant', 'result', 'thinking')
        ]
        assert fu2_msgs, 'No assistant/result message for second follow-up'
        fu2_text = ' '.join(m.get('content', '') for m in fu2_msgs)
        assert _SECRET_CODE in fu2_text, (
            'Agent lost context on the second same-profile '
            'follow-up — --resume did not replay prior turns. '
            f'Response: {fu2_text[:500]}'
        )

        # Root-cause assertion: session_id must be stable across
        # same-profile follow-ups. If it drifted, the next resume
        # would point at a transcript missing all earlier history.
        #
        # Pre-fix: task.session_id would be OVERWRITTEN here with
        # Claude Code's fresh session id from the --resume. That
        # would satisfy `session_id is not None` but fail equality.
        # Use the same polling helper because the write still lags
        # the result event — we wait for ANY non-null value and
        # then assert it matches.
        sid_after_fu2 = _wait_for_session_id(api_client, task_id)
        assert sid_after_fu2 == session_id_before_fu2, (
            'task.session_id changed across a same-profile '
            'follow-up. Every --resume would chain off a fresh '
            'transcript and lose prior context. '
            f'before={session_id_before_fu2} '
            f'after={sid_after_fu2}'
        )

        # Step 9: Task must reach `completed` after the follow-ups.
        # Historically follow-ups via /messages bypassed
        # `auto_run_task` and left tasks stuck at RUNNING; only
        # the conversation-stream `result` message (observed by
        # `_wait_for_result_message` above) transitioned. Now
        # `finalize_followup_session` in task_runner_auto.py runs
        # the same terminal pipeline as `auto_run_task` so status
        # should converge.
        deadline = time.time() + PIPELINE_TIMEOUT
        while time.time() < deadline:
            final_task = get_task(api_client, task_id)
            status = final_task.get('status')
            if status in ('completed', 'failed', 'waiting'):
                break
            time.sleep(POLL_INTERVAL)
        assert final_task['status'] == 'completed', (
            'Follow-up task should transition to completed after '
            'the agent finishes. '
            f'last status={final_task.get("status")!r}, '
            f'result={(final_task.get("result") or "")[:200]!r}'
        )
