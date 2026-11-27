"""Unit test for CDPMuxProxy download path rewriting.

Exercises the real `_route_client_message` path and asserts on the
message actually forwarded upstream (captured via a stub
`_send_upstream`).  This catches regressions where the rewrite is
removed or broken in the production code path.
"""

import json as _json
import logging
from unittest import mock

import pytest

from app.browser import cdp_mux_upstream as cdp_mux_upstream_module
from app.browser.cdp_mux_proxy import CDPMuxProxy


@pytest.mark.unit
class TestSetDownloadBehaviorAnsweredLocally:
    """F2: the pin is IMMORTAL — a client's set*DownloadBehavior is
    answered locally, never forwarded.

    Chrome ties the browser-wide download behaviour to the DevTools
    SESSION that last set it. The browser_harness daemon relay scopes
    every non-Target call to its page session, so a forwarded agent
    call — even with its path rewritten — transfers pin ownership to a
    mortal tab; when that session ends Chrome resets to the profile
    default and every later download leaves the store dir (harness
    lane F2, proven live). Answering locally keeps the setting owned
    by the mux's persistent root session (the startup pin).
    """

    def _make_proxy(self, download_dir=None):
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=download_dir,
        )
        proxy._forwarded = []
        proxy._answered = []

        async def _capture_upstream(msg):
            proxy._forwarded.append(msg)

        async def _capture_client(text):
            proxy._answered.append(_json.loads(text))

        proxy._send_upstream = _capture_upstream

        class _Ws:
            async def send(self, text):
                await _capture_client(text)

        class _Client:
            ws = _Ws()

        proxy._clients = {'client-a': _Client()}
        return proxy

    async def test_browser_call_answered_not_forwarded(self):
        """(1) Browser.* from a client: Chrome's success shape comes
        back to THAT client, and nothing goes upstream."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        msg = {
            'id': 5,
            'method': 'Browser.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/home/other/.vibe-seller/downloads/x',
                'eventsEnabled': True,
            },
        }
        await proxy._route_client_message('client-a', _json.dumps(msg))

        assert proxy._forwarded == []
        assert len(proxy._answered) == 1
        assert proxy._answered[0] == {'id': 5, 'result': {}}

    async def test_page_call_answered_with_session_echo(self):
        """(2) Page.* — same, and a session-scoped call gets the
        sessionId echoed back, matching Chrome's response shape."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'sessionId': 'SES1',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/tmp/elsewhere',
            },
        }
        await proxy._route_client_message('client-a', _json.dumps(msg))

        assert proxy._forwarded == []
        assert proxy._answered == [{'id': 7, 'result': {}, 'sessionId': 'SES1'}]

    async def test_pin_never_sent_on_a_page_session(self):
        """(3) the swallow is total: after a client's call (and even
        its disconnect + grace), no setDownloadBehavior of ANY kind
        ever carries a sessionId upstream — the only upstream pin is
        the sessionless startup one, so no mortal session can own it."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        for method in (
            'Browser.setDownloadBehavior',
            'Page.setDownloadBehavior',
        ):
            msg = {
                'id': 1,
                'method': method,
                'sessionId': 'SES-MORTAL',
                'params': {'behavior': 'allow', 'downloadPath': '/x'},
            }
            await proxy._route_client_message('client-a', _json.dumps(msg))
        # Simulate the client's session dying (grace cleanup).
        proxy._session_to_client.pop('SES-MORTAL', None)
        proxy._clients.pop('client-a', None)
        # A NEW client's call is swallowed just the same.
        msg = {
            'id': 2,
            'method': 'Browser.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': '/y'},
        }
        await proxy._route_client_message('client-b', _json.dumps(msg))

        # The only upstream setDownloadBehavior ever sent is the
        # sessionless startup pin.
        await proxy._startup_download_behavior()
        upstream_setdb = [
            m
            for m in proxy._forwarded
            if 'setDownloadBehavior' in str(m.get('method', ''))
        ]
        assert len(upstream_setdb) == 1
        assert 'sessionId' not in upstream_setdb[0]
        assert upstream_setdb[0]['params']['downloadPath'] == '/pin/acme'

    async def test_logged_once_per_answered_call(self, caplog):
        """(4) one INFO line per answered call, naming the client and
        the requested path."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        for i, path in enumerate(('/a', '/b')):
            msg = {
                'id': i + 1,
                'method': 'Browser.setDownloadBehavior',
                'params': {'behavior': 'allow', 'downloadPath': path},
            }
            with caplog.at_level(logging.INFO, logger='app.browser'):
                await proxy._route_client_message('client-a', _json.dumps(msg))
        assert caplog.text.count('answered locally') == 2
        assert '/a' in caplog.text and '/b' in caplog.text

    async def test_no_pin_forwarded_unchanged(self):
        """(5) no download_dir (aux-style proxies): the call is
        forwarded exactly as the client sent it — nothing to protect."""
        proxy = self._make_proxy(download_dir=None)
        msg = {
            'id': 9,
            'method': 'Browser.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': '/anywhere'},
        }
        await proxy._route_client_message('client-a', _json.dumps(msg))

        assert len(proxy._forwarded) == 1
        forwarded = proxy._forwarded[0]
        assert forwarded['method'] == 'Browser.setDownloadBehavior'
        assert forwarded['params']['downloadPath'] == '/anywhere'
        assert forwarded['id'] != 9  # rewritten id, not the local answer
        assert proxy._answered == []

    async def test_watchdog_tmp_dir_call_is_swallowed(self):
        """browser-use's DownloadsWatchdog sets a random /tmp dir per
        daemon start. Under the immortal-pin contract that call is
        answered locally (never forwarded, never applied): the
        deterministic-downloads behaviour now comes from the startup
        pin ALONE, and the watchdog's success response keeps its
        click-with-download-detection flow alive."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        msg = {
            'id': 1,
            'method': 'Browser.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/tmp/browser-use-downloads-abc123/',
                'eventsEnabled': True,
            },
        }
        await proxy._route_client_message('client-a', _json.dumps(msg))
        assert proxy._forwarded == []
        assert proxy._answered == [{'id': 1, 'result': {}}]

    async def test_no_id_still_swallowed(self):
        """An event-shaped call with no id: swallowed silently, still
        nothing upstream."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        msg = {
            'method': 'Browser.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': '/x'},
        }
        await proxy._route_client_message('client-a', _json.dumps(msg))
        assert proxy._forwarded == []
        assert proxy._answered == []


