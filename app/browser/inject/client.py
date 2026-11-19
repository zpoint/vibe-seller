"""Boundary interface the inject engine depends on.

The engine talks only to :class:`InjectClient`. The real implementation
drives the client via a Node inspector + renderer DOM + a Windows
process layer; tests inject a fake. Keeping this a narrow interface is
what lets the whole launcher be covered in CI without the real client.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoreRef:
    """One store/env as seen in the client's list for the current login."""

    name: str
    platform: str = ''
    remote_env: str = ''


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

        Default no-op: a client whose login is managed outside this process
        (e.g. ziniao's per-store credential injection) overrides nothing.
        A client that can drive its own login form (yuehulian team-login)
        overrides this to fill + submit it, raising ``LoginRequired`` when
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

    @abstractmethod
    async def close_store(self, ref: str) -> None:
        """Close a store env (best-effort; never raises for cleanup)."""
