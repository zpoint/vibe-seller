"""Per-client tab cap + activity tracking in the CDP mux.

Every agent navigation is a ``new_tab`` and nothing in-session ever
closed one — a long task accumulated hundreds of tabs (the
thousand-tab window). The mux now LRU-closes a client's oldest tab
beyond ``VIBE_TAB_CAP``, strictly within that client's ownership, and
stamps ``last_activity`` so the idle-browser sweeper can tell a used
browser from an abandoned one.
"""

import time
from unittest import mock

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.cdp_mux_types import ClientState, RequestMapping
from app.env_options import Options

pytestmark = pytest.mark.unit


def _proxy():
    proxy = CDPMuxProxy(listen_port=0, target_port=0)
    proxy._sent_upstream = []
    proxy._sent_client = []

    async def _up(msg):
        proxy._sent_upstream.append(msg)

    async def _cl(client, msg):
        proxy._sent_client.append((client.client_id, msg))

    proxy._send_upstream = _up
    proxy._send_client = _cl
    return proxy


def _client(proxy, client_id):
    client = ClientState(client_id=client_id, ws=mock.Mock())
    proxy._clients[client_id] = client
    return client


async def _create_target(proxy, client, target_id):
    gid = proxy._next_global_id()
    proxy._global_request_map[gid] = RequestMapping(
        client_id=client.client_id,
        original_id=1,
        is_create_target=True,
    )
    await proxy._route_response({'id': gid, 'result': {'targetId': target_id}})


def _closed_targets(proxy):
    return [
        m['params']['targetId']
        for m in proxy._sent_upstream
        if m.get('method') == 'Target.closeTarget'
    ]


class TestTabCap:
    async def test_oldest_tab_closed_beyond_cap(self, monkeypatch):
        monkeypatch.setenv(Options.TAB_CAP.env_var, '2')
        proxy = _proxy()
        client = _client(proxy, 'task-a')

        await _create_target(proxy, client, 'T1')
        await _create_target(proxy, client, 'T2')
        assert _closed_targets(proxy) == []

        await _create_target(proxy, client, 'T3')
        assert _closed_targets(proxy) == ['T1']
        assert client.target_ids == {'T2', 'T3'}
        assert client.target_order == ['T2', 'T3']
        assert 'T1' not in proxy._target_to_client

    async def test_cap_is_per_client_never_cross(self, monkeypatch):
        monkeypatch.setenv(Options.TAB_CAP.env_var, '2')
        proxy = _proxy()
        a = _client(proxy, 'task-a')
        b = _client(proxy, 'task-b')

        await _create_target(proxy, a, 'A1')
        await _create_target(proxy, b, 'B1')
        await _create_target(proxy, a, 'A2')
        await _create_target(proxy, b, 'B2')
        await _create_target(proxy, a, 'A3')

        # Only A's oldest closed; B untouched despite interleaving.
        assert _closed_targets(proxy) == ['A1']
        assert b.target_ids == {'B1', 'B2'}

    async def test_cap_zero_disables(self, monkeypatch):
        monkeypatch.setenv(Options.TAB_CAP.env_var, '0')
        proxy = _proxy()
        client = _client(proxy, 'task-a')
        for i in range(30):
            await _create_target(proxy, client, f'T{i}')
        assert _closed_targets(proxy) == []
        assert len(client.target_ids) == 30

    async def test_target_destroyed_prunes_order(self, monkeypatch):
        monkeypatch.setenv(Options.TAB_CAP.env_var, '3')
        proxy = _proxy()
        client = _client(proxy, 'task-a')
        await _create_target(proxy, client, 'T1')
        await _create_target(proxy, client, 'T2')

        # The browser reports T1 gone (e.g. page crashed / user closed).
        await proxy._route_target_event({
            'method': 'Target.targetDestroyed',
            'params': {'targetId': 'T1'},
        })
        assert 'T1' not in client.target_order
        assert 'T1' not in client.target_ids

        # A third create now fits without evicting T2.
        await _create_target(proxy, client, 'T3')
        await _create_target(proxy, client, 'T4')
        assert _closed_targets(proxy) == []


class TestActivityTracking:
    async def test_client_message_stamps_activity(self):
        proxy = _proxy()
        _client(proxy, 'task-a')
        proxy.last_activity = time.monotonic() - 1000
        assert proxy.idle_seconds() > 900

        await proxy._route_client_message(
            'task-a', '{"id": 1, "method": "Page.enable"}'
        )
        assert proxy.idle_seconds() < 5

    async def test_target_event_stamps_activity(self):
        # Target events also fire for HUMAN tab use — a hand-browsed
        # window must read as active.
        proxy = _proxy()
        proxy.last_activity = time.monotonic() - 1000
        await proxy._route_target_event({
            'method': 'Target.targetCreated',
            'params': {'targetInfo': {'targetId': 'X', 'type': 'page'}},
        })
        assert proxy.idle_seconds() < 5