@pytest.mark.unit
class TestDownloadEventFanOutUnchanged:
    """Q-EVENTS: root-session download events keep reaching clients.

    browser-use's DownloadsWatchdog waits on
    Browser.downloadWillBegin/downloadProgress (its
    click-with-download-detection resolves on them), and with F2 the
    events' enabler is always the mux's root session (the startup pin
    sets eventsEnabled). The mux's EXISTING broadcast must keep
    fanning those out — a client must never hang waiting for an event
    it no longer gets.
    """

    def _make_proxy(self):
        proxy = CDPMuxProxy(listen_port=0, target_port=0)
        proxy._fanout = []
        names = {}

        async def _capture_client(client, msg):
            proxy._fanout.append((names[id(client)], msg))

        proxy._send_client = _capture_client
        clients = {}
        for cid in ('client-a', 'client-b'):
            c = object()
            names[id(c)] = cid
            clients[cid] = c
        proxy._clients = clients
        return proxy

    async def test_browser_download_events_broadcast_to_all_clients(self):
        proxy = self._make_proxy()
        event = {
            'method': 'Browser.downloadWillBegin',
            'params': {'frameId': 'F1', 'guid': 'G1'},
        }
        await proxy._route_upstream_message(_json.dumps(event))

        recipients = sorted(cid for cid, _ in proxy._fanout)
        assert recipients == ['client-a', 'client-b']
        assert all(m == event for _, m in proxy._fanout)

    async def test_download_progress_also_broadcast(self):
        proxy = self._make_proxy()
        event = {
            'method': 'Browser.downloadProgress',
            'params': {'guid': 'G1', 'state': 'completed'},
        }
        await proxy._route_upstream_message(_json.dumps(event))
        assert len(proxy._fanout) == 2


