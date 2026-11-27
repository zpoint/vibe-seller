"""Boundary interface the inject engine depends on.

The engine talks only to :class:`InjectClient`. The real implementation
drives the client via a Node inspector + renderer DOM + a Windows
process layer; tests inject a fake. Keeping this a narrow interface is
what lets the whole launcher be covered in CI without the real client.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

# ── Unavailability tokens (a stable, vendor-neutral vocabulary) ──────
#
# ``StoreRef.unavailable_reason`` carries ONE of these tokens — never
# vendor-language prose (free text leaked Chinese into the English UI's
# greyed picker row). The frontend translates the token via i18n
# (``browserVendor.unavailable.<token>``). A vendor whose reason is
# outside the vocabulary uses ``UNAVAILABLE_OTHER`` and puts its own
# words in ``unavailable_detail`` (shown as a fallback / tooltip).
UNAVAILABLE_NO_DEVICE = 'no_device'
UNAVAILABLE_UNSUPPORTED_KERNEL = 'unsupported_kernel'
UNAVAILABLE_EXPIRED = 'expired'
UNAVAILABLE_DISABLED = 'disabled'
UNAVAILABLE_OTHER = 'other'

UNAVAILABLE_TOKENS = frozenset({
    UNAVAILABLE_NO_DEVICE,
    UNAVAILABLE_UNSUPPORTED_KERNEL,
    UNAVAILABLE_EXPIRED,
    UNAVAILABLE_DISABLED,
    UNAVAILABLE_OTHER,
})


@dataclass
class StoreRef:
    """One store/env as seen in the client's list for the current login.

    ``ref`` is the value to bind and reopen by: the stable identifier a
    store is opened with (an env id). Empty means "the name is the ref"
    (clients whose only handle is the store name).
    ``available`` is False when the env exists but cannot be opened
    (e.g. no device bound in the vendor client) so the UI can grey it
    out — with ``unavailable_reason`` (a token from
    ``UNAVAILABLE_TOKENS``, which the frontend translates) shown next
    to it — instead of offering a store every task would fail on.
    ``unavailable_detail`` is optional free text (the vendor's own
    words) for a tooltip or fallback display.
    """

    name: str
    platform: str = ''
    remote_env: str = ''
    ref: str = ''
    available: bool = True
    unavailable_reason: str = ''
    unavailable_detail: str = ''

    @property
    def open_ref(self) -> str:
        """The identifier ``open_store`` expects for this env."""
        return self.ref or self.name


@dataclass
class OpenedStore:
    """CDP endpoint of a freshly opened store env (debug port injected)."""

    cdp_host: str
    cdp_port: int
    user_data_dir: str = ''


class InjectClient(ABC):
    """A GUI anti-detect client driven by debug-port injection."""

    @abstractmethod
    async def ensure_running(self) -> None:
        """Start or attach the client; raise ``ClientNotRunning`` if not."""

    @abstractmethod
    async def is_logged_in(self) -> bool:
        """True when a user identity is logged into the GUI."""

    async def ensure_logged_in(self) -> None:
        """Auto-login if the client is logged out and credentials are stored.

        Default no-op: a client whose login is managed outside this
        process (e.g. per-store credential injection) overrides nothing.
        A client that can drive its own login form overrides this to fill
        + submit it, raising ``LoginRequired`` when
        it has no credentials or the login does not take. The engine calls
        it after ``ensure_running`` and before the ``is_logged_in`` gate.
        """
        return None

    @abstractmethod
    async def list_stores(self) -> list[StoreRef]:
        """Stores visible to the logged-in identity.

        Raises ``LoginRequired`` when not logged in (never an empty list
        standing in for "logged out").
        """

    @abstractmethod
    async def open_store(self, ref: str) -> OpenedStore:
        """Open a store env with a CDP port injected; return its endpoint.

        Raises ``LoginRequired`` / ``StoreNotFound`` / ``CdpUnreachable``.
        """

    async def after_open(self, opened: OpenedStore) -> None:
        """Drive the freshly opened env past its entry step.

        Default no-op: most clients land the env directly on the store.
        A client whose env opens on an intermediate store-info page
        (one with an "open store" button) overrides this to click
        through to the real store page, so the agent never has
        to discover that step. The engine calls it once CDP is reachable
        and BEFORE the CDPMuxProxy fronts the env (the proxy's startup
        cleanup reaps orphan web tabs, so the page this produces must
        exist first — with ``keep_last_page`` it is kept).
        """
        return None

    @abstractmethod
    async def close_store(self, ref: str) -> None:
        """Close a store env (best-effort; never raises for cleanup)."""
