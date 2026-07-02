import asyncio
from datetime import UTC, datetime
import json
import logging
import os
import re
import tempfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_token
from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.daemon_reaper import reap_orphaned_daemons
from app.browser.web_wrapper import write_web_browser_use_wrapper
from app.browser.wrapper import (
    remove_browser_use_wrapper,
    store_slug,
    write_browser_use_wrapper,
)
from app.browser.ziniao_utils import ensure_ziniao_running
from app.config import (
    AI_BOT_USER_ID,
    DEMO_MODE,
    LOCALHOST,
    WEB_BROWSER_SLUG,
)
from app.database import async_session
from app.models.app_settings import AppSettings
from app.models.browser_session import BrowserSession
from app.models.store import Store
from app.models.ziniao_account import ZiniaoAccount
from app.platform import find_processes_by_pattern, kill_process
from app.plugins import registered_browser_backends
from app.utils.crypto import decrypt_password
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# Base proxy port; each store gets a unique offset.
# Demo mode shifts to 9322 so a parallel demo runtime doesn't collide
# with the production server's CDP proxy at 9222 — both backends would
# otherwise try to bind the same port, and worse, the wrapper's
# "is the proxy alive?" probe would silently glue the demo agent into
# whichever backend won the bind.
_BASE_PROXY_PORT = 9322 if DEMO_MODE else 9222

# app_settings key holding the orchestrator web browser's proxy port,
# so it survives server restarts (the store equivalent lives on
# BrowserSession.proxy_port; the store-less web browser has no such row
# and uses this kv entry instead).
_WEB_PROXY_PORT_KEY = 'web_browser_proxy_port'


async def kill_aux_daemons() -> int:
    """Terminate every browser-use daemon whose --session ends in
    ``-aux``. Returns the count killed.

    Uses ``find_processes_by_pattern`` + ``kill_process`` (psutil) so
    it works on all platforms — the old ``ps ax`` / ``os.kill`` path
    was Unix-only.
    """
    daemons: list[int] = []
    procs = await find_processes_by_pattern('browser_use.skill_cli.daemon')
    for pid, cmdline in procs.items():
        # Match aux session names — any session ending in "-aux".
        if re.search(r'--session[= ][^ ]*-aux\b', cmdline):
            daemons.append(pid)
    for pid in daemons:
        await kill_process(pid)
    if daemons:
        logger.info(
            'Killed %d aux browser-use daemon(s) on settings change: %s',
            len(daemons),
            daemons,
        )
    return len(daemons)


async def _kill_all_browser_daemons() -> int:
    """Kill all browser-use daemon processes (startup only).

    Intended for server startup when no tasks are running yet.
    Kills every ``browser_use.skill_cli.daemon`` process found.
    Returns the number of processes killed.
    """
    try:
        daemons = await find_processes_by_pattern(
            'browser_use.skill_cli.daemon',
        )
        for pid in daemons:
            await kill_process(pid)
        if daemons:
            logger.info(
                'Startup: killed %d browser-use daemon(s) '
                'from previous run: %s',
                len(daemons),
                list(daemons.keys()),
            )
        return len(daemons)
    except Exception:
        return 0


