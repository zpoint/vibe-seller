"""Unit tests: a Ziniao env is only "ready" if Ziniao instrumented it,
and one broken store can never stall the others.

Observed failure (2026-07-25): three stores' envs came up with their
debug port bound but Ziniao's SBP layer never injected — no account
auto-fill, no OTP/passkey overlay, and ``navigator.webdriver`` leaking
``true`` to Amazon, which bounced every page to ``/ap/signin``. The
readiness contract only proved *Chrome* was up, so the agents were
handed unusable browsers and burned a full run on an unwinnable login.
Meanwhile the dead-mux path relaunched the whole env on every
``browser-use`` call, hammering the shared Ziniao client until it hung.
"""

from unittest import mock

import pytest

from app.browser import ziniao as zmod
from app.browser.launch_guards import RelaunchBudget
from app.browser.manager import BrowserManager
from app.browser.ziniao import ZiniaoBackend

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg():
    return {
        'company': 'co',
        'username': 'u',
        'password': 'p',
        'socket_port': 16851,
        'client_path': 'ziniao',
        'browser_oauth': 'OAUTH==',
        'proxy_port': 9222,
        'store_slug': 'store-x',
    }


class TestInstrumentationGate:
    async def test_uninstrumented_env_is_rejected_and_recovered_per_store(
        self, cfg, tmp_path
    ):
        """Port reachable but SBP dead → treat as a stale launch and
        recover PER STORE (stopBrowser this env + retry), never a global
        client kill."""
        calls: list[str] = []

        async def fake_try_connect(port, data, timeout):
            calls.append(data['action'])
            return (
                {'statusCode': '0', 'debuggingPort': 5000 + len(calls)},
                '127.0.0.1',
            )

        # Port is always reachable — only instrumentation is missing,
        # so this is exactly the case the old gate waved through.
        live = iter([False, False, True])

        async def fake_live(host, port):
            return next(live)

        with (
            mock.patch.object(
                zmod, 'ensure_ziniao_running', new=mock.AsyncMock()
            ),
            mock.patch.object(zmod, 'update_ziniao_core', new=mock.AsyncMock()),
            mock.patch.object(
                zmod,
                'try_connect_ziniao',
                new=mock.AsyncMock(side_effect=fake_try_connect),
            ),
            mock.patch.object(
                ZiniaoBackend,
                '_cdp_port_reachable',
                new=mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(
                ZiniaoBackend,
                '_instrumentation_live',
                new=mock.AsyncMock(side_effect=fake_live),
            ),
            mock.patch.object(
                ZiniaoBackend,
                '_wait_for_target_stability',
                new=mock.AsyncMock(),
            ),
            mock.patch.object(zmod, 'CDPMuxProxy') as MockProxy,
            mock.patch('app.browser.ziniao.DOWNLOADS_DIR', tmp_path),
            mock.patch('app.browser.ziniao_utils.force_kill_ziniao') as fk,
            mock.patch(
                'app.browser.ziniao_utils.kill_and_relaunch_ziniao',
                new=mock.AsyncMock(),
            ) as kar,
        ):
            MockProxy.return_value.start = mock.AsyncMock()
            await ZiniaoBackend().start(cfg)

        fk.assert_not_called()
        kar.assert_not_called()
        assert calls.count('stopBrowser') >= 2, (
            f'expected per-store stopBrowser recovery, got {calls}'
        )

    async def test_never_uninstrumented_after_all_attempts(self, cfg, tmp_path):
        """If instrumentation never appears, start() FAILS rather than
        returning a browser the agent cannot log in with."""

        async def fake_try_connect(port, data, timeout):
            return ({'statusCode': '0', 'debuggingPort': 6000}, '127.0.0.1')

        with (
            mock.patch.object(
                zmod, 'ensure_ziniao_running', new=mock.AsyncMock()
            ),
            mock.patch.object(zmod, 'update_ziniao_core', new=mock.AsyncMock()),
            mock.patch.object(
                zmod,
                'try_connect_ziniao',
                new=mock.AsyncMock(side_effect=fake_try_connect),
            ),
            mock.patch.object(
                ZiniaoBackend,
                '_cdp_port_reachable',
                new=mock.AsyncMock(return_value=True),
            ),
            mock.patch.object(
                ZiniaoBackend,
                '_instrumentation_live',
                new=mock.AsyncMock(return_value=False),
            ),
            mock.patch('app.browser.ziniao.DOWNLOADS_DIR', tmp_path),
        ):
            with pytest.raises(RuntimeError, match='instrumentation'):
                await ZiniaoBackend().start(cfg)

    async def test_probe_fails_open_when_inconclusive(self):
        """A probe that cannot reach the port must NOT block the launch —
        an unreachable/erroring probe is 'unknown', not 'broken'. Only a
        positive ``navigator.webdriver === true`` is a verdict."""
        ok = await ZiniaoBackend._instrumentation_live(
            '127.0.0.1', 1, attempts=1, delay=0
        )
        assert ok is True


class TestCrossStoreIsolation:
    """One broken store must not take the others down with it."""

    def test_relaunch_breaker_trips_then_resets(self):
        budget = RelaunchBudget()

        # Budget is per store: 3 relaunches allowed, 4th refuses.
        for _ in range(3):
            budget.note('s1', 'alpha')
        with pytest.raises(RuntimeError, match='Refusing to restart'):
            budget.note('s1', 'alpha')

        # A DIFFERENT store is unaffected by the tripped breaker.
        budget.note('s2', 'beta')

        # A healthy launch clears the budget (see start_session).
        budget.clear('s1')
        budget.note('s1', 'alpha')

    def test_relaunch_breaker_disabled_at_zero(self, monkeypatch):
        monkeypatch.setenv('VIBE_BROWSER_RELAUNCH_MAX', '0')
        budget = RelaunchBudget()
        for _ in range(25):
            budget.note('s1', 'alpha')  # must never raise

    def test_manager_wires_the_budget(self):
        """The breaker is only useful if the manager actually holds one."""
        assert isinstance(BrowserManager()._relaunches, RelaunchBudget)
