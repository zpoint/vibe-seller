"""Idle-browser sweeper guard matrix (app/browser/idle_sweep.py).

The sweep may stop a browser only when BOTH hold: no live task is
bound to it, and its mux is idle past ``VIBE_BROWSER_IDLE_S`` with no
connected clients. Every guard is pinned here; the workflow tier
drives the same sweep against real DB tasks.
"""

from unittest import mock

import pytest

from app.browser import aux_browser, idle_sweep
from app.browser.idle_sweep import _HOLD_STATUSES, sweep_idle_browsers
from app.browser.manager import WEB_BROWSER_SLUG, browser_manager
from app.env_options import Options
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


class _FakeProxy:
    def __init__(self, clients=0, idle=1e9):
        self._clients = clients
        self._idle = idle

    def has_active_clients(self):
        return self._clients > 0

    def idle_seconds(self):
        return self._idle


class _FakeBackend:
    def __init__(self, proxy):
        self._proxy = proxy


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(Options.BROWSER_IDLE_S.env_var, '300')
    stopped = {'stores': [], 'aux': [], 'web': 0}

    async def _bindings():
        return env_state['hold_ids'], env_state['nostore']

    env_state = {'hold_ids': set(), 'nostore': False}
    monkeypatch.setattr(idle_sweep, '_live_task_bindings', _bindings)

    async def _stop_session(store, db):
        stopped['stores'].append(store.id)
        browser_manager._active_sessions.pop(store.id, None)

    monkeypatch.setattr(browser_manager, 'stop_session', _stop_session)

    async def _stop_aux(store_id):
        stopped['aux'].append(store_id)
        aux_browser._backends.pop(store_id, None)
        return True

    monkeypatch.setattr(idle_sweep.aux_browser, 'stop_aux', _stop_aux)

    async def _stop_web():
        stopped['web'] += 1
        browser_manager._active_sessions.pop(WEB_BROWSER_SLUG, None)
        browser_manager._backends.pop(WEB_BROWSER_SLUG, None)

    monkeypatch.setattr(idle_sweep, '_stop_web', _stop_web)

    # Store lookup: any id resolves to a stub store.
    class _FakeDB:
        async def get(self, _model, key):
            return mock.Mock(id=key, name=f'store-{key}')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(idle_sweep, 'async_session', lambda: _FakeDB())

    monkeypatch.setattr(browser_manager, '_active_sessions', {})
    monkeypatch.setattr(browser_manager, '_backends', {})
    monkeypatch.setattr(aux_browser, '_backends', {})
    return env_state, stopped


def _add_store_browser(store_id, proxy):
    browser_manager._active_sessions[store_id] = mock.Mock()
    browser_manager._backends[store_id] = _FakeBackend(proxy)


class TestSweepGuards:
    async def test_idle_no_task_browser_is_stopped(self, env):
        _state, stopped = env
        _add_store_browser('s1', _FakeProxy())
        assert await sweep_idle_browsers() == 1
        assert stopped['stores'] == ['s1']

    async def test_live_task_holds_browser(self, env):
        state, stopped = env
        state['hold_ids'] = {'s1'}
        _add_store_browser('s1', _FakeProxy())
        assert await sweep_idle_browsers() == 0
        assert stopped['stores'] == []

    async def test_connected_client_holds_browser(self, env):
        _state, stopped = env
        _add_store_browser('s1', _FakeProxy(clients=1))
        assert await sweep_idle_browsers() == 0

    async def test_recent_activity_holds_browser(self, env):
        _state, stopped = env
        _add_store_browser('s1', _FakeProxy(idle=10))  # < 300s window
        assert await sweep_idle_browsers() == 0

    async def test_stores_are_independent(self, env):
        state, stopped = env
        state['hold_ids'] = {'busy'}
        _add_store_browser('busy', _FakeProxy())
        _add_store_browser('quiet', _FakeProxy())
        assert await sweep_idle_browsers() == 1
        assert stopped['stores'] == ['quiet']

    async def test_aux_swept_with_same_guards(self, env):
        state, stopped = env
        aux_browser._backends['s1'] = _FakeBackend(_FakeProxy())
        aux_browser._backends['s2'] = _FakeBackend(_FakeProxy(clients=1))
        state['hold_ids'] = set()
        assert await sweep_idle_browsers() == 1
        assert stopped['aux'] == ['s1']

    async def test_web_held_by_nostore_task(self, env):
        state, stopped = env
        state['nostore'] = True
        browser_manager._active_sessions[WEB_BROWSER_SLUG] = mock.Mock()
        browser_manager._backends[WEB_BROWSER_SLUG] = _FakeBackend(
            _FakeProxy()
        )
        assert await sweep_idle_browsers() == 0
        state['nostore'] = False
        assert await sweep_idle_browsers() == 1
        assert stopped['web'] == 1

    async def test_disabled_at_zero(self, env, monkeypatch):
        _state, stopped = env
        monkeypatch.setenv(Options.BROWSER_IDLE_S.env_var, '0')
        _add_store_browser('s1', _FakeProxy())
        assert await sweep_idle_browsers() == 0

    def test_waiting_does_not_hold(self):
        # A WAITING task must not pin a browser for hours — wrappers
        # lazily restart on wake. Pinned at the constant level so a
        # future edit is a conscious choice.
        assert TaskStatus.WAITING not in _HOLD_STATUSES
        assert TaskStatus.RUNNING in _HOLD_STATUSES
