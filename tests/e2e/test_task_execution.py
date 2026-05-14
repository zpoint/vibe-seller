"""
E2E test: Real agent pipeline -- create task via API, agent plans,
optionally asks questions, user answers, agent finishes.

Tests the full pipeline: pending -> designing -> planned -> running
-> completed.

Requires: provider credentials matching E2E_PROVIDER_MAP
plus ``claude`` CLI installed and available on PATH.

Marked with @pytest.mark.llm so they can be skipped when no API key.
"""

import json
import logging
import re
import threading
import time

import httpx
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    answer_question,
    build_smart_answers,
    create_store,
    create_task,
    get_task,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)

# -- Secret resolution ---------------------------------------------------

pytestmark = [pytest.mark.e2e]

# -- Fixtures --------------------------------------------------------------


@pytest.fixture(scope='module')
def test_store(api_client: httpx.Client) -> dict:
    """Create a store for the test module."""
    ts = int(time.time())
    return create_store(
        api_client,
        f'pipeline-test-{ts}',
        browser_backend='chrome',
    )


# -- Tests -----------------------------------------------------------------


class TestAgentPipeline:
    """Test the full agent pipeline via API calls."""

    def test_task_progresses_beyond_pending(
        self, api_client: httpx.Client, test_store: dict
    ):
        """Create a task and verify the agent starts working.

        The agent should move the task from 'pending' to at least
        'designing' within a reasonable time, proving the Claude
        CLI is connected and the pipeline is running.

        Also waits for the task to reach a terminal state so the
        agent session is freed before subsequent tests.
        """
        task = create_task(
            api_client,
            title='List the files in the current directory',
            store_id=test_store['id'],
            description=(
                'Use the Bash tool to run: ls. '
                'Do not use the browser. '
                'Do not ask questions.'
            ),
        )
        task_id = task['id']
        assert task['status'] == 'pending'

        logger.info(
            'Created task %s, waiting for agent to start',
            task_id[:8],
        )

        # Wait for it to move past 'pending' (store tasks may go
        # through queue: pending → queued → designing)
        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={
                'queued',
                'designing',
                'planned',
                'running',
                'completed',
                'failed',
            },
            timeout=60,
        )
        assert result['status'] not in ('pending',), (
            'Task should have progressed beyond pending'
        )
        logger.info(
            'Task %s progressed to: %s',
            task_id[:8],
            result['status'],
        )

        # Wait for terminal state so the agent session is
        # freed before the next test creates a store task.
        if result['status'] not in ('completed', 'failed'):
            poll_task_status(
                api_client,
                task_id,
                target_statuses={'completed', 'failed'},
                timeout=PIPELINE_TIMEOUT,
            )

    def test_full_pipeline_completes(
        self, api_client: httpx.Client, test_store: dict
    ):
        """Full pipeline + workspace symlink verification.

        Pre-creates a data file in the store workspace, then
        asks the agent to read it (through the stores/ symlink
        in the task working directory), find the max value, and
        write a result file.

        Tests:
        - Pipeline: pending → designing → planned → running
          → completed
        - Workspace: agent can read files through the stores/
          symlink in its task directory
        - Multi-step execution: read → process → write
        """
        store_slug = re.sub(
            r'[^a-z0-9-]', '-', test_store['name'].lower()
        ).strip('-')

        # Pre-create a data file in the store workspace
        data_path = f'stores/{store_slug}/numbers.txt'
        api_client.put(
            f'{BASE_URL}/api/workspace/file',
            params={'path': data_path},
            json={'content': '10\n45\n23\n67\n12\n'},
        ).raise_for_status()

        task = create_task(
            api_client,
            title='Read numbers and find max',
            store_id=test_store['id'],
            description=(
                f'Read stores/{store_slug}/numbers.txt '
                '(relative to your working directory), '
                'find the maximum number, and write it to '
                f'stores/{store_slug}/result.txt '
                'as a single line like "max=67". '
                'Do not use the browser. '
                'Do not ask questions.'
            ),
        )
        task_id = task['id']
        logger.info(
            'Created task %s for full pipeline test',
            task_id[:8],
        )

        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={'completed'},
            fail_statuses={'failed'},
            timeout=PIPELINE_TIMEOUT,
        )
        assert result['status'] == 'completed', (
            f'Task reached fail status "{result["status"]}" '
            f'(error: {result.get("error", "unknown")})'
        )

        # Verify the agent produced correct result
        result_path = f'stores/{store_slug}/result.txt'
        resp = api_client.get(
            f'{BASE_URL}/api/workspace/file',
            params={'path': result_path},
        )
        if resp.status_code == 200:
            content = resp.json().get('content', '')
            assert '67' in content, f'Expected 67 in result: {content}'

        logger.info(
            'Task %s finished with status: %s',
            task_id[:8],
            result['status'],
        )

    def test_status_transitions_are_valid(
        self, api_client: httpx.Client, test_store: dict
    ):
        """Track all status transitions and verify valid order.

        Valid transitions:
          pending -> [queued ->] designing -> planned -> running -> completed
          pending -> designing -> failed  (design failed)
          pending -> designing -> planned -> running -> failed
        """
        task = create_task(
            api_client,
            title='Say hello world',
            store_id=test_store['id'],
            description=(
                'Simply respond with "hello world". '
                'No browser automation needed. '
                'Do not ask any questions.'
            ),
        )
        task_id = task['id']

        # Auto mode: pending → running → completed.
        # Plan mode: pending → designing → planned → running.
        # 'queued' may appear briefly for either mode.
        valid_transitions = {
            'pending': {'queued', 'designing', 'running'},
            'queued': {'designing', 'running'},
            'designing': {
                'planned',
                'running',
                'completed',
                'failed',
            },
            'planned': {'running'},
            'running': {'completed', 'failed'},
        }
        terminal = {'completed', 'failed'}

        observed_statuses = ['pending']
        last_status = 'pending'
        start = time.time()

        while time.time() - start < PIPELINE_TIMEOUT:
            current = get_task(api_client, task_id)
            status = current['status']

            if status != last_status:
                logger.info(
                    'Task %s transition: %s -> %s',
                    task_id[:8],
                    last_status,
                    status,
                )
                # Validate transition
                allowed = valid_transitions.get(last_status, set())
                assert status in allowed, (
                    f'Invalid transition: '
                    f'{last_status} -> {status}. '
                    f'Allowed: {allowed}. '
                    f'History: {observed_statuses}'
                )
                observed_statuses.append(status)
                last_status = status

            if status in terminal:
                break

            time.sleep(POLL_INTERVAL)

        logger.info(
            'Task %s status history: %s',
            task_id[:8],
            ' -> '.join(observed_statuses),
        )

        # Should have at least progressed past pending
        assert len(observed_statuses) >= 2, (
            f'Expected at least 2 statuses, got: {observed_statuses}'
        )

    def test_task_without_store_completes(self, api_client: httpx.Client):
        """Create a store-independent task (no store_id) and verify
        the pipeline completes via plan mode (forced for non-store).
        """
        task = create_task(
            api_client,
            title='What is 2 + 2?',
            description=(
                'Answer: 4. That is all. '
                'Do not ask questions. Do not use browser.'
            ),
        )
        task_id = task['id']
        assert task['store_id'] is None
        assert task['plan_mode'] is True

        logger.info(
            'Created store-independent task %s (plan mode forced)',
            task_id[:8],
        )

        # Non-store tasks force plan_mode. The expected path is
        # `planned` → execute-plan → `completed`, but trivial
        # prompts (e.g. "what is 2+2") let the agent skip the plan
        # entirely — see CLAUDE.md § Plan-skipping. The state
        # machine transitions DESIGNING → COMPLETED in that case.
        # We accept either path: this test's contract is "the
        # task pipeline completes", not "the plan UI was rendered".
        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={'planned', 'completed', 'waiting'},
            fail_statuses={'failed'},
            timeout=PIPELINE_TIMEOUT,
        )

        if result['status'] == 'planned':
            # Approve the plan to trigger execution
            r = api_client.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan')
            assert r.status_code == 200

            # Wait for completion
            result = poll_task_status(
                api_client,
                task_id,
                target_statuses={'completed', 'waiting'},
                fail_statuses={'failed'},
                timeout=PIPELINE_TIMEOUT,
            )

        assert result['status'] in ('completed', 'waiting'), (
            f'Task reached fail status "{result["status"]}" '
            f'(error: {result.get("error", "unknown")})'
        )
        logger.info(
            'Store-independent task %s finished: %s',
            task_id[:8],
            result['status'],
        )


