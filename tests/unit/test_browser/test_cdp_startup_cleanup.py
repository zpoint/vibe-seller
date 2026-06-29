"""Unit tests for CDPMuxProxy._startup_cleanup orphan-tab reaping.

Covers the ``keep_last_page`` behaviour: closing the env's *last* page
closes the whole browser window, which tears down a Ziniao environment
(6.26.x: network SDK aborts with 'ContainerId is missing'). With
``keep_last_page=True`` the cleanup must always leave one page open while
still reaping extra orphan tabs.
"""

import asyncio
import json

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy


def _make_proxy(targets, *, keep_last_page):
    proxy = CDPMuxProxy(
        listen_port=0,
        target_port=0,
        keep_last_page=keep_last_page,
    )
    proxy._sent = []

    async def _capture(msg):
        proxy._sent.append(msg)

    proxy._send_upstream = _capture

    class _FakeUpstream:
        def __init__(self):
            self._answered = False

        async def recv(self):
            # Answer the getTargets request with the matching id.
            if not self._answered:
                self._answered = True
                gid = proxy._sent[0]['id']
                return json.dumps({
                    'id': gid,
                    'result': {'targetInfos': targets},
                })
            await asyncio.sleep(10)  # no further reads expected

    proxy._upstream = _FakeUpstream()
    return proxy


def _closed_target_ids(proxy):
    return [
        m['params']['targetId']
        for m in proxy._sent
        if m.get('method') == 'Target.closeTarget'
    ]


PAGES = [
    {'type': 'page', 'url': 'https://a.example.com/', 'targetId': 'A'},
    {'type': 'page', 'url': 'https://b.example.com/', 'targetId': 'B'},
    {'type': 'service_worker', 'url': 'chrome-extension://x', 'targetId': 'X'},
    {'type': 'page', 'url': 'chrome://newtab/', 'targetId': 'N'},
]


@pytest.mark.unit
class TestStartupCleanupKeepLastPage:
    @pytest.mark.asyncio
    async def test_keeps_one_page_when_keep_last_page(self):
        """keep_last_page=True: reap extra http pages but leave one open."""
        proxy = _make_proxy(PAGES, keep_last_page=True)
        await proxy._startup_cleanup()
        closed = _closed_target_ids(proxy)
        # Two http pages (A, B); one is kept, only one closed.
        assert len(closed) == 1
        assert closed[0] in ('A', 'B')
        # Never touch extension / chrome:// targets.
        assert 'X' not in closed and 'N' not in closed

    @pytest.mark.asyncio
    async def test_single_page_is_never_closed(self):
        """The Ziniao fresh-open case: one launcher page -> close nothing."""
        one = [{'type': 'page', 'url': 'https://store/', 'targetId': 'L'}]
        proxy = _make_proxy(one, keep_last_page=True)
        await proxy._startup_cleanup()
        assert _closed_target_ids(proxy) == []

    @pytest.mark.asyncio
    async def test_default_closes_all_http_pages(self):
        """Default (Chrome): keep_last_page=False closes every http page."""
        proxy = _make_proxy(PAGES, keep_last_page=False)
        await proxy._startup_cleanup()
        closed = _closed_target_ids(proxy)
        assert sorted(closed) == ['A', 'B']
        assert 'X' not in closed and 'N' not in closed
