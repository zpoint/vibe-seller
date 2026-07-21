"""ZiniaoBackend.stop() must terminate the browser ENV, not just the
proxy.

Before this fix, stop() only tore down the CDP mux — the Ziniao
Chromium env (and its accumulated tabs) lived until store deletion or
a machine reboot, which is why idle-browser sweeping was a no-op on
exactly the anti-detect backend. stop() now sends the per-store
``stopBrowser`` captured at start() — and ONLY that env, never the
shared Ziniao client (docs/ziniao-concurrency.md).
"""

import pytest

from app.browser import ziniao as ziniao_mod
from app.browser.base import BrowserSessionInfo
from app.browser.ziniao import ZiniaoBackend

pytestmark = pytest.mark.unit


class TestZiniaoStopSendsStopBrowser:
    async def test_stop_sends_per_store_stopbrowser(self, monkeypatch):
        calls = []

        async def _fake_connect(port, data, timeout=10):
            calls.append((port, data))
            return {'statusCode': '0'}, 'localhost'

        monkeypatch.setattr(ziniao_mod, 'try_connect_ziniao', _fake_connect)
        backend = ZiniaoBackend()
        backend._stop_data = {
            'action': 'stopBrowser',
            'browserOauth': 'demo-oauth-1',
        }
        backend._socket_port = 16851

        await backend.stop(BrowserSessionInfo())

        assert len(calls) == 1
        port, data = calls[0]
        assert port == 16851
        assert data['action'] == 'stopBrowser'
        assert data['browserOauth'] == 'demo-oauth-1'
        # One-shot: state cleared so a double-stop can't re-fire.
        assert backend._stop_data is None
        await backend.stop(BrowserSessionInfo())
        assert len(calls) == 1

    async def test_stop_without_start_is_quiet(self, monkeypatch):
        called = []

        async def _fake_connect(*a, **k):
            called.append(1)
            return None, None

        monkeypatch.setattr(ziniao_mod, 'try_connect_ziniao', _fake_connect)
        await ZiniaoBackend().stop(BrowserSessionInfo())
        assert called == []

    async def test_stopbrowser_failure_never_raises(self, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError('control app down')

        monkeypatch.setattr(ziniao_mod, 'try_connect_ziniao', _boom)
        backend = ZiniaoBackend()
        backend._stop_data = {'action': 'stopBrowser', 'browserId': '1'}
        backend._socket_port = 16851
        await backend.stop(BrowserSessionInfo())  # must not raise
