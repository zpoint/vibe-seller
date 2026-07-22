"""Aux-browser lifecycle invariants.

Regression guards for two defects that let one store's aux start wedge
aux for EVERY store: ``ChromeBackend.stop`` used to require a
``SessionInfo`` the aux path never had (so teardown raised and leaked a
live Chromium), and ``start_aux`` ran the browser start/stop under a
module lock with no timeout (so a hung launch held the lock forever).
"""

import asyncio
from unittest import mock

import pytest

from app.browser import aux_browser
from app.browser.chrome import ChromeBackend

pytestmark = pytest.mark.unit


class _Store:
    id = 'store-1'
    name = 'Example Store'


class TestStopWithoutInfo:
    def test_chrome_stop_callable_with_no_info(self):
        # The aux path calls ``backend.stop()`` with no SessionInfo. With
        # nothing started (all handles None) it must no-op cleanly, never
        # raise TypeError — which used to leak the aux Chromium.
        backend = ChromeBackend()
        asyncio.run(backend.stop())


class TestStartAuxIsBounded:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        aux_browser._backends.clear()
        aux_browser._ports.clear()
        yield
        aux_browser._backends.clear()
        aux_browser._ports.clear()

    def test_hung_start_times_out_and_frees_the_lock(self, monkeypatch):
        # A backend whose start() never returns must not hold _lock
        # forever: start_aux bounds it, raises, and a SUBSEQUENT call can
        # still acquire the lock (i.e. aux isn't wedged for other stores).
        monkeypatch.setattr(aux_browser, '_START_TIMEOUT', 0.2)
        monkeypatch.setattr(aux_browser, '_STOP_TIMEOUT', 0.2)

        class _HangingBackend:
            async def start(self, cfg):
                await asyncio.sleep(60)  # never completes in-bound

            async def stop(self, info=None):
                return None

        monkeypatch.setattr(aux_browser, 'ChromeBackend', _HangingBackend)
        monkeypatch.setattr(aux_browser, '_free_port', lambda: 54321)

        async def scenario():
            with pytest.raises(asyncio.TimeoutError):
                await aux_browser.start_aux(_Store(), headless=True)
            # Lock must be free now: a second call acquires it and also
            # bounds out, rather than blocking forever.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    aux_browser.start_aux(_Store(), headless=True),
                    timeout=5,
                )
            # No half-started backend left registered.
            assert 'store-1' not in aux_browser._backends

        asyncio.run(scenario())

    def test_alive_instance_is_reused(self, monkeypatch):
        # When a registered instance answers _alive, start_aux returns it
        # without relaunching.
        sentinel = mock.Mock()
        aux_browser._backends['store-1'] = sentinel
        aux_browser._ports['store-1'] = 40000

        async def scenario():
            with mock.patch.object(
                aux_browser, '_alive', new=mock.AsyncMock(return_value=True)
            ):
                out = await aux_browser.start_aux(_Store(), headless=True)
            assert out['proxy_port'] == 40000
            assert out['ws'].endswith(':40000/client-aux')
            assert aux_browser._backends['store-1'] is sentinel

        asyncio.run(scenario())
