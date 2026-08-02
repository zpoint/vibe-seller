"""Two live connections may never share one CDP mux client id.

Client registration used to be an unguarded
``self._clients[client_id] = client``. A second connection with the same
id displaced the first in the dict, and nothing else changed: every
response and event for that id then routed to the newcomer's socket
while the displaced driver waited forever. No error, no log — the losing
agent simply read the wrong page, or nothing at all.

The id is derived from ``VIBE_TASK_ID`` plus (since wrapper v5) a
``--worker N`` slot, so a collision means two browser drivers were
handed the same identity — the exact failure mode parallel worker slots
exist to prevent. It must be loud, and it must not corrupt the survivor.

The takeover direction is deliberate. The common real cause is a daemon
whose socket died without a clean close, and REFUSING the newcomer would
wedge every subsequent call for that task; so the newest connection wins,
inherits the targets, and the loser gets a close code it can report.
"""

import asyncio
from unittest import mock

import pytest

from app.browser.cdp_mux_proxy import (
    DUPLICATE_CLIENT_CLOSE_CODE,
    CDPMuxProxy,
)


class _FakeWS:
    """A websocket that yields queued frames, then stays open until closed."""

    def __init__(self, path: str):
        self.request = mock.Mock(path=path)
        self.sent: list[str] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._closed = asyncio.Event()

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = '') -> None:
        self.close_code = code
        self.close_reason = reason
        self._closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        # No client traffic in these tests: block until closed, then end
        # the read loop the way a real disconnect does.
        await self._closed.wait()
        raise StopAsyncIteration


@pytest.fixture
def proxy():
    p = CDPMuxProxy(listen_port=0, target_port=0, cleanup_grace=0.01)
    p._upstream_sent = []

    async def _send_upstream(msg):
        p._upstream_sent.append(msg)

    p._send_upstream = _send_upstream
    yield p
    # Deferred-cleanup tasks outlive the test's event loop otherwise
    # ("Task was destroyed but it is pending").
    for task, _ in p._deferred_cleanups.values():
        task.cancel()


async def _connect(proxy: CDPMuxProxy, ws: _FakeWS) -> asyncio.Task:
    """Start a handler and wait until THIS ws owns the id (or it bailed).

    Waiting on "the id is present" is not enough for the second
    connection of a collision test — the first one already put it there,
    so the wait would fall through before the handler under test ran.
    """
    cid = ws.request.path.removeprefix('/client-')
    task = asyncio.create_task(proxy._handle_client_ws(ws))
    for _ in range(200):
        await asyncio.sleep(0)
        registered = proxy._clients.get(cid)
        if (registered is not None and registered.ws is ws) or task.done():
            break
    return task