class TestAgentQuestionAnswer:
    """Test the question/answer flow where the agent asks the user
    something and the user responds.

    These tests are less deterministic since the LLM may or may
    not ask questions. We design prompts that strongly encourage
    questions.
    """

    def test_question_answer_flow(
        self, api_client: httpx.Client, test_store: dict
    ):
        """Create a self-contained task that triggers questions
        and can always complete after answers (no external deps).

        Uses a persistent SSE listener in a background thread
        (like the frontend) so question events are never missed.
        """
        task = create_task(
            api_client,
            title='Create a summary report',
            store_id=test_store['id'],
            description=(
                'Create a brief summary report about this '
                'store as a text file in the workspace. '
                'Do NOT use the browser for this task. '
                'I have not specified the format or what to '
                'include. Ask me what format and content I '
                'want before proceeding.'
            ),
        )
        task_id = task['id']
        logger.info(
            'Created question-trigger task %s',
            task_id[:8],
        )

        # Background SSE listener — mirrors frontend behavior.
        # Keeps a persistent connection so question events are
        # never missed, then auto-answers immediately.
        questions_answered = threading.Event()
        stop_listener = threading.Event()

        def _sse_listener():
            """Persistent SSE listener that auto-answers questions."""
            sse_client = httpx.Client(timeout=None)
            login(sse_client)
            try:
                with sse_client.stream(
                    'GET',
                    f'{BASE_URL}/api/sse',
                    timeout=PIPELINE_TIMEOUT,
                ) as resp:
                    for line in resp.iter_lines():
                        if stop_listener.is_set():
                            break
                        if not line or not line.startswith('data:'):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if (
                            event.get('type') == 'task_questions'
                            and event.get('task_id') == task_id
                        ):
                            request_id = event.get('request_id', '')
                            questions = event.get('questions', [])
                            logger.info(
                                'SSE: task %s question (request_id=%s)',
                                task_id[:8],
                                request_id[:8],
                            )
                            answer_question(
                                sse_client,
                                task_id,
                                request_id,
                                build_smart_answers(questions),
                            )
                            questions_answered.set()
                        if (
                            event.get('type') == 'agent_done'
                            and event.get('task_id') == task_id
                        ):
                            break
            except (httpx.ReadTimeout, httpx.ReadError):
                pass
            finally:
                sse_client.close()

        listener = threading.Thread(target=_sse_listener, daemon=True)
        listener.start()

        # Poll task status until terminal
        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={'completed', 'failed'},
            timeout=PIPELINE_TIMEOUT,
        )

        stop_listener.set()
        listener.join(timeout=5)

        logger.info(
            'Task %s final status: %s (questions_answered=%s)',
            task_id[:8],
            result['status'],
            questions_answered.is_set(),
        )


