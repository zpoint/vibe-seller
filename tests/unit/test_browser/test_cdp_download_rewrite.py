"""Unit test for CDPMuxProxy download path rewriting.

Exercises the real `_route_client_message` path and asserts on the
message actually forwarded upstream (captured via a stub
`_send_upstream`).  This catches regressions where the rewrite is
removed or broken in the production code path.
"""

import json

import pytest

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
