"""Unit tests for "ask new users to set up AI?" (app/ai/ai_readiness.py).

``claude`` is a stand-in script, so the machine's real login is never
read: the startup check, "another AI was added", and the default.
"""

import asyncio
import os
from pathlib import Path
import stat
import sys

import pytest

from app.ai import ai_readiness
from app.ai.profiles import ProfileManager

pytestmark = pytest.mark.unit

LOGGED_OUT = 'print(\'{"loggedIn": false, "authMethod": "none"}\'); sys.exit(1)'
LOGGED_IN = 'print(\'{"loggedIn": true, "authMethod": "claude.ai"}\')'


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Isolated profiles file, no mock CLI, fresh module state."""
    monkeypatch.setattr(
        'app.ai.profiles.PROFILES_PATH', tmp_path / 'profiles.json'
    )
    monkeypatch.setattr(ai_readiness, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.delenv('MOCK_CLI', raising=False)
    ai_readiness.reset()
    yield monkeypatch
    ai_readiness.reset()


def _use_cli(env, tmp_path: Path, body: str) -> None:
    script = tmp_path / 'claude'
    script.write_text(f'#!{sys.executable}\nimport sys, time\n{body}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env.setattr(ai_readiness, 'resolve_claude_binary', lambda: str(script))


async def _after_startup_check() -> bool:
    await ai_readiness.run_startup_check()
    return await ai_readiness.ai_ready()


async def test_default_is_not_ready(env):
    # No startup check has answered: the prompt shows.
    assert await ai_readiness.ai_ready() is False


async def test_logged_in_at_startup_is_ready(env, tmp_path):
    _use_cli(env, tmp_path, LOGGED_IN)
    assert await _after_startup_check() is True


async def test_logged_out_at_startup_is_not_ready(env, tmp_path):
    _use_cli(env, tmp_path, LOGGED_OUT)  # exits 1: the JSON decides
    assert await _after_startup_check() is False


@pytest.mark.parametrize(
    'body',
    [
        'print("this is not json")',
        'sys.stderr.write("unknown command auth"); sys.exit(2)',
    ],
)
async def test_cli_that_cannot_say_is_not_ready(env, tmp_path, body):
    _use_cli(env, tmp_path, body)
    assert await _after_startup_check() is False


async def test_missing_cli_is_not_ready(env, tmp_path):
    env.setattr(
        ai_readiness,
        'resolve_claude_binary',
        lambda: str(tmp_path / 'no-such-claude'),
    )
    assert await _after_startup_check() is False


async def test_another_ai_added_is_ready_without_login(env, tmp_path):
    _use_cli(env, tmp_path, LOGGED_OUT)
    assert await _after_startup_check() is False
    ProfileManager.create_profile(
        'deepseek',
        'DeepSeek',
        {
            'ANTHROPIC_BASE_URL': 'https://example.invalid/anthropic',
            'ANTHROPIC_AUTH_TOKEN': 'sk-test',
        },
    )
    assert await ai_readiness.ai_ready() is True


async def test_mock_cli_is_ready(env):
    env.setenv('MOCK_CLI', 'mock.py')
    assert await ai_readiness.ai_ready() is True


async def test_hung_cli_is_killed(env, tmp_path):
    # A shell stand-in that records its pid and ``exec``s sleep (same
    # pid). The timeout leaves room for the spawn under a loaded run.
    pidfile = tmp_path / 'pid'
    binary = tmp_path / 'claude'
    binary.write_text(f'#!/bin/sh\necho $$ > {pidfile}\nexec sleep 60\n')
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    env.setattr(ai_readiness, 'resolve_claude_binary', lambda: str(binary))
    env.setattr(ai_readiness, 'PROBE_TIMEOUT_S', 3.0)
    assert await _after_startup_check() is False
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_probe_uses_the_default_profile_env(env, tmp_path):
    # The probe sees the env a default-profile task is spawned with.
    _use_cli(
        env,
        tmp_path,
        'import os, json\n'
        "print(json.dumps({'loggedIn': os.environ.get('PROBE_MARK') == 'x'}))",
    )
    real = ProfileManager.get_env_for_profile
    env.setattr(
        ProfileManager,
        'get_env_for_profile',
        staticmethod(lambda pid: {**real(pid), 'PROBE_MARK': 'x'}),
    )
    assert await _after_startup_check() is True


async def test_runs_once_at_scheduler_start(env, tmp_path):
    # Registered by start_scheduler as a one-shot job: it fires right
    # away, in the background, and is gone afterwards.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: PLC0415

    _use_cli(env, tmp_path, LOGGED_IN)
    scheduler = AsyncIOScheduler()
    scheduler.start()
    try:
        ai_readiness.register_ai_setup_check(scheduler)
        for _ in range(100):
            if await ai_readiness.ai_ready():
                break
            await asyncio.sleep(0.05)
        assert await ai_readiness.ai_ready() is True
        assert scheduler.get_job('ai_setup_check') is None  # ran once
    finally:
        scheduler.shutdown(wait=False)
