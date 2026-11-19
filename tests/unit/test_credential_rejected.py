"""A rejected credential ends the run instead of 3+ minutes of retries.

Observed: a task on a profile whose API key the provider rejected sat at
"running" with nothing on screen while the CLI retried the 401 ten times
with backoff (187 s), then failed anyway. A rejected key does not
recover by waiting. The first rejection is let through — that is when
Claude Code refreshes an expired OAuth login — and the second ends the
run as ``auth_failed`` with a reason that names the profile.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_stream as stream_mod
import app.task_session_lifecycle as lifecycle

pytestmark = pytest.mark.unit


def _retry(error: str, status: int) -> dict:
    return {
        'type': 'system',
        'subtype': 'api_retry',
        'attempt': 1,
        'max_retries': 10,
        'retry_delay_ms': 600,
        'error_status': status,
        'error': error,
        'session_id': 's',
    }


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(stream_mod, 'meter_event', AsyncMock())
    s = AgentSession(
        task_id='task-cred', prompt='hi', mode='auto', profile_id='deepseek'
    )
    s.stop = AsyncMock()
    return s


async def test_second_rejection_stops_the_run(session):
    await session._handle_event(_retry('authentication_failed', 401))
    await asyncio.sleep(0)
    session.stop.assert_not_awaited()
    assert session._fatal_error is None

    # The observed run had a 503 between its 401s; still one rejection.
    await session._handle_event(_retry('server_error', 503))
    await session._handle_event(_retry('authentication_failed', 401))
    await asyncio.sleep(0)

    session.stop.assert_awaited_once()
    assert session._is_error_result is True
    assert session._error_category == 'auth_failed'
    assert 'deepseek' in session._fatal_error
    assert '401' in session._fatal_error

    # Later retries do not stop it twice.
    await session._handle_event(_retry('authentication_failed', 401))
    await asyncio.sleep(0)
    session.stop.assert_awaited_once()


async def test_other_retries_keep_retrying(session):
    for err, status in (('rate_limit', 429), ('server_error', 503)) * 3:
        await session._handle_event(_retry(err, status))
    await asyncio.sleep(0)
    session.stop.assert_not_awaited()
    assert session._fatal_error is None


def test_a_backend_ended_run_is_not_a_resume_failure():
    """A resumed run stopped for a rejected key exits non-zero with no
    result — the resume-failure shape. Retrying it fresh would meet the
    same 401 (and clear the error the run recorded)."""
    s = SimpleNamespace(
        _proc=SimpleNamespace(returncode=1),
        resume_session_id='sess-1',
        _result_text='',
        _fatal_error=None,
    )
    assert lifecycle._is_resume_failure(s) is True
    s._fatal_error = 'The AI provider rejected the credentials ...'
    assert lifecycle._is_resume_failure(s) is False
