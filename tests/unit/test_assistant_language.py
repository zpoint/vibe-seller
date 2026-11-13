"""Assistant language directive (observed while taking the docs shots).

The assistant replied in ENGLISH to a Chinese 「从这个链接安装这个技
能：…」 message. The task prompts carry an explicit server-side
language hint (`build_system_extra` → `detect_language_hint`,
app/task_runner.py:188, app/task_runner_context.py:108) — the
assistant route (app/routers/workspace_assistant.py `_build_system_prompt`)
gets only the static file, so its language rule must itself be an
unmissable directive. Pinned here.
"""

import pytest

from app.prompts import WORKSPACE_ASSISTANT_PROMPT

pytestmark = pytest.mark.unit


def test_assistant_prompt_directs_the_users_language():
    p = WORKSPACE_ASSISTANT_PROMPT
    assert "Answer in the user's language" in p
    assert 'Chinese' in p  # the observed failure was zh → English
    assert 'Never reply' in p
