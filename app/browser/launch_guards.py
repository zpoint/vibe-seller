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
from contextlib import asynccontextmanager
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


class BrowserBusyError(RuntimeError):
    """The global launch lock could not be taken in time.

    Distinct from a launch *failure*: nothing about this store is known
    to be wrong, and retrying is the right response — which is why the
    routes map it to 503 rather than 500.
    """


class GlobalLaunchLock:
    """The manager's global lock, with a ceiling on how long you wait.

    ``BrowserManager`` serializes every launch on one lock so concurrent
    starts cannot hammer the shared anti-detect client. The lock itself
    was an ``asyncio.Lock``, so **a wedged holder made every later
    caller wait for ever** — and an HTTP handler that waits for ever
    sends no response at all. On 2026-08-24 that is exactly what a task
    saw: ``POST /api/stores/{id}/browser/start?force=1`` with proxies
    unset returned ``HTTP 000 (240.003041s)``, and the agent's only
    possible reading was *"the infrastructure is broken"* — no status,
    no message, nothing naming what to restart.

    A hang is worse than a failure here. The task looks alive, its whole
    budget drains, the wrapper's own 90 s curl gives up first, and no
    retry can help because the next call queues behind the same holder.
    So the wait is bounded and the refusal **names the holder and how
    long it has been in there**, which is the one fact that separates
    "somebody else is legitimately launching, try again" from "the
    client is wedged, restart it".

    Nothing is force-released. The holder may be inside Ziniao's own
    client and interrupting it is how a half-built browser env is left
    behind; ``start_backend_bounded`` is what stops a launch running
    away, and this only stops *waiting* on one.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._holder: str | None = None
        self._since: float = 0.0

    @property
    def holder(self) -> str | None:
        """What is inside the lock right now, or ``None``."""
        return self._holder

    def held_for(self) -> float:
        """Seconds the current holder has been in, or ``0.0``."""
        return time.monotonic() - self._since if self._holder else 0.0

    def _busy_message(self, what: str, waited: float) -> str:
        held = self.held_for()
        holder = self._holder or 'an unnamed caller'
        launch_cap = Options.BROWSER_START_TIMEOUT_S.get_float()
        if launch_cap <= 0:
            # A launch has no ceiling either, so there is no duration
            # that would make this holder *late* — saying "within the 0s
            # a launch may take" would read as the opposite of what 0
            # means. Report the fact and stop short of a verdict.
            verdict = (
                f'It has been in there {held:.0f}s. Launches are '
                f'unbounded here (VIBE_BROWSER_START_TIMEOUT_S=0), so '
                f'nothing can say whether that is a launch in progress '
                f'or a stuck holder — check Ziniao if it does not '
                f'clear.'
            )
        elif held > launch_cap:
            verdict = (
                f'It has been in there {held:.0f}s, past the '
                f'{launch_cap:.0f}s a launch may take, so it is stuck — '
                f'restart Ziniao (Settings → Ziniao) rather than '
                f'retrying.'
            )
        else:
            verdict = (
                f'It has been in there {held:.0f}s, within the '
                f'{launch_cap:.0f}s a launch may take, so this is '
                f'ordinary contention — retry shortly.'
            )
        return (
            f'Browser subsystem busy: {what} waited {waited:.0f}s for '
            f'the launch lock, which is held by {holder}. {verdict}'
        )

    @asynccontextmanager
    async def hold(self, what: str):
        """Take the lock, or raise :class:`BrowserBusyError` saying why not."""
        timeout = Options.BROWSER_LOCK_WAIT_S.get_float()
        started = time.monotonic()
        if timeout <= 0:
            await self._lock.acquire()
        else:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout)
            except TimeoutError:
                msg = self._busy_message(what, time.monotonic() - started)
                logger.warning('%s', msg)
                raise BrowserBusyError(msg) from None
        self._holder = what
        self._since = time.monotonic()
        try:
            yield
        finally:
            self._holder = None
            self._since = 0.0
            self._lock.release()
