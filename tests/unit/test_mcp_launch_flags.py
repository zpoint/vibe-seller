"""One launch-flag decision for MCP configs — task AND assistant.

The assistant once always passed ``--strict-mcp-config``; a profile
with ``load_global_mcp=True`` therefore LOST its global MCP servers in
the assistant — a regression against the profile setting the task
launch has always honored (``app/ai/claude_backend.py``: strict only
when globals are off, else the project ``.mcp.json`` rides cwd
auto-discovery). ``mcp_launch_flags`` is the single composition both
launches call; these pin both sides.
"""

from pathlib import Path

import pytest

from app.ai.claude_backend_utils import mcp_launch_flags

pytestmark = pytest.mark.unit


def test_task_globals_off_is_strict_on_the_project_config():
    cfg = Path('/ws/.mcp.json')
    flags = mcp_launch_flags(
        load_global=False, session_config=None, project_config=cfg
    )
    assert flags == ['--mcp-config', str(cfg), '--strict-mcp-config']


def test_task_globals_on_needs_no_flags():
    """The project .mcp.json is auto-discovered from the cwd; no
    flags means globals AND project servers both load."""
    assert (
        mcp_launch_flags(
            load_global=True,
            session_config=None,
            project_config=Path('/ws/.mcp.json'),
        )
        == []
    )


def test_assistant_globals_off_is_strict_on_the_session_file():
    cfg = Path('/data/system-prompts/__ws_assistant_u__.mcp.json')
    flags = mcp_launch_flags(
        load_global=False, session_config=cfg, project_config=None
    )
    assert flags == ['--mcp-config', str(cfg), '--strict-mcp-config']


def test_assistant_globals_on_passes_the_session_file_unmerged():
    """THE regression pin: the assistant's per-session skill-tools
    file still rides along (it is not auto-discovered), but WITHOUT
    --strict-mcp-config, so the profile's global servers merge in."""
    cfg = Path('/data/system-prompts/__ws_assistant_u__.mcp.json')
    flags = mcp_launch_flags(
        load_global=True, session_config=cfg, project_config=None
    )
    assert flags == ['--mcp-config', str(cfg)]
    assert '--strict-mcp-config' not in flags


def test_no_config_at_all_is_no_flags():
    assert (
        mcp_launch_flags(
            load_global=False, session_config=None, project_config=None
        )
        == []
    )


def test_mock_lane_assistant_has_no_config():
    """MOCK_CLI sessions build no mcp_config — no flags either way."""
    assert (
        mcp_launch_flags(
            load_global=True, session_config=None, project_config=None
        )
        == []
    )


def test_session_and_project_the_same_file_is_not_duplicated():
    cfg = Path('/ws/.mcp.json')
    assert (
        mcp_launch_flags(
            load_global=True, session_config=cfg, project_config=cfg
        )
        == []
    )
