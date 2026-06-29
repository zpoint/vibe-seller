"""Unit test for CDPMuxProxy wedged-tab recovery.

``reset_orphan_page_tabs`` is the proxy primitive the wrapper's
self-healing ``open`` calls (via ``/vibe/reset-tabs``) when a page
renderer wedges. It must close only ``http(s)`` page tabs — never
extension / devtools / ``chrome://`` targets — and fire
``Target.closeTarget`` for each so the next daemon attaches to a clean
tab instead of hanging on the dead one (the wedged-tab failure).

Scoping: the proxy serves multiple concurrent tasks per store, so the
reset must only touch tabs owned by the *requesting* client or true
orphans (no owner) — never a sibling task's tabs, whether that sibling
is connected or in its deferred-cleanup grace window.
"""

from unittest import mock

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.cdp_mux_types import ClientState


@pytest.mark.unit
class TestResetOrphanPageTabs:
    def _make_proxy(self, targets):
        proxy = CDPMuxProxy(listen_port=0, target_port=0)
        proxy._forwarded = []

        async def _fetch(path):
            assert path == '/json/list'
            return targets

        async def _capture(msg):
            proxy._forwarded.append(msg)

        proxy._fetch_upstream_http = _fetch
        proxy._send_upstream = _capture
        return proxy

    async def test_closes_only_web_page_tabs(self):
        proxy = self._make_proxy([
            {
                'id': 'T1',
                'type': 'page',
                'url': 'https://advertising.amazon.com/cm',
            },
            {'id': 'T2', 'type': 'page', 'url': 'http://seller.amazon.com/x'},
            {'id': 'T3', 'type': 'page', 'url': 'chrome://newtab/'},
            {'id': 'T4', 'type': 'background_page', 'url': 'https://ext/bg'},
            {'id': 'T5', 'type': 'page', 'url': 'devtools://devtools/x'},
        ])
        closed = await proxy.reset_orphan_page_tabs()

        assert closed == 2
        closed_ids = {
            m['params']['targetId']
            for m in proxy._forwarded
            if m['method'] == 'Target.closeTarget'
        }
        assert closed_ids == {'T1', 'T2'}

    async def test_drops_target_ownership_mapping(self):
        proxy = self._make_proxy([
            {'id': 'T1', 'type': 'page', 'url': 'https://x.com/'},
        ])
        proxy._target_to_client['T1'] = 'client-a'
        await proxy.reset_orphan_page_tabs(requesting_client='client-a')
        # Ownership cleared so the next daemon owns a fresh tab.
        assert 'T1' not in proxy._target_to_client

    async def test_skips_tabs_owned_by_other_clients(self):
        """Task A's self-heal must never close task B's healthy tabs —
        whether B is connected or in its reconnect grace window."""
        proxy = self._make_proxy([
            # Owned by the requester (its wedged tab) — closed.
            {'id': 'T1', 'type': 'page', 'url': 'https://x.com/a'},
            # Owned by a live sibling — skipped.
            {'id': 'T2', 'type': 'page', 'url': 'https://x.com/b'},
            # Owned by a sibling in deferred cleanup — skipped.
            {'id': 'T3', 'type': 'page', 'url': 'https://x.com/c'},
            # No owner (true orphan, e.g. browser-spawned) — closed.
            {'id': 'T4', 'type': 'page', 'url': 'https://x.com/d'},
        ])
        proxy._target_to_client = {
            'T1': 'task-a',
            'T2': 'task-b',
            'T3': 'task-c',
        }
        closed = await proxy.reset_orphan_page_tabs(requesting_client='task-a')

        assert closed == 2
        closed_ids = {
            m['params']['targetId']
            for m in proxy._forwarded
            if m['method'] == 'Target.closeTarget'
        }
        assert closed_ids == {'T1', 'T4'}
        # Sibling ownership untouched.
        assert proxy._target_to_client == {'T2': 'task-b', 'T3': 'task-c'}

    async def test_unscoped_reset_closes_only_unowned_tabs(self):
        """A reset with no client id (stale wrapper) fails safe: it
        only closes true orphans, never any owned tab."""
        proxy = self._make_proxy([
            {'id': 'T1', 'type': 'page', 'url': 'https://x.com/a'},
            {'id': 'T2', 'type': 'page', 'url': 'https://x.com/b'},
        ])
        proxy._target_to_client = {'T1': 'task-a'}
        closed = await proxy.reset_orphan_page_tabs()

        assert closed == 1
        closed_ids = {
            m['params']['targetId']
            for m in proxy._forwarded
            if m['method'] == 'Target.closeTarget'
        }
        assert closed_ids == {'T2'}

    async def test_closed_tab_removed_from_owner_client_state(self):
        """Closing the requester's own tab must also drop the id from
        its ClientState (live or deferred) so a daemon reconnect
        doesn't 'recover' a dead target."""
        proxy = self._make_proxy([
            {'id': 'T1', 'type': 'page', 'url': 'https://x.com/a'},
        ])
        proxy._target_to_client = {'T1': 'task-a'}
        deferred_state = ClientState(
            client_id='task-a', ws=mock.Mock(), target_ids={'T1'}
        )
        proxy._deferred_cleanups['task-a'] = (mock.Mock(), deferred_state)

        closed = await proxy.reset_orphan_page_tabs(requesting_client='task-a')

        assert closed == 1
        assert 'T1' not in deferred_state.target_ids

    async def test_no_tabs_is_noop(self):
        proxy = self._make_proxy([])
        assert await proxy.reset_orphan_page_tabs() == 0
        assert proxy._forwarded == []

    async def test_handles_unreachable_upstream(self):
        proxy = self._make_proxy(None)  # _fetch returns None
        assert await proxy.reset_orphan_page_tabs() == 0
        assert proxy._forwarded == []
