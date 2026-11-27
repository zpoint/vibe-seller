"""Unit test for CDPMuxProxy download path rewriting.

Exercises the real `_route_client_message` path and asserts on the
message actually forwarded upstream (captured via a stub
`_send_upstream`).  This catches regressions where the rewrite is
removed or broken in the production code path.
"""

import json
import logging
import os
from unittest import mock

import pytest

from app.browser import cdp_mux_upstream as cdp_mux_upstream_module
from app.browser.cdp_mux_proxy import CDPMuxProxy


@pytest.mark.unit
class TestDownloadPathRewrite:
    """Test setDownloadBehavior interception via _route_client_message."""

    def _make_proxy(self, download_dir=None):
        """Create a proxy instance (not started — no sockets).

        Stubs `_send_upstream` to record what would be forwarded,
        so we can assert on the real message that goes to the browser.
        """
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=download_dir,
        )
        proxy._forwarded = []

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._send_upstream = _capture
        return proxy

    async def test_rewrite_when_download_dir_set(self):
        proxy = self._make_proxy(download_dir='/stable/path')
        msg = {
            'id': 1,
            'method': 'Browser.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/tmp/browser-use-downloads-abc123/',
                'eventsEnabled': True,
            },
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        assert len(proxy._forwarded) == 1
        forwarded = proxy._forwarded[0]
        assert forwarded['method'] == 'Browser.setDownloadBehavior'
        assert forwarded['params']['downloadPath'] == '/stable/path'
        # Other params untouched
        assert forwarded['params']['behavior'] == 'allow'
        assert forwarded['params']['eventsEnabled'] is True

    async def test_no_rewrite_when_download_dir_none(self):
        proxy = self._make_proxy(download_dir=None)
        original_path = '/tmp/browser-use-downloads-abc123/'
        msg = {
            'id': 1,
            'method': 'Browser.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': original_path,
                'eventsEnabled': True,
            },
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        assert proxy._forwarded[0]['params']['downloadPath'] == original_path

    async def test_other_methods_untouched(self):
        proxy = self._make_proxy(download_dir='/stable/path')
        msg = {
            'id': 1,
            'method': 'Target.createTarget',
            'params': {'url': 'about:blank'},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        assert proxy._forwarded[0]['params'] == {'url': 'about:blank'}

    async def test_no_crash_when_params_missing_download_path(self):
        proxy = self._make_proxy(download_dir='/stable/path')
        msg = {
            'id': 1,
            'method': 'Browser.setDownloadBehavior',
            'params': {'behavior': 'deny'},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        # Missing downloadPath key should NOT be added
        assert 'downloadPath' not in forwarded['params']
        assert forwarded['params']['behavior'] == 'deny'


@pytest.mark.unit
class TestPageDownloadBehaviorRewrite:
    """``Page.setDownloadBehavior`` must be pinned like ``Browser.*``.

    A live task agent followed a doc's hardcoded default-home path on a
    multi-home machine: it mkdir'd the dir in ANOTHER server's home and
    overrode the mux pin with ``Page.setDownloadBehavior`` — the one
    spelling the old rewrite did not cover. Downloads then landed
    outside this server's tree entirely.
    """

    def _make_proxy(self, download_dir=None):
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=download_dir,
        )
        proxy._forwarded = []

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._send_upstream = _capture
        return proxy

    async def test_page_override_rewritten_to_pin(self):
        """The incident, verbatim: an outside downloadPath (here the
        default-home spelling of another server's tree) must end up in
        the pinned dir."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/home/other/.vibe-seller/downloads/acme',
            },
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        assert forwarded['method'] == 'Page.setDownloadBehavior'
        assert forwarded['params']['downloadPath'] == '/srv-home/downloads/acme'
        assert forwarded['params']['behavior'] == 'allow'

    async def test_page_override_logged(self, caplog):
        """The rewrite is visible in the server log — the audit trail
        for 'an agent tried to redirect downloads'."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/tmp/elsewhere',
            },
        }
        with caplog.at_level(logging.INFO, logger='app.browser'):
            await proxy._route_client_message('client-a', json.dumps(msg))

        assert 'outside the pinned dir' in caplog.text
        assert '/tmp/elsewhere' in caplog.text

    async def test_page_matching_path_passthrough_silent(self, caplog):
        """A downloadPath already equal to the pin (any ~ / trailing
        slash spelling) passes through untouched and unlogged."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/srv-home/downloads/acme/',
            },
        }
        with caplog.at_level(logging.INFO, logger='app.browser'):
            await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        # Unchanged — the matching spelling is passed through verbatim
        # (trailing slash and all); only a mismatch is rewritten.
        assert (
            forwarded['params']['downloadPath'] == '/srv-home/downloads/acme/'
        )
        assert 'outside the pinned dir' not in caplog.text

    async def test_page_deny_without_path_untouched(self):
        """``behavior: deny`` with no downloadPath is a 'do not
        download' choice, not a redirect — never rewritten."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'deny'},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        assert 'downloadPath' not in forwarded['params']
        assert forwarded['params']['behavior'] == 'deny'

    async def test_page_no_pin_passthrough(self):
        """No pinned dir (``download_dir=None``) → nothing enforced."""
        proxy = self._make_proxy(download_dir=None)
        original = '/home/other/.vibe-seller/downloads/acme'
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': original},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        assert proxy._forwarded[0]['params']['downloadPath'] == original

    async def test_page_subdir_of_pin_untouched(self, caplog):
        """A flow that points downloads at a SUBDIRECTORY of the pin
        and waits for the file exactly there keeps working — the
        rewrite must never move a file away from where a legitimate
        flow waits for it (single-install no-regression rule)."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        sub = '/srv-home/downloads/acme/run-2026-11'
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': sub},
        }
        with caplog.at_level(logging.INFO, logger='app.browser'):
            await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        assert forwarded['params']['downloadPath'] == sub
        assert 'outside the pinned dir' not in caplog.text

    async def test_browser_subdir_of_pin_untouched(self, caplog):
        """Same shape through Browser.setDownloadBehavior (a
        browser-wide flow may equally target a subfolder)."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        sub = '/srv-home/downloads/acme/exports'
        msg = {
            'id': 7,
            'method': 'Browser.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': sub},
        }
        with caplog.at_level(logging.INFO, logger='app.browser'):
            await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        assert forwarded['params']['downloadPath'] == sub
        assert 'outside the pinned dir' not in caplog.text

    async def test_tilde_spelled_pin_untouched(self, caplog, tmp_path):
        """The pin spelled with ``~`` (what a default-home agent types)
        resolves inside and is left alone."""
        home = tmp_path
        pin = home / '.vibe-seller' / 'downloads' / 'acme'
        proxy = self._make_proxy(download_dir=str(pin))
        requested = '~/.vibe-seller/downloads/acme'
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': requested},
        }
        old_home = os.environ.get('HOME')
        os.environ['HOME'] = str(home)
        try:
            with caplog.at_level(logging.INFO, logger='app.browser'):
                await proxy._route_client_message('client-a', json.dumps(msg))
        finally:
            if old_home is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = old_home

        forwarded = proxy._forwarded[0]
        assert forwarded['params']['downloadPath'] == requested
        assert 'outside the pinned dir' not in caplog.text

    async def test_prefix_lookalike_outside_is_rewritten(self):
        """A path that shares the pin's prefix but is NOT under it
        (a sibling dir) is outside — the inside-check must not be a
        bare ``startswith``."""
        proxy = self._make_proxy(download_dir='/srv-home/downloads/acme')
        sibling = '/srv-home/downloads/acme-other'
        msg = {
            'id': 7,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': sibling},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))

        forwarded = proxy._forwarded[0]
        assert forwarded['params']['downloadPath'] == '/srv-home/downloads/acme'

    async def test_browser_rewrite_also_logged(self, caplog):
        """The pre-existing Browser.* rewrite now logs too (the
        watchdog's /tmp dir shows up here — once per daemon start)."""
        proxy = self._make_proxy(download_dir='/stable/path')
        msg = {
            'id': 1,
            'method': 'Browser.setDownloadBehavior',
            'params': {
                'behavior': 'allow',
                'downloadPath': '/tmp/browser-use-downloads-abc123/',
                'eventsEnabled': True,
            },
        }
        with caplog.at_level(logging.INFO, logger='app.browser'):
            await proxy._route_client_message('client-a', json.dumps(msg))

        assert proxy._forwarded[0]['params']['downloadPath'] == '/stable/path'
        assert 'outside the pinned dir' in caplog.text


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
                calls.append(json.loads(msg))

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

    async def test_watchdog_tmp_dir_still_rewritten(self):
        """browser-use's DownloadsWatchdog sets a random /tmp dir per
        daemon start — main rewrote it to the pin and so must this
        branch (the deterministic-downloads fix)."""
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
        await proxy._route_client_message('client-a', json.dumps(msg))
        assert proxy._forwarded[0]['params']['downloadPath'] == '/pin/acme'


@pytest.mark.unit
class TestWindowsShapedPins:
    """Z2: a Windows-shaped pin compares with ntpath semantics on ANY
    server OS.

    winchrome passes the WINDOWS-side dir as the pin while the server
    runs on POSIX; native-Windows Ziniao passes one on Windows. In both
    cases a client's ``c:/``-spelled or lowercased subdir of the pin is
    INSIDE and must be left alone; a sibling or another drive is
    outside.
    """

    PIN = 'C:\\Users\\straw\\downloads\\acme'

    def _make_proxy(self):
        proxy = CDPMuxProxy(
            listen_port=0,
            target_port=0,
            download_dir=self.PIN,
        )
        proxy._forwarded = []

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._send_upstream = _capture
        return proxy

    async def _send(self, proxy, download_path):
        msg = {
            'id': 1,
            'method': 'Page.setDownloadBehavior',
            'params': {'behavior': 'allow', 'downloadPath': download_path},
        }
        await proxy._route_client_message('client-a', json.dumps(msg))
        return proxy._forwarded[0]['params']['downloadPath']

    async def test_windows_subdir_backslash_inside(self):
        proxy = self._make_proxy()
        sub = 'C:\\Users\\straw\\downloads\\acme\\run-1'
        assert await self._send(proxy, sub) == sub

    async def test_windows_subdir_forward_slash_lowercase_inside(self):
        proxy = self._make_proxy()
        sub = 'c:/users/straw/downloads/acme/run-1'
        assert await self._send(proxy, sub) == sub

    async def test_pin_itself_forward_slash_lowercase_inside(self):
        proxy = self._make_proxy()
        same = 'c:/users/straw/downloads/acme/'
        assert await self._send(proxy, same) == same

    async def test_sibling_dir_outside_rewritten(self):
        proxy = self._make_proxy()
        got = await self._send(proxy, 'C:\\Users\\straw\\downloads\\acme-other')
        assert got == self.PIN

    async def test_different_drive_outside_rewritten(self):
        proxy = self._make_proxy()
        got = await self._send(proxy, 'D:\\Users\\straw\\downloads\\acme')
        assert got == self.PIN

    async def test_negative_control_posix_compare_reads_subdir_outside(
        self,
    ):
        """Without the ntpath branch (the pre-fix state on a POSIX
        server), a Windows-spelled subdir reads as OUTSIDE and would be
        rewritten to the pin root — this demonstrates the detector
        catches exactly that regression."""
        proxy = self._make_proxy()
        with mock.patch(
            'app.browser.cdp_mux_routing._is_windows_shaped',
            lambda _p: False,
        ):
            got = await self._send(
                proxy, 'C:\\Users\\straw\\downloads\\acme\\run-1'
            )
        assert got == self.PIN
