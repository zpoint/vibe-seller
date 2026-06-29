"""Plugin framework — the inversion-of-control seam (SkyPilot-informed).

Core knows no customer. Instead of importing customer gates, browser
backends, or skills directly, core reads them from an
:class:`ExtensionContext` that plugins populate at startup. A plugin is
any installed package exposing a :class:`Plugin` subclass; it
contributes by implementing :meth:`Plugin.install`, which receives the
context and calls its ``register_*`` methods.

Discovery
---------
Per-customer isolation happens at *pack/install* time: each customer's
deployment installs only that customer's plugin wheels, so other
customers' code is absent at the wheel boundary (cannot leak). On the
box the loader therefore just loads whatever is installed:

  - the OSS **builtin** plugin (:mod:`app.builtin_plugin`) is imported
    directly — always present, no reinstall needed to pick it up — and
    registers every public gate/backend/skill through this same API
    (dogfood; the registry is never empty in a normal install);
  - external plugins are auto-discovered via the
    ``vibe_seller.plugins`` entry-point group their wheels declare.

Registration is **declarative**: ``install`` only records contributions
into the context; nothing touches the live FastAPI app. App-level
effects are applied separately by reading the populated context —
``main._wire_plugins`` mounts plugin routers / frontend-bundle routes at
module load, and the app lifespan starts background services. This split
lets the context load lazily in app-less unit tests (the
``set_task_result`` gate path calls :func:`registered_gates` without a
running server) while the server still wires routes and services once.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
import enum
import importlib.metadata
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# A pretool gate: (command, task_dir, catalog_read) -> deny reason | None.
# Uniform signature so ``first_bash_deny`` can call every guard the same
# way regardless of which fields it actually consumes.
PretoolGate = Callable[[str, object, bool], 'str | None']
# A background service: a long-running coroutine factory. The app
# lifespan wraps the returned coroutine in a task and cancels it on
# shutdown — the shape any service plugin (alerting, monitoring, a
# custom poller, …) plugs into.
ServiceFactory = Callable[[], Awaitable[None]]
# A frontend bundle handler: a no-arg callable returning a FastAPI
# Response with the plugin's compiled JS. Kept loose (``object``) so
# core does not import FastAPI types here.
FrontendHandler = Callable[[], object]

ENTRY_POINT_GROUP = 'vibe_seller.plugins'


class PluginContext(enum.Enum):
    """Process context a plugin is being loaded in.

    Kept for parity with the SkyPilot contract and forward-compat. The
    single-process FastAPI server loads in :attr:`UVICORN`; the registry
    is also built lazily (app-less) for tests, where the distinction is
    irrelevant because registration never touches the app.
    """

    MAIN = 'main'  # early init: config/DB ready, no FastAPI app yet
    UVICORN = 'uvicorn'  # FastAPI app available


ALL_PLUGIN_CONTEXTS = frozenset(PluginContext)


class ExtensionContext:
    """Everything plugins contribute, collected for core to read.

    Plugins call the ``register_*`` methods from :meth:`Plugin.install`.
    Keyed contributions (gates, backends, prompt slots) are
    last-write-wins; ordered ones (pretool gates, skill sources,
    services, routers, frontend bundles) are append-only. Load order is
    deterministic (builtin first, then entry points sorted by name), so
    a later plugin can intentionally override an earlier gate.
    """

    def __init__(
        self,
        context: PluginContext = PluginContext.UVICORN,
        app: object | None = None,
    ) -> None:
        self.context = context
        # The live FastAPI app, when loading in the UVICORN context.
        # Registration must not depend on it (it is None in tests); it
        # is an escape hatch for advanced plugins only.
        self.app = app
        self._gates: dict[str, object] = {}
        self._pretool_gates: list[tuple[str, PretoolGate]] = []
        self._browser_backends: dict[str, type] = {}
        self._skill_sources: list[Path] = []
        self._prompt_fragments: dict[str, list[str]] = {}
        self._services: list[tuple[str, ServiceFactory]] = []
        self._routers: list[tuple[object, str]] = []
        self._frontend_bundles: list[tuple[str, FrontendHandler, bool]] = []
        self._review_markers: list[str] = []

    # ── registration (called from Plugin.install) ───────────────────

    def register_gate(self, name: str, module: object) -> None:
        """Register a ``set_task_result`` gate module under ``name``.

        ``module`` must expose
        ``check(result_text, task_id=None, rules=None) -> GateDeny|None``.
        """
        self._gates[name] = module

    def register_pretool_gate(self, name: str, check: PretoolGate) -> None:
        """Register a PreToolUse Bash guard.

        ``check(command, task_dir, catalog_read)`` returns a deny reason
        or ``None``. Registration order is the deny-check order (first
        deny wins in ``bash_safety.first_bash_deny``).
        """
        self._pretool_gates.append((name, check))

    def register_browser_backend(self, name: str, cls: type) -> None:
        """Register a ``BrowserBackend`` subclass under ``name``."""
        self._browser_backends[name] = cls

    def register_skill_source(self, path) -> None:
        """Register a directory whose subdirs are skills to sync."""
        self._skill_sources.append(Path(path))

    def register_prompt_fragment(self, slot: str, text: str) -> None:
        """Append ``text`` to the named system-prompt ``slot``.

        Recognized slot consumed by the core prompt assembler:
        ``'system_extra'`` — appended to the end of every task's
        assembled system prompt (see ``task_runner.build_system_extra``).
        """
        self._prompt_fragments.setdefault(slot, []).append(text)

    def register_service(self, name: str, start: ServiceFactory) -> None:
        """Register a long-running background service coroutine factory.

        ``start`` is run as a task at app startup and cancelled at
        shutdown. Core ships none; the contract exists so a service
        plugin needs zero core edits.
        """
        self._services.append((name, start))

    def register_router(self, router: object, prefix: str = '') -> None:
        """Register a FastAPI ``APIRouter`` to mount on the app."""
        self._routers.append((router, prefix))

    def register_frontend_bundle(
        self,
        route: str,
        handler: FrontendHandler,
        *,
        early: bool = False,
    ) -> None:
        """Register a compiled JS bundle served at ``route``.

        The OSS dashboard fetches ``GET /api/plugins`` and dynamically
        loads each ``route``. ``early=True`` plugins resolve before the
        app makes API calls. ``handler`` is a no-arg callable returning
        a JS ``Response``.
        """
        self._frontend_bundles.append((route, handler, early))

    def register_review_marker(self, pattern: str) -> None:
        """Declare a regex marking audit deliverables this plugin already
        reviews server-side (at ``set_task_result``).

        Core's legacy REVIEW-file Stop-hook gate
        (``bash_safety.check_review_status``) stands down for any
        ``AD_AUDIT_*`` whose text matches a registered marker — so a
        plugin that ships its own completeness gate doesn't get its
        audits double-gated. Core registers no markers itself; its
        amazon/noon set is matched separately.
        """
        self._review_markers.append(pattern)

    # ── accessors (read by core) ─────────────────────────────────────

    @property
    def gates(self) -> dict[str, object]:
        return dict(self._gates)

    @property
    def pretool_gates(self) -> list[tuple[str, PretoolGate]]:
        return list(self._pretool_gates)

    @property
    def browser_backends(self) -> dict[str, type]:
        return dict(self._browser_backends)

    @property
    def skill_sources(self) -> list[Path]:
        return list(self._skill_sources)

    @property
    def services(self) -> list[tuple[str, ServiceFactory]]:
        return list(self._services)

    @property
    def routers(self) -> list[tuple[object, str]]:
        return list(self._routers)

    @property
    def frontend_bundles(self) -> list[tuple[str, FrontendHandler, bool]]:
        return list(self._frontend_bundles)

    @property
    def review_markers(self) -> list[str]:
        return list(self._review_markers)

    def prompt_fragments(self, slot: str) -> list[str]:
        """All fragments registered for ``slot`` (empty if none)."""
        return list(self._prompt_fragments.get(slot, []))


class Plugin(abc.ABC):
    """Base class every plugin (builtin and external) subclasses."""

    # Contexts this plugin loads in. Defaults to all for parity.
    load_contexts: frozenset[PluginContext] = ALL_PLUGIN_CONTEXTS

    @classmethod
    def should_load(cls, context: PluginContext) -> bool:
        return context in cls.load_contexts

    @property
    def name(self) -> str | None:
        return None

    @property
    def version(self) -> str | None:
        return None

    @abc.abstractmethod
    def install(self, ctx: ExtensionContext) -> None:
        """Record this plugin's contributions into ``ctx``.

        Declarative only — must not touch ``ctx.app`` for anything that
        needs to work in an app-less context (use ``register_router`` /
        ``register_frontend_bundle`` instead).
        """


# The OSS builtin is imported directly (always present, no entry point
# / reinstall needed). Kept as a string to avoid an import cycle at
# module load — resolved inside ``load_plugins``.
_BUILTIN_PLUGIN = 'app.builtin_plugin:BuiltinPlugin'


def _load_builtin(ctx: ExtensionContext) -> str | None:
    module_path, class_name = _BUILTIN_PLUGIN.split(':')
    module = importlib.import_module(module_path)
    plugin_cls = getattr(module, class_name)
    if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, Plugin)):
        raise TypeError(f'{_BUILTIN_PLUGIN} is not a Plugin subclass')
    if not plugin_cls.should_load(ctx.context):
        return None
    plugin = plugin_cls()  # one instance — install() may have side effects
    plugin.install(ctx)
    return plugin.name or class_name


def load_plugins(ctx: ExtensionContext) -> list[str]:
    """Populate ``ctx`` from the builtin + every installed plugin.

    Builtin first (direct import), then ``vibe_seller.plugins`` entry
    points sorted by name (deterministic). Returns the names of plugins
    successfully installed.

    Fail-closed on the builtin: it registers the safety-critical OSS
    gates / Bash guards / browser backends, so if it cannot load the
    registry would be silently empty — that must abort startup, not run
    open. External plugins are the opposite: a misbehaving out-of-tree
    plugin (or corrupt entry-point metadata) is logged and skipped so
    one bad wheel can't take down a customer's whole server.
    """
    loaded: list[str] = []
    try:
        builtin = _load_builtin(ctx)
    except Exception:
        logger.exception(
            'Builtin plugin failed to load — core gates/guards/backends '
            'would be missing; aborting startup (fail-closed)'
        )
        raise
    if builtin:
        loaded.append(builtin)

    try:
        eps = list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # pragma: no cover — corrupt env/metadata
        logger.exception(
            'Failed to enumerate %r entry points; loading builtin only',
            ENTRY_POINT_GROUP,
        )
        eps = []
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            plugin_cls = ep.load()
            if not (
                isinstance(plugin_cls, type) and issubclass(plugin_cls, Plugin)
            ):
                raise TypeError(f'{ep.value} is not a Plugin subclass')
            if not plugin_cls.should_load(ctx.context):
                continue
            plugin_cls().install(ctx)
            loaded.append(ep.name)
        except Exception:  # pragma: no cover — defensive at boot
            logger.exception('Failed to load plugin %r', ep.name)
    logger.info('Loaded plugins: %s', ', '.join(loaded) or '(none)')
    return loaded


_context: ExtensionContext | None = None
_loaded = False


def get_extension_context() -> ExtensionContext:
    """Return the process-wide context, loading plugins on first use.

    Idempotent: :func:`load_plugins` runs exactly once. Lazy so any call
    site touching it before the app lifespan — notably the
    ``set_task_result`` gate path exercised directly in unit tests —
    still sees a fully-populated context.
    """
    global _context, _loaded
    if _context is None:
        _context = ExtensionContext(context=PluginContext.UVICORN)
    if not _loaded:
        _loaded = True
        load_plugins(_context)
    return _context


# ── convenience accessors for core call sites ───────────────────────


def registered_gates() -> dict[str, object]:
    return get_extension_context().gates


def registered_pretool_gates() -> list[tuple[str, PretoolGate]]:
    return get_extension_context().pretool_gates


def registered_browser_backends() -> dict[str, type]:
    return get_extension_context().browser_backends


def registered_skill_sources() -> list[Path]:
    return get_extension_context().skill_sources


def registered_review_markers() -> list[str]:
    return get_extension_context().review_markers


def registered_prompt_fragments(slot: str) -> list[str]:
    return get_extension_context().prompt_fragments(slot)


def reset_for_tests() -> None:
    """Drop the singleton so the next access reloads plugins.

    Test-only seam: a test that installs a fake plugin (or monkeypatches
    entry points) calls this to force a fresh load.
    """
    global _context, _loaded
    _context = None
    _loaded = False
