"""No external MCP tool may be asked to outlive the client's call timeout.

Qoder fails every tool call at 60 s, and Codex does by default. wait_task
advertised (and accepted) waits of up to 120 s, so a model that asked for
the advertised maximum got a failed call every time — the operator saw a
column of red crosses while the task was fine. These tests pin the
ceiling of every duration argument below that timeout, for the tools
registered today and any added later.
"""

import pytest

from app.agent_mcp import tools  # noqa: F401  registers every tool
from app.agent_mcp.registry import tool_definitions
from app.agent_mcp.support import (
    CLIENT_TOOL_CALL_TIMEOUT_SECONDS,
    DEFAULT_WAIT_SECONDS,
    MAX_WAIT_SECONDS,
)

pytestmark = pytest.mark.unit

# Headroom for the final poll, the guidance lookup and the response.
_HEADROOM_SECONDS = 5


def _duration_args():
    for definition in tool_definitions():
        props = definition['inputSchema'].get('properties', {})
        for arg, spec in props.items():
            if 'second' in arg or 'timeout' in arg:
                yield definition['name'], arg, spec


def test_long_poll_tools_are_found():
    # Guards the scan below from passing vacuously after a rename.
    names = {name for name, _, _ in _duration_args()}
    assert {'wait_task', 'workspace_assistant'} <= names


@pytest.mark.parametrize(
    'name,arg,spec',
    list(_duration_args()),
    ids=lambda v: v if isinstance(v, str) else '',
)
def test_duration_ceiling_fits_client_timeout(name, arg, spec):
    ceiling = spec.get('maximum')
    assert ceiling is not None, f'{name}.{arg} has no maximum'
    assert ceiling <= CLIENT_TOOL_CALL_TIMEOUT_SECONDS - _HEADROOM_SECONDS, (
        f'{name}.{arg} allows {ceiling}s; clients fail calls at '
        f'{CLIENT_TOOL_CALL_TIMEOUT_SECONDS}s'
    )


def test_default_wait_within_ceiling():
    assert DEFAULT_WAIT_SECONDS <= MAX_WAIT_SECONDS
