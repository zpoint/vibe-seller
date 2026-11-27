"""Upstream identity on reconnect (PR C1, from 84's customer question:
"can a store end up driving another store's browser?" — yes it could).

``_reconnect_upstream`` used to retry the SAME host:port up to 10 times
and accept whatever ``/json/version`` answered. Vendors rotate debugging
ports (Ziniao especially), so while store A's browser was down, store
B's fresh browser could land on A's port — and A's proxy attached to it,
putting A's agent inside B's store. The proxy now records the upstream
browser's GUID at first connect and refuses a reconnect to a DIFFERENT
browser, going straight to the per-store relaunch hook instead.
"""

import asyncio
from unittest import mock

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.cdp_mux_types import UpstreamIdentityMismatch
from app.browser.cdp_mux_upstream import browser_guid

# asyncio_mode=auto runs the async tests; an explicit asyncio mark here
# would poison the two sync tests below with a PytestWarning.
pytestmark = [pytest.mark.unit]


def _proxy(guid: str | None = None, relaunch=None) -> CDPMuxProxy:
    proxy = CDPMuxProxy(
        listen_port=0,
        target_port=0,
        relaunch_upstream=relaunch,
    )
    proxy._upstream_browser_id = guid
    proxy._running = True
    return proxy


class TestBrowserGuid:
    def test_extracts_the_guid(self):
        assert browser_guid('ws://x/devtools/browser/AAA-BBB') == 'AAA-BBB'

    def test_bare_url_gives_none(self):
        assert browser_guid('ws://x') is None


class TestIdentityCheck:
    async def test_same_guid_reconnects(self):
        """A websocket blip on the SAME browser reconnects unchanged."""
        proxy = _proxy(guid='guid-a')
        with (
            mock.patch.object(
                proxy,
                '_discover_ws_url',
                mock.AsyncMock(return_value='ws://h/devtools/browser/guid-a'),
            ),
            mock.patch(
                'app.browser.cdp_mux_upstream.websockets.connect',
                mock.AsyncMock(),
            ),
            mock.patch.object(
                proxy, '_startup_download_behavior', mock.AsyncMock()
            ),
            mock.patch('asyncio.create_task'),
        ):
            await proxy._connect_upstream()
        # no UpstreamIdentityMismatch raised — attached

    async def test_a_different_guid_is_refused(self):
        """The port answers with store B's browser while we fronted
        store A — an impostor, never attached to."""
        proxy = _proxy(guid='guid-a')
        with mock.patch.object(
            proxy,
            '_discover_ws_url',
            mock.AsyncMock(return_value='ws://h/devtools/browser/guid-b'),
        ):
            with pytest.raises(UpstreamIdentityMismatch):
                await proxy._connect_upstream()

    async def test_a_fresh_relaunch_target_is_accepted(self):
        """The relaunch hook clears the recorded id (its target is by
        construction THIS store's fresh browser) — connecting to it
        must re-record, not refuse."""
        proxy = _proxy(guid='guid-a')
        # The hook's flow: _relaunch_and_reconnect_upstream repointed the
        # target and cleared the identity; the connect then re-records.
        proxy._upstream_browser_id = None
        with (
            mock.patch.object(
                proxy,
                '_discover_ws_url',
                mock.AsyncMock(return_value='ws://h/devtools/browser/guid-new'),
            ),
            mock.patch(
                'app.browser.cdp_mux_upstream.websockets.connect',
                mock.AsyncMock(),
            ),
            mock.patch.object(
                proxy, '_startup_download_behavior', mock.AsyncMock()
            ),
            mock.patch('asyncio.create_task'),
        ):
            await proxy._connect_upstream()
        assert proxy._upstream_browser_id == 'guid-new'


class TestReconnectFlow:
    async def test_an_impostor_goes_straight_to_the_relaunch_hook(self):
        """Mismatch is not retried against the same port (10 attempts
        would only re-meet the impostor): the hook is called, and the
        proxy recovers on the hook's fresh target."""
        calls = {'relaunch': 0, 'connect': 0}
        relaunch_urls = iter([
            'ws://h/devtools/browser/guid-b',  # first attempt: impostor
            'ws://h:2/devtools/browser/guid-c',  # hook target: fresh
        ])

        async def _relaunch():
            calls['relaunch'] += 1
            return 2222, 'h'  # fresh port from the vendor

        proxy = _proxy(guid='guid-a', relaunch=_relaunch)

        async def _discover():
            return next(relaunch_urls)

        async def _connect(url, *a, **kw):
            calls['connect'] += 1
            return mock.AsyncMock()

        with (
            mock.patch.object(proxy, '_discover_ws_url', _discover),
            mock.patch(
                'app.browser.cdp_mux_upstream.websockets.connect', _connect
            ),
            mock.patch.object(
                proxy, '_startup_download_behavior', mock.AsyncMock()
            ),
            mock.patch('asyncio.create_task'),
        ):
            # First attempt meets the impostor; the loop must break to
            # the hook without burning the remaining 9 attempts.
            await proxy._reconnect_upstream()

        assert calls['relaunch'] == 1
        # The hook's target: identity cleared, then re-recorded.
        assert proxy.target_port == 2222
        assert proxy._upstream_browser_id == 'guid-c'
        assert proxy._running is True  # did not give up

    async def test_no_hook_means_give_up_without_attaching(self):
        """winchrome / inject (no relaunch hook): an impostor ends the
        proxy — recovery falls back to the manager's full restart on
        the next browser/start. Never an attach to the impostor."""
        proxy = _proxy(guid='guid-a')
        attached = []

        async def _connect(url, *a, **kw):
            attached.append(url)
            return mock.AsyncMock()

        with (
            mock.patch.object(
                proxy,
                '_discover_ws_url',
                mock.AsyncMock(return_value='ws://h/devtools/browser/guid-b'),
            ),
            mock.patch(
                'app.browser.cdp_mux_upstream.websockets.connect', _connect
            ),
        ):
            await proxy._reconnect_upstream()
            # give-up schedules stop() as a task — let it run.
            for _ in range(20):
                if not proxy._running:
                    break
                await asyncio.sleep(0.05)

        assert not proxy._running  # gave up — stopped, not attached
        assert attached == []  # NEVER attached to the impostor
