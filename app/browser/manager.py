import asyncio
from datetime import UTC, datetime
import json
import logging
import os
import secrets
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_token
from app.browser import vendor_creds
from app.browser.base import BrowserBackend, BrowserSessionInfo

# Re-exported for their existing importers (routers, ai, tests):
from app.browser.bh_daemons import (  # noqa: F401 — re-export
    LEGACY_DAEMON_PATTERN,
    kill_aux_daemons,
    kill_bh_daemons,
)
from app.browser.bu_binary import (  # noqa: F401 — re-export
    warn_on_browser_use_version_mismatch,
)
from app.browser.cdp_probe import OURS, cdp_proxy_alive, proxy_ownership
from app.browser.daemon_reaper import reap_orphaned_daemons
from app.browser.launch_guards import (
    GlobalLaunchLock,
    RelaunchBudget,
    start_backend_bounded,
)
from app.browser.proxy_ports import (
    ProxyPortAllocator,
    default_proxy_base,
    resolve_proxy_base,
)
from app.browser.vendors import (
    PICKER_GENERIC,
    vendor_spec,
)
from app.browser.web_wrapper import write_web_browser_use_wrapper
from app.browser.wrapper import (
    remove_browser_use_wrapper,
    wipe_generated_wrappers,
    write_browser_use_wrapper,
)
from app.browser.ziniao_startup import (
    ZiniaoAccountConflictError,
    ensure_ziniao_for_account,
)
from app.config import (
    AI_BOT_USER_ID,
    WEB_BROWSER_SLUG,
)
from app.database import async_session
from app.env_options import Options
from app.models.app_settings import AppSettings
from app.models.browser_session import BrowserSession
from app.models.store import Store
from app.models.ziniao_account import ZiniaoAccount
from app.plugins import registered_browser_backends
from app.utils.crypto import decrypt_password

# Re-exported for their existing importers (routers, ai):
from app.utils.json_file import (  # noqa: F401 — re-export
    atomic_write_json,
    read_mcp_config,
)

logger = logging.getLogger(__name__)

# Base proxy port; each store gets a unique offset from it. This is a
# PREFERRED starting point only: every candidate port is ownership-
# checked and BOUND (see ProxyPortAllocator.allocate) before it is
# handed out, so a port held by another server instance, an agent-
# spawned CDP bridge or any other listener is skipped, whatever range
# it sits in. Demo mode still prefers 9322 (see
# proxy_ports.default_proxy_base) so a parallel demo runtime walks
# past the production server's 9222 — cosmetic now, not the safety
# property. ``VIBE_CDP_PROXY_BASE`` moves the whole per-instance range
# (proxy_ports.resolve_proxy_base): parallel test servers each get
# their own range instead of queueing for one 9222 holder.
_BASE_PROXY_PORT = default_proxy_base()

# app_settings key holding the orchestrator web browser's proxy port,
# so it survives server restarts (the store equivalent lives on
# BrowserSession.proxy_port; the store-less web browser has no such row
# and uses this kv entry instead).
_WEB_PROXY_PORT_KEY = 'web_browser_proxy_port'


async def inject_vendor_store_config(
    browser_config: dict, store, db: AsyncSession
) -> None:
    """Spec-driven vendors (registry): the bound profile ref + machine
    credentials for a store's vendor.

    Every vendor the GENERIC picker binds gets its chosen profile ref
    (whatever the picker bound, an env id or a name) injected from the
    ``browser_oauth`` column into ``browser_ref``, the value
    ``open_store`` receives. On top of that:

    - a vendor that DECLARES ``credential_fields`` gets its stored
      machine login injected (encrypted; so a logged-out client can be
      auto-logged-in instead of failing the task);
    - a credential-less vendor (empty ``credential_fields`` — the user
      signs in once in the client; the local API acts as that login)
      gets only the client-path override, when the spec declares one.

    Ziniao keeps its own branch in the manager (per-account
    credentials, custom picker); plain backends inject nothing. See
    app/browser/vendors.py.
    """
    spec = vendor_spec(store.browser_backend)
    if spec is None or spec.picker_kind != PICKER_GENERIC:
        return
    if store.browser_oauth and spec.lists_profiles:
        browser_config['browser_ref'] = store.browser_oauth
    if spec.credential_fields:
        await vendor_creds.inject_vendor_config(browser_config, db, spec)
    elif spec.client_path_setting:
        await vendor_creds.inject_client_path(browser_config, db, spec)


