"""Unit test for CDPMuxProxy upstream self-heal.

The mux proxy runs *inside* the server process and connects upstream to
a backend browser's CDP debugging port. That port is not stable: after a
server restart the in-process proxy is torn down, and when tasks
re-dispatch the backend browser may have gone away or (for Ziniao)
relaunched on a DIFFERENT, rotated debugging port. A proxy that only ever
retries its ORIGINAL ``target_port`` reconnects forever against a dead
port and serves HTTP 502 on ``/json/version`` indefinitely — every
re-dispatched task then 502s, its daemon never creates a ``.sock``, and
the task thrashes and fails.

These tests pin the self-heal contract: when ordinary reconnection to the
current upstream is exhausted, the proxy invokes its injected
``relaunch_upstream`` hook to obtain a FRESH ``(port, host)``, repoints
itself, and reconnects — rather than giving up (which stops the proxy and
502s). They exercise the recovery path with a fully mocked upstream: no
real browser, no Ziniao client, no network, no backoff sleeps.
"""

import asyncio
from unittest import mock

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy

OLD_PORT = 9222
NEW_PORT = 9337  # rotated debugging port after a backend relaunch


def _make_proxy(relaunch=None):
    """A proxy wired to a target, never actually listening."""
    proxy = CDPMuxProxy(
        listen_port=0,
        target_port=OLD_PORT,
        relaunch_upstream=relaunch,
    )
    proxy._running = True
    return proxy


@pytest.mark.unit
class TestRelaunchAndReconnect:
    """The self-heal primitive: relaunch upstream, repoint, reconnect."""

    async def test_repoints_to_fresh_port_and_reconnects(self):
        relaunch = mock.AsyncMock(return_value=(NEW_PORT, '127.0.0.1'))
        proxy = _make_proxy(relaunch)
        proxy._connect_upstream = mock.AsyncMock()

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is True
        relaunch.assert_awaited_once()
        # Proxy now points at the fresh port the relaunch reported.
        assert proxy.target_port == NEW_PORT
        assert proxy.target_host == '127.0.0.1'
        proxy._connect_upstream.assert_awaited_once()

    async def test_bare_port_return_keeps_host(self):
        # Hook may return just a port (host unchanged).
        relaunch = mock.AsyncMock(return_value=NEW_PORT)
        proxy = _make_proxy(relaunch)
        original_host = proxy.target_host
        proxy._connect_upstream = mock.AsyncMock()

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is True
        assert proxy.target_port == NEW_PORT
        assert proxy.target_host == original_host

    async def test_no_hook_cannot_self_heal(self):
        proxy = _make_proxy(relaunch=None)
        proxy._connect_upstream = mock.AsyncMock()

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is False
        # No hook → must not touch the target or attempt a reconnect.
        assert proxy.target_port == OLD_PORT
        proxy._connect_upstream.assert_not_awaited()

    async def test_hook_returns_none_gives_up(self):
        # Backend cannot recover (e.g. WSL can't auto-relaunch Ziniao).
        relaunch = mock.AsyncMock(return_value=None)
        proxy = _make_proxy(relaunch)
        proxy._connect_upstream = mock.AsyncMock()

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is False
        assert proxy.target_port == OLD_PORT
        proxy._connect_upstream.assert_not_awaited()

    async def test_reconnect_failure_after_relaunch_reports_unhealed(self):
        relaunch = mock.AsyncMock(return_value=(NEW_PORT, '127.0.0.1'))
        proxy = _make_proxy(relaunch)
        proxy._connect_upstream = mock.AsyncMock(
            side_effect=ConnectionError('fresh port not up yet')
        )

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is False

    async def test_hook_exception_is_swallowed(self):
        relaunch = mock.AsyncMock(side_effect=RuntimeError('client wedged'))
        proxy = _make_proxy(relaunch)
        proxy._connect_upstream = mock.AsyncMock()

        healed = await proxy._relaunch_and_reconnect_upstream()

        assert healed is False
        proxy._connect_upstream.assert_not_awaited()


@pytest.mark.unit
class TestReconnectEscalatesToSelfHeal:
    """End-to-end: exhausted reconnect escalates instead of 502-ing."""

    async def test_stale_port_triggers_relaunch_not_permanent_stop(self):
        # Upstream reconnect succeeds only once the proxy points at the
        # FRESH port — i.e. retrying the stale port is hopeless, exactly
        # the post-restart / rotated-debuggingPort scenario.
        relaunch = mock.AsyncMock(return_value=(NEW_PORT, '127.0.0.1'))
        proxy = _make_proxy(relaunch)

        async def fake_connect():
            if proxy.target_port == OLD_PORT:
                raise ConnectionError('stale debugging port serves nothing')
            # Fresh port: connection succeeds.

        proxy._connect_upstream = mock.AsyncMock(side_effect=fake_connect)
        proxy.stop = mock.AsyncMock()

        # No real backoff waits.
        with mock.patch(
            'app.browser.cdp_mux_upstream.asyncio.sleep',
            new=mock.AsyncMock(),
        ):
            await proxy._reconnect_upstream()

        # Self-healed: repointed at the fresh port, never gave up (502).
        relaunch.assert_awaited_once()
        assert proxy.target_port == NEW_PORT
        proxy.stop.assert_not_called()
        assert proxy._reconnecting is False

    async def test_gives_up_when_self_heal_also_fails(self):
        # Relaunch can't recover → proxy stops (the honest terminal state,
        # not a silent forever-502).
        relaunch = mock.AsyncMock(return_value=None)
        proxy = _make_proxy(relaunch)
        proxy._connect_upstream = mock.AsyncMock(
            side_effect=ConnectionError('dead')
        )
        proxy.stop = mock.AsyncMock()

        with mock.patch(
            'app.browser.cdp_mux_upstream.asyncio.sleep',
            new=mock.AsyncMock(),
        ):
            await proxy._reconnect_upstream()
            # Let the create_task(self.stop()) give-up task run.
            await asyncio.sleep(0)

        relaunch.assert_awaited_once()
        proxy.stop.assert_called_once()
        assert proxy._reconnecting is False
