"""Unit tests for the Mac inspector session (SIGUSR1 trigger only).

The base open()/evaluate() machinery (lock, busy refusal, pid check,
stale-target retry) is shared with Windows and covered by
``test_machine_lock`` / ``test_inject_contract``; here we pin the ONLY
Mac-specific behaviour: the trigger sends SIGUSR1 through the seam and
returns, and a silent 9229 still surfaces as ``InspectorError``.
"""

from unittest import mock

import pytest

from app.browser import win_inspector
import app.browser.mac_inspector as mod
from app.browser.mac_inspector import MacInspectorSession
from app.browser.win_inspector import InspectorError

pytestmark = pytest.mark.unit


async def test_trigger_sends_sigusr1_to_the_given_pid(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, 'send_usr1', sent.append)
    session = MacInspectorSession()
    await session._trigger(4242)
    assert sent == [4242]


async def test_trigger_maps_a_gone_target_to_inspector_error(monkeypatch):
    """A stale pid (client died between the caller reading main_pid and
    the signal) must surface as the typed InspectorError, not an
    untyped ProcessLookupError."""

    def gone(pid):
        raise ProcessLookupError()

    monkeypatch.setattr(mod, 'send_usr1', gone)
    session = MacInspectorSession()
    with pytest.raises(InspectorError) as ei:
        await session._trigger(7)
    assert 'gone' in str(ei.value)


async def test_trigger_goes_through_the_seam_not_os_kill(monkeypatch):
    """The seam is the boundary: patching ``send_usr1`` must intercept
    the signal (negative control for reaching past it to os.kill)."""
    direct = []
    monkeypatch.setattr(mod.os, 'kill', lambda *a: direct.append(a))
    monkeypatch.setattr(mod, 'send_usr1', lambda pid: None)
    session = MacInspectorSession()
    await session._trigger(99)
    assert direct == []


async def test_open_raises_inspector_error_when_9229_stays_silent(
    monkeypatch,
):
    session = MacInspectorSession()
    monkeypatch.setattr(mod, 'send_usr1', lambda pid: None)
    with (
        mock.patch.object(
            session, '_discover_ws', new=mock.AsyncMock(return_value=None)
        ),
        mock.patch.object(win_inspector.asyncio, 'sleep', new=mock.AsyncMock()),
    ):
        with pytest.raises(InspectorError):
            await session.open(1234)


def test_constructor_passes_a_stub_exe_path_script():
    """The Windows trigger needs the PowerShell preamble; the Mac one
    must not (a stub keeps the shared constructor working)."""
    session = MacInspectorSession()
    assert session._exe_path_script() == ''