@pytest.mark.unit
class TestDuplicateClientId:
    async def test_second_connection_takes_over_and_first_is_closed(
        self, proxy
    ):
        first = _FakeWS('/client-t1')
        first_task = await _connect(proxy, first)
        proxy._clients['t1'].target_ids.add('TARGET-A')
        proxy._clients['t1'].target_order.append('TARGET-A')
        proxy._target_to_client['TARGET-A'] = 't1'

        second = _FakeWS('/client-t1')
        second_task = await _connect(proxy, second)

        # The displaced connection is closed with a code that names the
        # cause — a hung daemon is far cheaper to diagnose than a silent
        # one, and this is what makes a slot collision reportable.
        assert first.close_code == DUPLICATE_CLIENT_CLOSE_CODE
        assert 'newer connection' in (first.close_reason or '')

        # Exactly one live client, and it is the newcomer.
        assert list(proxy._clients) == ['t1']
        assert proxy._clients['t1'].ws is second

        await second.close()
        await asyncio.wait_for(
            asyncio.gather(first_task, second_task), timeout=2
        )

    async def test_collision_is_logged_at_error(self, proxy, caplog):
        first = _FakeWS('/client-t1')
        first_task = await _connect(proxy, first)
        with caplog.at_level('ERROR', logger='app.browser.cdp_mux_routing'):
            second = _FakeWS('/client-t1')
            second_task = await _connect(proxy, second)
        assert any(
            'client id collision' in r.message.lower()
            or 'client id collision' in r.getMessage().lower()
            for r in caplog.records
        ), caplog.text
        # The message must point at the fix, not just the symptom.
        assert '--worker' in caplog.text

        await second.close()
        await asyncio.wait_for(
            asyncio.gather(first_task, second_task), timeout=2
        )

    async def test_displaced_handler_does_not_close_the_survivors_tabs(
        self, proxy
    ):
        """The race this guards is the whole reason for the change.

        The displaced handler's ``finally`` runs *after* the newcomer has
        registered. Popping by id alone would take the LIVE client out of
        the registry and schedule a cleanup that closes tabs the new
        connection is actively using.
        """
        first = _FakeWS('/client-t1')
        first_task = await _connect(proxy, first)
        proxy._clients['t1'].target_ids.add('TARGET-A')
        proxy._target_to_client['TARGET-A'] = 't1'

        second = _FakeWS('/client-t1')
        second_task = await _connect(proxy, second)

        # Let the displaced handler unwind and any deferred cleanup fire.
        await asyncio.wait_for(first_task, timeout=2)
        await asyncio.sleep(0.1)

        assert proxy._clients.get('t1') is not None, (
            'the live client was evicted by the displaced handler'
        )
        assert proxy._clients['t1'].ws is second
        assert not proxy._deferred_cleanups
        closed = [
            m
            for m in proxy._upstream_sent
            if m.get('method') == 'Target.closeTarget'
        ]
        assert not closed, f'survivor tabs were closed: {closed}'

        await second.close()
        await asyncio.wait_for(second_task, timeout=2)

    async def test_targets_transfer_to_the_new_connection(self, proxy):
        """Ownership follows the id, so the browser state is not orphaned."""
        first = _FakeWS('/client-t1')
        first_task = await _connect(proxy, first)
        proxy._clients['t1'].target_ids.add('TARGET-A')
        proxy._clients['t1'].target_order.append('TARGET-A')
        proxy._clients['t1'].session_ids.add('SESSION-A')
        proxy._session_to_client['SESSION-A'] = 't1'
        proxy._target_to_client['TARGET-A'] = 't1'

        second = _FakeWS('/client-t1')
        second_task = await _connect(proxy, second)

        client = proxy._clients['t1']
        assert client.target_ids == {'TARGET-A'}
        # Sessions belong to the dead daemon process and are dropped —
        # the same rule the reconnect path already applies.
        assert client.session_ids == set()
        assert 'SESSION-A' not in proxy._session_to_client

        await second.close()
        await asyncio.wait_for(
            asyncio.gather(first_task, second_task), timeout=2
        )

    async def test_distinct_worker_ids_do_not_collide(self, proxy):
        """The positive case: distinct slots are simply separate clients.

        This is what `--worker N` buys — same browser, separate tab sets,
        no takeover, no shared target ownership.
        """
        main = _FakeWS('/client-t1')
        main_task = asyncio.create_task(proxy._handle_client_ws(main))
        w1 = _FakeWS('/client-t1-w1')
        w1_task = asyncio.create_task(proxy._handle_client_ws(w1))
        for _ in range(50):
            await asyncio.sleep(0)
            if len(proxy._clients) == 2:
                break

        assert set(proxy._clients) == {'t1', 't1-w1'}
        assert main.close_code is None
        assert w1.close_code is None

        await main.close()
        await w1.close()
        await asyncio.wait_for(asyncio.gather(main_task, w1_task), timeout=2)

    async def test_normal_reconnect_after_disconnect_still_recovers(
        self, proxy
    ):
        """No regression: a clean disconnect + reconnect is not a collision."""
        first = _FakeWS('/client-t1')
        first_task = await _connect(proxy, first)
        proxy._clients['t1'].target_ids.add('TARGET-A')
        proxy._target_to_client['TARGET-A'] = 't1'

        await first.close()
        await asyncio.wait_for(first_task, timeout=2)
        assert 't1' in proxy._deferred_cleanups

        second = _FakeWS('/client-t1')
        second_task = await _connect(proxy, second)
        assert proxy._clients['t1'].target_ids == {'TARGET-A'}
        assert not proxy._deferred_cleanups
        # Nothing was displaced, so nothing was force-closed.
        assert first.close_code == 1000

        await second.close()
        await asyncio.wait_for(second_task, timeout=2)
