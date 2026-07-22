"""Idle-browser sweeper — terminate browsers no task is using.

A store's browser (Ziniao env or Chrome), its aux browser, and the
store-less ``web`` browser all start lazily but, before this module,
were NEVER stopped: ``stop_session``'s only caller was store deletion,
so every launched browser lived until reboot, accumulating one tab per
agent navigation (the thousand-tab window).

Sweep rule, per browser, both conditions required:

- **no ACTIVE task is bound to it** — active means
  PENDING/QUEUED/DESIGNING/PLANNED/RUNNING; WAITING deliberately does
  NOT hold a browser (a parked task can wait hours and the wrapper
  lazily restarts the browser via ``browser/start`` on wake), and
- **its CDP mux has been idle ≥ ``VIBE_BROWSER_IDLE_S``** (default
  5 min; 0 disables the sweeper) with no connected clients — the mux
  timestamp also captures target events from a HUMAN using the window,
  so a browser being browsed by hand is not yanked away.

Termination reuses the existing per-store-safe paths
(``browser_manager.stop_session`` — which now really stops a Ziniao
env via stopBrowser — and ``aux_browser.stop_aux``), so the shared
Ziniao client is never touched (docs/ziniao-concurrency.md).

Registered as a 1-minute cron job (scheduler/cron.py); the idle window
does the real pacing. Lives in its own module (not daemon_reaper)
because it needs ``browser_manager``, which itself imports the daemon
reaper — same sweep-with-guards shape, one import direction.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.browser import aux_browser
from app.browser.base import BrowserSessionInfo
from app.browser.manager import WEB_BROWSER_SLUG, browser_manager
from app.database import async_session
from app.env_options import Options
from app.models.store import Store
from app.models.task import Task
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

# Statuses that hold a browser open. Compare daemon_reaper's
# _ACTIVE_STATUSES: WAITING keeps a *daemon's task* alive there, but a
# WAITING task must not pin a browser for hours — browsers restart
# lazily on wake.
_HOLD_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.DESIGNING,
    TaskStatus.PLANNED,
    TaskStatus.RUNNING,
}


async def _live_task_bindings() -> tuple[set[str], bool]:
    """(store_ids with a live task, any store-less live task?)."""
    async with async_session() as db:
        rows = await db.execute(
            select(Task.store_id).where(
                Task.status.in_([s.value for s in _HOLD_STATUSES])
            )
        )
        store_ids = set()
        nostore = False
        for (sid,) in rows.all():
            if sid:
                store_ids.add(sid)
            else:
                nostore = True
        return store_ids, nostore


def _proxy_busy(backend, idle_s: float) -> bool:
    """True when the backend's mux says the browser is in use.

    No proxy (already torn down / never started) reads as not-busy:
    the remaining signal is the task binding, which the caller has
    already checked.
    """
    proxy = getattr(backend, '_proxy', None)
    if proxy is None:
        return False
    return proxy.has_active_clients() or proxy.idle_seconds() < idle_s


async def _stop_web() -> None:
    """Stop the store-less ``web`` browser (manager lock held); the
    wrapper lazily restarts it via ``browser/web/start`` on next use."""
    async with browser_manager._lock:
        info = browser_manager._active_sessions.pop(WEB_BROWSER_SLUG, None)
        backend = browser_manager._backends.pop(WEB_BROWSER_SLUG, None)
        if backend is not None:
            await backend.stop(info or BrowserSessionInfo())


async def sweep_idle_browsers() -> int:
    """Stop every browser with no live task and an idle mux. Returns
    the number of browsers stopped."""
    idle_s = Options.BROWSER_IDLE_S.get_float()
    if idle_s <= 0:
        return 0

    hold_store_ids, nostore_active = await _live_task_bindings()
    stopped = 0

    # ── Store main browsers (Ziniao / Chrome / winchrome) ──
    for key in list(browser_manager._active_sessions.keys()):
        if key == WEB_BROWSER_SLUG:
            # Store-less web browser: held by any live no-store task.
            if nostore_active:
                continue
            backend = browser_manager._backends.get(key)
            if backend is None or _proxy_busy(backend, idle_s):
                continue
            try:
                await _stop_web()
                stopped += 1
                logger.info('Idle sweep: stopped web browser')
            except Exception:
                logger.warning(
                    'Idle sweep: web browser stop failed', exc_info=True
                )
            continue
        if key in hold_store_ids:
            continue
        backend = browser_manager._backends.get(key)
        if backend is None or _proxy_busy(backend, idle_s):
            continue
        async with async_session() as db:
            store = await db.get(Store, key)
            if store is None:
                continue
            try:
                await browser_manager.stop_session(store, db)
                stopped += 1
                logger.info(
                    'Idle sweep: stopped browser for store %s', store.name
                )
            except Exception:
                logger.warning(
                    'Idle sweep: stop failed for store %s',
                    store.name,
                    exc_info=True,
                )

    # ── Aux browsers ──
    for store_id in list(aux_browser._backends.keys()):
        if store_id in hold_store_ids:
            continue
        if _proxy_busy(aux_browser._backends.get(store_id), idle_s):
            continue
        try:
            if await aux_browser.stop_aux(store_id):
                stopped += 1
                logger.info(
                    'Idle sweep: stopped aux browser for store %s', store_id
                )
        except Exception:
            logger.warning(
                'Idle sweep: aux stop failed for %s', store_id, exc_info=True
            )

    return stopped