class BrowserManager:
    """Manages browser sessions for all stores.

    Each store gets its own backend instance, proxy port, and
    browser-use wrapper script. Calls are serialized via an
    asyncio lock to avoid races.
    """

    def __init__(self):
        # Per-store backend instances (keyed by store_id)
        self._backends: dict[str, BrowserBackend] = {}
        self._active_sessions: dict[str, BrowserSessionInfo] = {}
        # Identity of THIS server instance, for CDP-proxy ownership
        # (PR C1). Every CDPMuxProxy this manager starts serves the id
        # on ``GET /vibe/instance``; the allocator and the wrappers
        # refuse to reuse or attach to a listener that does not answer
        # with it. That is what makes two vibe-seller servers on one
        # machine unable to hand out — or glue an agent onto — the same
        # proxy port. Single-instance machines (every real user today)
        # keep the exact same ports: the first free port from the base
        # is still 9222/9322.
        #
        # Known limitation, deliberately not "fixed" here: two servers
        # CAN both drive the single GUI client of an inject-driven
        # vendor (悦互联, 稳卖's login form). The #326 machine-wide
        # inspector lock serialises inspector sessions, but not whole
        # GUI flows across servers (one server's store-form listing
        # while another runs a task). Production runs one server per
        # machine, so this is a test-environment hazard — see
        # docs/browser.md § Multiple server instances.
        self._instance_id = secrets.token_hex(8)
        # Proxy-port allocation, keyed by store (PR C1): bind-before-
        # use reservations owned by THIS instance id. See
        # app.browser.proxy_ports — kept in its own module because the
        # reservation lifecycle (allocate / handoff to the mux /
        # release on teardown) is a unit of its own.
        self.ports = ProxyPortAllocator(resolve_proxy_base(), self._instance_id)
        # Serialize start/stop to avoid races
        # Bounded on purpose — a wedged holder must not turn every
        # later caller into a request that never answers.
        self._lock = GlobalLaunchLock()
        # Track the active Ziniao account (only one can run
        # per machine).
        self._active_ziniao_account_id: str | None = None
        # store_id -> store_name for active ziniao stores
        self._ziniao_stores: dict[str, str] = {}
        # Circuit breaker for the dead-mux relaunch path — every
        # browser-use call can reach it via the wrapper, so without a
        # budget a wedged Ziniao client becomes a restart storm that
        # deepens the wedge and wipes agent tabs mid-task.
        self._relaunches = RelaunchBudget()

    @property
    def instance_id(self) -> str:
        """This server instance's CDP-proxy ownership id."""
        return self._instance_id

    async def _cdp_alive(self, port: int, timeout: float = 2.0) -> bool:
        """Is the CDP proxy on *port* OURS, with a LIVE browser behind it?

        Two dimensions, both load-bearing (found live by a real task
        after a browser crash):

        - **Ownership**: liveness alone was the cross-server bug — two
          servers allocate from the same base port, and a probe that
          accepted any 200-answer reused a foreign proxy, driving
          ANOTHER server's store browser. A listener that does not
          answer ``/vibe/instance`` with OUR instance id is foreign
          (see app.browser.cdp_probe.proxy_ownership).
        - **Upstream liveness**: the mux serves the ownership handshake
          ITSELF, so a 200 there says nothing about the browser behind
          it. When the browser dies but the mux survives, the mux
          answers ``/json/version`` with 502 — without this dimension
          ``browser/start`` early-returns "alive" without relaunching,
          and every agent call in the window dies with "WebSocket
          connection closed" until the mux's own ~3-minute backoff
          tears it down.
        """
        if await proxy_ownership(port, self._instance_id, timeout) != OURS:
            return False
        return await cdp_proxy_alive(port, timeout)

    async def _cdp_alive_with_retry(
        self,
        port: int,
        attempts: int = 3,
        gap: float = 1.0,
    ) -> bool:
        """Multi-attempt liveness-and-ownership probe.

        A single 2 s timeout on the ownership endpoint is too tight a
        gate to trigger a full Ziniao+proxy restart — the proxy may be
        momentarily slow under CDP load while still healthy. Retry a
        few times with a short gap; only when every attempt fails do we
        conclude the upstream is genuinely dead (or the port foreign —
        same recovery: teardown and reallocate). Returns True on the
        first success.
        """
        for i in range(attempts):
            if await self._cdp_alive(port):
                return True
            if i < attempts - 1:
                await asyncio.sleep(gap)
        return False

    async def cleanup_stale_sessions(self) -> int:
        """Mark stale 'running' sessions as idle and kill orphans.

        Called on server startup — in-memory state is empty so
        any DB session still marked 'running' is stale.
        Also kills any orphaned browser-use daemons from the
        previous server run, wipes stale wrapper scripts, and warns
        on a browser-use version mismatch (in-place-upgrade safety —
        see docs/browser-use-0.13-migration.md §8.4).
        Returns the number of sessions cleaned up.
        """
        # In-place-upgrade safety, in order:
        #  (c) warn if the installed browser-use can't drive the wrapper
        #      shape this code emits (>=0.13),
        #  (a) wipe stale wrapper scripts so a pre-upgrade (0.12-shaped)
        #      wrapper is never invoked before it self-heals on the next
        #      task launch, and orphaned ones whose store is gone (their
        #      frozen proxy_port can later point at a LIVE store),
        #  (b) reap orphaned daemons (both 0.13 pid-file + legacy 0.12
        #      cmdline) — preserves daemons for active tasks (e.g. WAITING
        #      tasks that survive a restart).
        warn_on_browser_use_version_mismatch()
        async with async_session() as db:
            rows = await db.execute(select(Store.id))
            live_store_ids = {sid for (sid,) in rows.all()}
        wipe_generated_wrappers(live_store_ids)
        await reap_orphaned_daemons()

        async with async_session() as db:
            result = await db.execute(
                select(BrowserSession).where(BrowserSession.status == 'running')
            )
            stale = result.scalars().all()
            for s in stale:
                s.status = 'idle'
                s.cdp_port = None
                s.chrome_pid = None
                s.proxy_port = None
                s.updated_at = datetime.now(UTC).isoformat()
            if stale:
                await db.commit()
                logger.info(
                    'Cleaned up %d stale browser session(s)',
                    len(stale),
                )
            return len(stale)

    def _get_backend(self, store_id: str, backend_type: str) -> BrowserBackend:
        """Get or create a per-store backend instance.

        Both Chrome and Ziniao use CDPMuxProxy for multi-task
        multiplexing and shared cookie context.
        """
        if store_id not in self._backends:
            backends = registered_browser_backends()
            cls = backends.get(backend_type)
            if cls is None:
                raise ValueError(f'Unsupported browser backend: {backend_type}')
            self._backends[store_id] = cls()
        return self._backends[store_id]

    async def start_session(
        self, store: Store, db: AsyncSession
    ) -> BrowserSession:
        """Start or reuse a browser session for a store.

        Serialized via lock to prevent wrapper-generation races. A Ziniao
        store's client is brought up first, OUTSIDE the lock: a client
        (re)start can take minutes and must not hold up every other
        store's launch. Both waits share one budget
        (``VIBE_BROWSER_LOCK_WAIT_S``), so the route answers — 503 while
        Ziniao is still starting — before the wrapper's 90 s curl ends.
        """
        budget = Options.BROWSER_LOCK_WAIT_S.get_float()
        end = time.monotonic() + budget if budget > 0 else None
        await self.ensure_ziniao_ready(
            store, db, wait_s=budget if end else None, session_start=True
        )
        wait = max(end - time.monotonic(), 1.0) if end else None
        async with self._lock.hold(f'start_session({store.name})', wait):
            return await self._start_session_locked(store, db)

    @staticmethod
    async def _read_headless_setting(db: AsyncSession) -> bool:
        """Read the system-wide browser_headless app setting.

        The CI=true env var (set automatically by GitHub Actions) always
        forces headless regardless of the DB value, so CI never needs a
        virtual display and runs at full headless speed.
        """
        if os.environ.get('CI') == 'true':
            return True
        row = await db.get(AppSettings, 'browser_headless')
        return bool(row and row.value == 'true')

    async def _start_session_locked(
        self, store: Store, db: AsyncSession
    ) -> BrowserSession:
        # Check if session already exists and is running
        result = await db.execute(
            select(BrowserSession).where(BrowserSession.store_id == store.id)
        )
        session = result.scalar_one_or_none()
        if (
            session
            and session.status == 'running'
            and store.id in self._active_sessions
        ):
            # Verify CDP proxy is actually alive before
            # returning early — Ziniao may have crashed.
            proxy_port = self.ports.get(store.id)
            # Chrome stores pre-CDPMuxProxy may lack a proxy port.
            # Allocate one and force a full restart.
            if not proxy_port:
                logger.info(
                    'No proxy port for %s — forcing full restart',
                    store.name,
                )
                self._active_sessions.pop(store.id, None)
            elif not await self._cdp_alive_with_retry(proxy_port):
                # Tearing the env down and relaunching is the recovery,
                # but it is reachable from EVERY browser-use call via
                # the wrapper. Budget it per store, else a wedged
                # Ziniao client produces an endless stop/start storm
                # (each cycle also destroys the agent's tabs and login
                # state mid-task). Past the budget, fail loudly.
                self._relaunches.note(store.id, store.name)
                logger.warning(
                    'CDP proxy :%s not responding for %s — forcing restart',
                    proxy_port,
                    store.name,
                )
                # Clean up stale state so the code below
                # does a full restart.
                self._active_sessions.pop(store.id, None)
                self._ziniao_stores.pop(store.id, None)
                # Drop the port reservation too: "not alive" now also
                # means "held by someone else", and re-allocating from
                # the DB-saved port is what moves us off it.
                self.ports.pop(store.id)
                if store.id in self._backends:
                    try:
                        await self._backends[store.id].stop(
                            BrowserSessionInfo()
                        )
                    except Exception as e:
                        logger.warning(
                            'Error stopping stale backend for %s: %s',
                            store.name,
                            e,
                        )
                    del self._backends[store.id]
            else:
                # CDP alive — just re-write wrapper
                token = create_token(AI_BOT_USER_ID, 'ai_bot')
                headless = await self._read_headless_setting(db)
                await asyncio.to_thread(
                    write_browser_use_wrapper,
                    store.name,
                    store.browser_backend,
                    proxy_port,
                    api_token=token,
                    store_id=store.id,
                    headless=headless,
                    slug=store.slug,
                    instance_id=self._instance_id,
                )
                return session

        browser_config = (
            json.loads(store.browser_config) if store.browser_config else {}
        )

        # For ziniao stores with a linked account, inject creds
        if store.browser_backend == 'ziniao' and store.ziniao_account_id:
            self.refuse_other_ziniao_account(
                store.ziniao_account_id, f'store "{store.name}"'
            )
            account = await db.get(ZiniaoAccount, store.ziniao_account_id)
            if account:
                browser_config['company'] = account.company
                browser_config['username'] = account.username
                browser_config['password'] = decrypt_password(
                    account.encrypted_password
                )
                browser_config['socket_port'] = account.socket_port
                browser_config['client_path'] = account.client_path or 'ziniao'
            if store.browser_oauth:
                browser_config['browser_oauth'] = store.browser_oauth

        await inject_vendor_store_config(browser_config, store, db)

        # Allocate unique proxy port — both backends use CDPMuxProxy.
        # Reuse the DB-persisted port only when it is still FREE on
        # this machine; a foreign holder is skipped (see
        # ProxyPortAllocator.allocate).
        existing_port = session.proxy_port if session else None
        proxy_port = await self.ports.allocate(store.id, existing_port)
        browser_config['proxy_port'] = proxy_port
        browser_config['vibe_instance_id'] = self._instance_id
        # Hand the allocator's bound socket to the mux (bind-before-use:
        # the port was reserved at allocation; the mux serves on THIS
        # socket, so the allocation and the listener cannot diverge).
        browser_config['proxy_socket'] = self.ports.sockets.get(proxy_port)
        browser_config['store_slug'] = store.slug

        # System-wide headless preference from app_settings. The Chrome
        # backend (also used as the Ziniao "aux" session for non-Amazon
        # URLs) reads `headless` from this dict. Per-store override is
        # still respected — only inject if the store didn't set one.
        if 'headless' not in browser_config:
            if os.environ.get('CI') == 'true':
                browser_config['headless'] = True
            else:
                row = await db.get(AppSettings, 'browser_headless')
                browser_config['headless'] = bool(row and row.value == 'true')

        backend = self._get_backend(store.id, store.browser_backend)

        # Write the wrapper BEFORE backend.start(): the wrapper is the ONLY
        # safe entrypoint, so a failed/stale launch must still leave one —
        # else the agent's bare `browser-use` falls through PATH to the real
        # binary → the user's local Chrome. See docs/browser.md § Wrapper
        # safety.
        token = create_token(AI_BOT_USER_ID, 'ai_bot')
        await asyncio.to_thread(
            write_browser_use_wrapper,
            store.name,
            store.browser_backend,
            proxy_port,
            api_token=token,
            store_id=store.id,
            headless=bool(browser_config.get('headless', False)),
            slug=store.slug,
            instance_id=self._instance_id,
        )

        logger.info(
            'Starting browser for store %s (backend=%s)',
            store.name,
            store.browser_backend,
        )
        # Bounded — runs under the GLOBAL _lock, so a store stuck in its
        # own retry loop would otherwise block every other store's
        # launch. See app/browser/launch_guards.py.
        try:
            info = await start_backend_bounded(
                backend, browser_config, store.name
            )
        except RuntimeError:
            self._backends.pop(store.id, None)
            # A start that never served must not keep the port reserved
            # either — release it so the next attempt re-allocates from
            # a clean slate.
            self.ports.pop(store.id)
            raise
        # The mux now owns the reservation socket (it served on it);
        # drop our reference without closing — the socket belongs to
        # the mux's server and is closed by its stop().
        self.ports.handoff(proxy_port)
        self._active_sessions[store.id] = info
        # A healthy launch clears the relaunch budget — the breaker
        # should only trip on a *run* of failures, not on occasional
        # recoveries spread over a long session.
        self._relaunches.clear(store.id)

        # Track active Ziniao account
        if store.browser_backend == 'ziniao' and store.ziniao_account_id:
            self._active_ziniao_account_id = store.ziniao_account_id
            self._ziniao_stores[store.id] = store.name

        now = datetime.now(UTC).isoformat()
        if session:
            session.cdp_port = info.cdp_port
            session.chrome_pid = info.pid
            session.proxy_port = proxy_port
            session.status = 'running'
            session.started_at = now
            session.updated_at = now
        else:
            session = BrowserSession(
                store_id=store.id,
                cdp_port=info.cdp_port,
                chrome_pid=info.pid,
                proxy_port=proxy_port,
                status='running',
                started_at=now,
                updated_at=now,
            )
            db.add(session)

        await db.commit()
        await db.refresh(session)
        return session

    async def stop_session(self, store: Store, db: AsyncSession) -> None:
        async with self._lock.hold(f'stop_session({store.name})'):
            await self._stop_session_locked(store, db)

    async def _stop_session_locked(
        self, store: Store, db: AsyncSession
    ) -> None:
        # Teardown is TOTAL, not conditional on the active-session
        # record. The old `if info:` guard skipped backend.stop() when
        # the in-memory session was already gone (a failed earlier
        # stop, a mid-flight restart) while `_backends` still held the
        # backend — its CDPMuxProxy kept LISTENING on the proxy port
        # long after the browser was gone (observed live: a swept store
        # left the mux holding its port until the next server boot).
        # A leaked listener is also the direct fuel for cross-server
        # port confusion, so stop whatever backend exists, guard each
        # phase, and always clear the bookkeeping.
        info = self._active_sessions.pop(store.id, None)
        backend = self._backends.get(store.id)
        if backend is not None:
            try:
                await backend.stop(info or BrowserSessionInfo())
            except Exception as e:
                logger.warning(
                    'Error stopping browser backend for store %s: %s',
                    store.name,
                    e,
                )

        # Clean up backend instance
        self._backends.pop(store.id, None)
        self.ports.pop(store.id)

        # Clear Ziniao account tracking if last store stopped
        self._ziniao_stores.pop(store.id, None)
        if not self._ziniao_stores:
            self._active_ziniao_account_id = None

        # Remove browser-use wrapper
        await asyncio.to_thread(remove_browser_use_wrapper, store.slug)

        result = await db.execute(
            select(BrowserSession).where(BrowserSession.store_id == store.id)
        )
        session = result.scalar_one_or_none()
        if session:
            session.status = 'idle'
            session.cdp_port = None
            session.chrome_pid = None
            session.proxy_port = None
            session.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

    async def write_browser_config_for_store(
        self, store: Store, db: AsyncSession
    ) -> None:
        """Generate a per-store browser-use wrapper script.

        Creates ``~/.vibe-seller/bin/{slug}/browser-use`` that
        validates sessions, blocks dangerous flags, injects
        ``--cdp-url``, and auto-starts the CDP proxy via an
        authenticated API call.  No browser is started here;
        the agent invokes the wrapper later.
        """
        async with self._lock.hold('write_browser_config_for_store'):
            # Both backends need a proxy port for CDPMuxProxy.
            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store.id
                )
            )
            session = result.scalar_one_or_none()
            existing_port = session.proxy_port if session else None
            proxy_port = await self.ports.allocate(store.id, existing_port)

            # Generate a short-lived token so the wrapper's
            # auto-start curl can authenticate against the API.
            token = create_token(AI_BOT_USER_ID, 'ai_bot')
            headless = await self._read_headless_setting(db)

            await asyncio.to_thread(
                write_browser_use_wrapper,
                store.name,
                store.browser_backend,
                proxy_port,
                api_token=token,
                store_id=store.id,
                headless=headless,
                slug=store.slug,
                instance_id=self._instance_id,
            )

    async def _web_proxy_port(self, db: AsyncSession) -> int:
        """Allocate (and persist) the web browser's proxy port.

        Reuses the kv-persisted port across restarts, mirroring how
        stores reuse ``BrowserSession.proxy_port`` — but only when the
        saved port is still free; a port now held by another server
        instance is reallocated (and the new value persisted).
        """
        row = await db.get(AppSettings, _WEB_PROXY_PORT_KEY)
        existing = int(row.value) if row and row.value.isdigit() else None
        proxy_port = await self.ports.allocate(WEB_BROWSER_SLUG, existing)
        if existing != proxy_port:
            if row:
                row.value = str(proxy_port)
            else:
                db.add(
                    AppSettings(key=_WEB_PROXY_PORT_KEY, value=str(proxy_port))
                )
            await db.commit()
        return proxy_port

    async def write_web_browser_config(self, db: AsyncSession) -> None:
        """Generate the store-less orchestrator ``web`` browser wrapper.

        Creates ``~/.vibe-seller/bin/_web/browser-use``. No browser is
        started here — the wrapper lazy-starts it via
        ``POST /api/browser/web/start`` on first use, so no-store tasks
        that never touch the browser pay nothing.
        """
        async with self._lock.hold('write_web_browser_config'):
            proxy_port = await self._web_proxy_port(db)
            token = create_token(AI_BOT_USER_ID, 'ai_bot')
            headless = await self._read_headless_setting(db)
            await asyncio.to_thread(
                write_web_browser_use_wrapper,
                proxy_port,
                api_token=token,
                headless=headless,
                instance_id=self._instance_id,
            )

    async def write_task_browser_config(
        self, store: Store | None, db: AsyncSession
    ) -> None:
        """Write the right browser-use wrapper for a task.

        The single entry point every launch path (auto-run, execute-plan,
        woken) uses so a fresh, correctly-scoped wrapper (per-store or the
        store-less web browser) is always on disk with a current token —
        regardless of which path runs the task.
        """
        if store:
            await self.write_browser_config_for_store(store, db)
        else:
            await self.write_web_browser_config(db)

    async def start_web_session(self, db: AsyncSession) -> int:
        """Start (or reuse) the store-less orchestrator ``web`` browser.

        A single generic Chrome instance shared by all no-store tasks;
        per-task tab isolation is provided by CDPMuxProxy keyed on
        ``VIBE_TASK_ID`` (same as stores). Serialized via the lock so
        concurrent orchestrator tasks racing the lazy auto-start can't
        launch two Chromes. Returns the proxy port (the web wrapper's
        start API hands it back so a reallocated port is followed).
        """
        async with self._lock.hold('start_web_session'):
            proxy_port = await self._web_proxy_port(db)
            if (
                WEB_BROWSER_SLUG in self._active_sessions
                and await self._cdp_alive_with_retry(proxy_port)
            ):
                return proxy_port  # already up
            # Tear down any stale backend before a fresh launch.
            if WEB_BROWSER_SLUG in self._backends:
                try:
                    await self._backends[WEB_BROWSER_SLUG].stop(
                        BrowserSessionInfo()
                    )
                except Exception as e:
                    logger.warning('Error stopping stale web backend: %s', e)
                del self._backends[WEB_BROWSER_SLUG]
                self._active_sessions.pop(WEB_BROWSER_SLUG, None)

            headless = await self._read_headless_setting(db)
            browser_config = {
                'proxy_port': proxy_port,
                'vibe_instance_id': self._instance_id,
                'proxy_socket': self.ports.sockets.get(proxy_port),
                'store_slug': WEB_BROWSER_SLUG,
                'headless': headless,
            }
            backend = self._get_backend(WEB_BROWSER_SLUG, 'chrome')
            logger.info('Starting orchestrator web browser (chrome)')
            info = await backend.start(browser_config)
            self._active_sessions[WEB_BROWSER_SLUG] = info
            # The mux owns the reservation socket now (see
            # _start_session_locked for the hand-off contract).
            self.ports.handoff(proxy_port)
            return proxy_port

    def refuse_other_ziniao_account(self, account_id: str, who: str) -> None:
        """Only one Ziniao account can run per machine. Different profiles
        (browserOauth) on the SAME account are fine; another account's
        live stores are never killed to make room."""
        active_id = self._active_ziniao_account_id
        if active_id is not None and active_id != account_id:
            active = ', '.join(self._ziniao_stores.values())
            raise ZiniaoAccountConflictError(
                f'Cannot start Ziniao for {who}: Ziniao account conflict. '
                f'Store(s) [{active}] are using a different Ziniao '
                f'account. Only one Ziniao account can run at a time. '
                f'Please stop the browser session for [{active}] first.'
            )

    def has_live_ziniao(self) -> bool:
        """Ziniao browsers open, or a launch in progress, on this server —
        a slow client is then busy, not dead, and is never killed."""
        return bool(self._ziniao_stores) or self._lock.holder is not None

    async def ensure_ziniao_ready(
        self,
        store: Store,
        db: AsyncSession,
        *,
        wait_s: float | None = None,
        session_start: bool = False,
    ) -> None:
        """Bring a Ziniao store's client up in WebDriver mode.

        Kills and relaunches it when it is not answering (normal mode,
        not running, leftovers) — never asks, never needs a flag. Raises
        when that fails. No-op for other stores.
        """
        if store.browser_backend != 'ziniao' or not store.ziniao_account_id:
            return
        who = f'store "{store.name}"'
        self.refuse_other_ziniao_account(store.ziniao_account_id, who)
        await ensure_ziniao_for_account(
            db,
            store.ziniao_account_id,
            who,
            wait_s=wait_s,
            live=self.has_live_ziniao(),
            session_start=session_start,
        )

    async def ensure_session(
        self, store: Store, db: AsyncSession
    ) -> BrowserSession:
        """Ensure a browser session is running, starting if needed.

        Always rewrites the wrapper so the agent sees the
        current CDP endpoint.
        """
        return await self.start_session(store, db)

    def get_browser(self, store_id: str):
        """Get the Playwright Browser object for a store."""
        info = self._active_sessions.get(store_id)
        return info.browser if info else None

    def get_cdp_port(self, store_id: str) -> int | None:
        info = self._active_sessions.get(store_id)
        return info.cdp_port if info else None


# Singleton
browser_manager = BrowserManager()
