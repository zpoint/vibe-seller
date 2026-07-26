"""Guards around launching a store's browser.

``BrowserManager.start_session`` holds a **global** lock — deliberately,
so concurrent launches can't hammer the shared anti-detect client (see
docs/ziniao-concurrency.md). That makes one store's failure everyone's
problem: an unbounded per-store retry loop starves every other store's
launch, and the dead-mux relaunch path is reachable from *every*
``browser-use`` call, so a wedged client turns recovery into a storm.

Both guards below bound that blast radius. They live here rather than on
the manager because they are policy, independently testable, and the
manager is already at the repo's file-size ceiling.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.browser.base import BrowserSessionInfo
from app.env_options import Options

logger = logging.getLogger(__name__)


class RelaunchBudget:
    """Per-store budget for the dead-mux full-env relaunch.

    Relaunching tears down and re-creates a store's whole browser env.
    With the client wedged every attempt fails identically, so the
    unbudgeted version loops forever — the observed failure was three
    stores cycling stop/start every ~20 s for five minutes, which
    hammered the shared client until it hung and left every agent on a
    freshly-wiped, logged-out browser. Bounding it turns that storm into
    one actionable failure.
    """

    def __init__(self) -> None:
        self._seen: dict[str, list[float]] = {}

    def clear(self, store_id: str) -> None:
        """Forget a store's history — call after a healthy launch so the
        breaker only trips on a *run* of failures, not on occasional
        recoveries spread across a long session."""
        self._seen.pop(store_id, None)

    def note(self, store_id: str, store_name: str) -> None:
        """Record one relaunch; raise once the budget is spent."""
        limit = Options.BROWSER_RELAUNCH_MAX.get_int()
        window = Options.BROWSER_RELAUNCH_WINDOW_S.get_float()
        if limit <= 0 or window <= 0:
            return
        now = time.monotonic()
        recent = [t for t in self._seen.get(store_id, []) if now - t < window]
        if len(recent) >= limit:
            oldest = int(now - recent[0])
            self._seen[store_id] = recent
            raise RuntimeError(
                f'Browser for {store_name} has been relaunched '
                f'{len(recent)} times in the last {oldest}s and its CDP '
                f'proxy is still dead. Refusing to restart again — '
                f'repeated relaunches wipe the browser session and can '
                f'wedge the shared Ziniao client. Check that Ziniao is '
                f'running in WebDriver mode and responding, then retry '
                f'(Settings → Ziniao → Force Restart).'
            )
        recent.append(now)
        self._seen[store_id] = recent


async def start_backend_bounded(backend, browser_config: dict, store_name: str):
    """``backend.start()`` with a hard ceiling on how long it may run.

    Runs under the manager's global lock, so a store stuck in its own
    retry loop would otherwise block every other store's launch for
    minutes (4 Ziniao attempts x ~95 s). One broken store must fail
    fast, not stall the machine.

    On timeout the half-started env is torn down — the Ziniao backend
    captures its per-store ``stopBrowser`` payload early in ``start()``,
    so this closes THIS env only and never touches a peer.
    """
    timeout = Options.BROWSER_START_TIMEOUT_S.get_float()
    if timeout <= 0:
        return await backend.start(browser_config)
    try:
        return await asyncio.wait_for(
            backend.start(browser_config), timeout=timeout
        )
    except TimeoutError:
        try:
            await backend.stop(BrowserSessionInfo())
        except Exception as e:
            logger.warning(
                'Cleanup after start timeout failed for %s: %s', store_name, e
            )
        raise RuntimeError(
            f'Browser launch for {store_name} exceeded {timeout:.0f}s and '
            f'was aborted so it could not block other stores. This store '
            f'failed to start; other stores are unaffected. Retry the '
            f'task — if it persists, restart Ziniao (Settings → Ziniao).'
        ) from None