class TestKimiBashBug:
    """Regression test for kimi-k2.5 `: ` prefix bug.

    Kimi prepends `: ` to tool call parameters when the task uses
    multi-step numbered instructions ("Step 1: ... Step 2: ...").
    This affects all tool types (Bash, Read, Write) and causes the
    circuit breaker to kill the agent.

    Single-action tasks and flowing-sentence descriptions work fine.
    See PROFILING.md for full details.

    This test is marked xfail so it documents the bug without
    blocking CI. If kimi fixes this, the test will xpass and we
    can remove the xfail marker.
    """

    @pytest.mark.skip(
        reason=(
            'kimi-k2.5 `: ` prefix bug — flaky, fails ~50% '
            'under load. Run manually: E2E_PROVIDER_MAP=kimi '
            'docker compose run --rm e2e uv run pytest '
            'tests/e2e/test_task_execution.py::TestKimiBashBug '
            '-v --e2e -p no:randomly. See PROFILING.md'
        ),
    )
    def test_numbered_steps_bash_echo(
        self, api_client: httpx.Client, test_store: dict
    ):
        """Task with 'Step 1: echo ...' triggers kimi `: ` bug."""
        tag = int(time.time())
        task = create_task(
            api_client,
            title=f'Multi-step echo {tag}',
            store_id=test_store['id'],
            description=(
                f'Step 1: Run echo hello-{tag} in bash. '
                f'Step 2: Run echo world-{tag} in bash. '
                'Do not ask questions.'
            ),
        )
        result = poll_task_status(
            api_client,
            task['id'],
            target_statuses={'completed'},
            fail_statuses={'failed'},
            timeout=120,
        )
        assert result['status'] == 'completed'