def atomic_write_json(path, data):
    """Write JSON atomically via tempfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_mcp_config():
    """Read .mcp.json, returning empty config if missing."""
    mcp_path = VIBE_SELLER_DIR / '.mcp.json'
    if mcp_path.exists():
        return json.loads(mcp_path.read_text())
    return {'mcpServers': {}}


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
        # Track proxy port allocations: store_id -> proxy_port
        self._proxy_ports: dict[str, int] = {}
        self._next_proxy_port = _BASE_PROXY_PORT
        # Serialize start/stop to avoid races
        self._lock = asyncio.Lock()
        # Track the active Ziniao account (only one can run
        # per machine).
        self._active_ziniao_account_id: str | None = None
        # store_id -> store_name for active ziniao stores
        self._ziniao_stores: dict[str, str] = {}

    @staticmethod
    async def _cdp_alive(port: int, timeout: float = 2.0) -> bool:
        """Quick TCP check to see if the CDP proxy is responding."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(LOCALHOST, port),
                timeout=timeout,
            )
            # Send minimal HTTP request
            request = (
                f'GET /json/version HTTP/1.0\r\n'
                f'Host: {LOCALHOST}:{port}\r\n'
                f'\r\n'
            )
            writer.write(request.encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(256), timeout=timeout)
            writer.close()
            return b'200' in data[:50]
        except Exception:
            return False

    @classmethod
    async def _cdp_alive_with_retry(
        cls,
        port: int,
        attempts: int = 3,
        gap: float = 1.0,
    ) -> bool:
        """Multi-attempt liveness probe.

        A single 2 s timeout on `/json/version` is too tight a gate to
        trigger a full Ziniao+proxy restart — the proxy may be momentarily
        slow under CDP load while still healthy. Retry a few times with
        a short gap; only when every attempt fails do we conclude the
        upstream is genuinely dead (the failure mode PR #68 was added
        to recover from). Returns True on the first success.
        """
        for i in range(attempts):
            if await cls._cdp_alive(port):
                return True
            if i < attempts - 1:
                await asyncio.sleep(gap)
        return False

    async def cleanup_stale_sessions(self) -> int:
        """Mark stale 'running' sessions as idle and kill orphans.

        Called on server startup — in-memory state is empty so
        any DB session still marked 'running' is stale.
        Also kills any orphaned browser-use daemons from the
        previous server run.
        Returns the number of sessions cleaned up.
        """
        # Kill orphaned daemons using the same logic as the
        # periodic reaper — preserves daemons for active tasks
        # (e.g. WAITING tasks that survive server restart).
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

    def _allocate_proxy_port(
        self, store_id: str, existing_port: int | None = None
    ) -> int:
        """Get a unique proxy port for a store.

        If the store already has a persisted proxy_port in the DB,
        reuse it to survive server restarts.
        """
        if store_id in self._proxy_ports:
            return self._proxy_ports[store_id]
        if existing_port is not None:
            self._proxy_ports[store_id] = existing_port
            # Keep _next_proxy_port above any restored port
            if existing_port >= self._next_proxy_port:
                self._next_proxy_port = existing_port + 1
            return existing_port
        port = self._next_proxy_port
        self._next_proxy_port += 1
        self._proxy_ports[store_id] = port
        return port

    async def start_session(
        self, store: Store, db: AsyncSession
    ) -> BrowserSession:
        """Start or reuse a browser session for a store.

        Serialized via lock to prevent wrapper-generation races.
        """
        async with self._lock:
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
            proxy_port = self._proxy_ports.get(store.id)
            # Chrome stores pre-CDPMuxProxy may lack a proxy port.
            # Allocate one and force a full restart.
            if not proxy_port:
                logger.info(
                    'No proxy port for %s — forcing full restart',
                    store.name,
                )
                self._active_sessions.pop(store.id, None)
            elif not await self._cdp_alive_with_retry(proxy_port):
                logger.warning(
                    'CDP proxy :%s not responding for %s — forcing restart',
                    proxy_port,
                    store.name,
                )
                # Clean up stale state so the code below
                # does a full restart.
                self._active_sessions.pop(store.id, None)
                self._ziniao_stores.pop(store.id, None)
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
                write_browser_use_wrapper(
                    store.name,
                    store.browser_backend,
                    proxy_port,
                    api_token=token,
                    store_id=store.id,
                    headless=headless,
                )
                return session

        browser_config = (
            json.loads(store.browser_config) if store.browser_config else {}
        )

        # For ziniao stores with a linked account, inject creds
        if store.browser_backend == 'ziniao' and store.ziniao_account_id:
            # Only one Ziniao account can be active per machine.
            # Different profiles (browserOauth) on the SAME
            # account are fine; different accounts are not.
            if (
                self._active_ziniao_account_id is not None
                and self._active_ziniao_account_id != store.ziniao_account_id
            ):
                active = ', '.join(self._ziniao_stores.values())
                raise RuntimeError(
                    f'Cannot start browser for store '
                    f'"{store.name}": Ziniao account '
                    f'conflict. Store(s) [{active}] are '
                    f'using a different Ziniao account. '
                    f'Only one Ziniao account can run at '
                    f'a time. Please stop the browser '
                    f'session for [{active}] first.'
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

        # Allocate unique proxy port — both backends use CDPMuxProxy.
        # Reuse the DB-persisted port if available.
        existing_port = session.proxy_port if session else None
        proxy_port = self._allocate_proxy_port(store.id, existing_port)
        browser_config['proxy_port'] = proxy_port
        browser_config['store_slug'] = store_slug(store.name, store.id)

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

        logger.info(
            'Starting browser for store %s (backend=%s)',
            store.name,
            store.browser_backend,
        )
        info = await backend.start(browser_config)
        self._active_sessions[store.id] = info

        # Track active Ziniao account
        if store.browser_backend == 'ziniao' and store.ziniao_account_id:
            self._active_ziniao_account_id = store.ziniao_account_id
            self._ziniao_stores[store.id] = store.name

        # Generate browser-use wrapper script
        token = create_token(AI_BOT_USER_ID, 'ai_bot')
        write_browser_use_wrapper(
            store.name,
            store.browser_backend,
            proxy_port,
            api_token=token,
            store_id=store.id,
            headless=bool(browser_config.get('headless', False)),
        )

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
        async with self._lock:
            await self._stop_session_locked(store, db)

    def remove_browser_entry(
        self,
        store_name: str,
        backend: str,
        store_id: str | None = None,
    ) -> None:
        """Remove a store's browser-use wrapper."""
        remove_browser_use_wrapper(store_name, store_id)

    async def _stop_session_locked(
        self, store: Store, db: AsyncSession
    ) -> None:
        info = self._active_sessions.pop(store.id, None)
        if info:
            backend = self._backends.get(store.id)
            if backend:
                await backend.stop(info)

        # Clean up backend instance
        self._backends.pop(store.id, None)
        self._proxy_ports.pop(store.id, None)

        # Clear Ziniao account tracking if last store stopped
        self._ziniao_stores.pop(store.id, None)
        if not self._ziniao_stores:
            self._active_ziniao_account_id = None

        # Remove browser-use wrapper
        remove_browser_use_wrapper(store.name, store.id)

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
        async with self._lock:
            # Both backends need a proxy port for CDPMuxProxy.
            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store.id
                )
            )
            session = result.scalar_one_or_none()
            existing_port = session.proxy_port if session else None
            proxy_port = self._allocate_proxy_port(store.id, existing_port)

            # Generate a short-lived token so the wrapper's
            # auto-start curl can authenticate against the API.
            token = create_token(AI_BOT_USER_ID, 'ai_bot')
            headless = await self._read_headless_setting(db)

            write_browser_use_wrapper(
                store.name,
                store.browser_backend,
                proxy_port,
                api_token=token,
                store_id=store.id,
                headless=headless,
            )

    async def _web_proxy_port(self, db: AsyncSession) -> int:
        """Allocate (and persist) the web browser's proxy port.

        Reuses the kv-persisted port across restarts, mirroring how
        stores reuse ``BrowserSession.proxy_port``.
        """
        row = await db.get(AppSettings, _WEB_PROXY_PORT_KEY)
        existing = int(row.value) if row and row.value.isdigit() else None
        proxy_port = self._allocate_proxy_port(WEB_BROWSER_SLUG, existing)
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
        async with self._lock:
            proxy_port = await self._web_proxy_port(db)
            token = create_token(AI_BOT_USER_ID, 'ai_bot')
            headless = await self._read_headless_setting(db)
            write_web_browser_use_wrapper(
                proxy_port,
                api_token=token,
                headless=headless,
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

    async def start_web_session(self, db: AsyncSession) -> None:
        """Start (or reuse) the store-less orchestrator ``web`` browser.

        A single generic Chrome instance shared by all no-store tasks;
        per-task tab isolation is provided by CDPMuxProxy keyed on
        ``VIBE_TASK_ID`` (same as stores). Serialized via the lock so
        concurrent orchestrator tasks racing the lazy auto-start can't
        launch two Chromes.
        """
        async with self._lock:
            proxy_port = await self._web_proxy_port(db)
            if (
                WEB_BROWSER_SLUG in self._active_sessions
                and await self._cdp_alive_with_retry(proxy_port)
            ):
                return  # already up
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
                'store_slug': WEB_BROWSER_SLUG,
                'headless': headless,
            }
            backend = self._get_backend(WEB_BROWSER_SLUG, 'chrome')
            logger.info('Starting orchestrator web browser (chrome)')
            info = await backend.start(browser_config)
            self._active_sessions[WEB_BROWSER_SLUG] = info

    async def check_ziniao_reachable(
        self, store: Store, db: AsyncSession
    ) -> None:
        """Pre-check that Ziniao is reachable for a ziniao store.

        Raises RuntimeError with a user-friendly message if Ziniao
        cannot be reached (WSL fails fast in ~3s; Mac may
        auto-launch). No-op for non-ziniao stores.
        """
        if store.browser_backend != 'ziniao':
            return
        if not store.ziniao_account_id:
            return

        account = await db.get(ZiniaoAccount, store.ziniao_account_id)
        if not account:
            raise RuntimeError(
                f'Ziniao account not found for store "{store.name}".'
            )

        user_info = {
            'company': account.company,
            'username': account.username,
            'password': decrypt_password(account.encrypted_password),
        }
        await ensure_ziniao_running(
            socket_port=account.socket_port,
            client_path=account.client_path or 'ziniao',
            user_info=user_info,
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