@pytest.mark.unit
class TestProactiveDownloadBehavior:
    """Proxy should set download path on upstream connect — not wait for a
    client to send it. Closes a race where early clicks land in Chrome's
    default (Ziniao native) dir instead of the per-store dir.

    The method is fire-and-forget: it sends the CDP request and returns
    immediately. It must NOT read from ``self._upstream`` because
    ``_read_upstream`` hasn't started yet — a recv loop would steal
    unsolicited browser events and (on reconnect) responses to
    still-in-flight client requests.
    """

    def _make_proxy(self, download_dir):
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=download_dir,
        )
        proxy._forwarded = []

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._send_upstream = _capture

        # Guard: assert the method never tries to recv() from upstream.
        # If it did, the missing attribute would raise and fail the test.
        proxy._upstream = None
        return proxy

    async def test_startup_sends_setDownloadBehavior(self):
        proxy = self._make_proxy(download_dir='/stable/path')
        await proxy._startup_download_behavior()

        assert len(proxy._forwarded) == 1
        m = proxy._forwarded[0]
        assert m['method'] == 'Browser.setDownloadBehavior'
        assert m['params']['behavior'] == 'allow'
        assert m['params']['downloadPath'] == '/stable/path'
        assert m['params']['eventsEnabled'] is True

    async def test_startup_noop_when_download_dir_none(self):
        proxy = self._make_proxy(download_dir=None)
        await proxy._startup_download_behavior()
        assert proxy._forwarded == []

    async def test_startup_does_not_read_upstream(self):
        """Regression guard: reading from upstream would steal events
        belonging to clients (especially on reconnect)."""

        class _BlowUpOnRecv:
            async def recv(self):
                raise AssertionError(
                    '_startup_download_behavior must not call recv()'
                )

            async def send(self, *_a, **_kw):
                pass

        proxy = self._make_proxy(download_dir='/stable/path')
        proxy._upstream = _BlowUpOnRecv()
        # Should complete without touching recv
        await proxy._startup_download_behavior()
        assert len(proxy._forwarded) == 1


@pytest.mark.unit
class TestPinOnEveryUpstreamConnect:
    """Z1: the download pin rides EVERY upstream connection.

    Ziniao's kernel defaults downloads to its own data dir; the pin on
    first connect AND on every reconnect is what keeps files landing in
    the store folder. #350 does not touch this code — these tests pin
    it so the routing change can never silently drop the upstream-side
    pin.
    """

    def _make_proxy(self, download_dir):
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=download_dir,
        )
        proxy._forwarded = []

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._send_upstream = _capture
        proxy._upstream = None
        return proxy

    async def test_first_connect_pins(self):
        proxy = self._make_proxy(download_dir='/pin/acme')
        await proxy._startup_download_behavior()
        assert len(proxy._forwarded) == 1
        m = proxy._forwarded[0]
        assert m['method'] == 'Browser.setDownloadBehavior'
        assert m['params']['downloadPath'] == '/pin/acme'

    async def test_reconnect_pins_again(self):
        """The reconnect path (_connect_upstream) re-sends the pin —
        a browser the proxy re-attached to never keeps a stale
        download dir. Stub the socket + discovery, run the REAL
        reconnect method, and capture what it sends."""
        proxy = self._make_proxy(download_dir='/pin/acme')
        calls = []

        class _FakeWs:
            async def send(self, msg):
                calls.append(_json.loads(msg))

        class _FakeConnect:
            def __await__(self):
                async def _coro():
                    return _FakeWs()

                return _coro().__await__()

        # The pin rides _send_upstream (stubbed below to _forwarded);
        # calls captures anything the raw socket would have seen.

        async def _fake_discover():
            return 'ws://ignored'

        async def _fake_read_upstream():
            return None

        with (
            mock.patch.object(cdp_mux_upstream_module, 'websockets') as fake_ws,
            mock.patch.object(proxy, '_discover_ws_url', _fake_discover),
            mock.patch.object(proxy, '_read_upstream', _fake_read_upstream),
        ):
            fake_ws.connect = lambda *a, **kw: _FakeConnect()
            await proxy._connect_upstream()

        pins = [
            c
            for c in proxy._forwarded
            if c.get('method') == 'Browser.setDownloadBehavior'
        ]
        assert len(pins) == 1
        assert pins[0]['params']['downloadPath'] == '/pin/acme'
