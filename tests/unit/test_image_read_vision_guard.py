"""A text-only model may not Read an image it cannot see.

Observed in CI on a text-only MiniMax model: the agent screenshotted an
ad console's dashboard, "read" the PNG, reported two campaigns with ids
that appear nowhere on the page, told the user their campaign id was
wrong, and ended the turn -- never opening the campaign list. The image
block was dropped before the model saw it and nothing said so, so the
description was invented. The guard turns that silent drop into a
refusal that says what to read instead.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK
from app.ai.image_guards import check_image_read_without_vision
from app.ai.profiles import model_sees_images

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ('model', 'expected'),
    [
        # text-only per catalog (DeepSeek's pricing doc: 图像理解 不支持)
        ('deepseek-v4-pro[1m]', False),
        ('deepseek-v4-pro', False),
        ('DeepSeek-V4-Pro', False),  # match is case-insensitive
        # live-verified vision (2026-10-29 red+blue probe)
        ('MiniMax-M3.1-Flash[1m]', True),
        ('MiniMax-M3.1-Flash', True),  # a [1m] suffix changes nothing
        # retired ids are unknown to the catalog -> no restriction
        ('MiniMax-M2.5', None),
        ('some-custom-model', None),  # unknown -> no restriction
        ('', None),
        (None, None),
    ],
)
def test_model_sees_images_follows_the_catalog(model, expected):
    assert model_sees_images(model) is expected


@pytest.mark.parametrize('path', ['/x/bh-tmp/shot.png', '/x/a.JPG', 'b.webp'])
def test_text_only_model_is_refused_an_image_read(path):
    deny = check_image_read_without_vision(
        'Read', {'file_path': path}, 'deepseek-v4-pro[1m]', False
    )
    assert deny and 'cannot see images' in deny
    assert 'innerText' in deny, 'the refusal must say what to read instead'


@pytest.mark.parametrize(
    ('tool', 'path', 'vision'),
    [
        ('Read', '/x/shot.png', True),  # a vision model reads it
        ('Read', '/x/shot.png', None),  # unknown model: left alone
        ('Read', '/x/notes.md', False),  # text files are always fine
        ('Bash', '/x/shot.png', False),  # only the Read tool is guarded
    ],
)
def test_everything_else_is_allowed(tool, path, vision):
    assert (
        check_image_read_without_vision(tool, {'file_path': path}, 'm', vision)
        is None
    )


def _hook_decision(model_env: dict, path: str) -> dict:
    s = AgentSession(task_id='test-task', prompt='test', mode='auto')
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': 'Read', 'tool_input': {'file_path': path}},
    }
    with patch(
        'app.ai.claude_backend_hooks.ProfileManager.get_env_for_profile',
        return_value=model_env,
    ):
        asyncio.run(s._handle_hook_callback('req-1', request))
    assert len(sent) == 1
    return sent[0]['hookSpecificOutput']


def test_hook_denies_a_screenshot_read_on_the_text_only_profile():
    out = _hook_decision(
        {'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]'}, '/t/shot.png'
    )
    assert out['permissionDecision'] == 'deny'
    assert 'deepseek-v4-pro[1m]' in out['permissionDecisionReason']


@pytest.mark.parametrize(
    ('env', 'path'),
    [
        ({'ANTHROPIC_MODEL': 'MiniMax-M3.1-Flash[1m]'}, '/t/shot.png'),
        ({}, '/t/shot.png'),  # plain Claude profile: no model override
        ({'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]'}, '/t/REPORT.md'),
    ],
)
def test_hook_allows_what_the_model_can_actually_read(env, path):
    assert _hook_decision(env, path)['permissionDecision'] == 'allow'
