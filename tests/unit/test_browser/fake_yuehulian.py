"""Scenario-driven fake of an inject-driven anti-detect client.

One ``FakeScenario`` spec = one WF case. Lets the whole yuehulian
launcher be covered in CI without the real Windows client. The twist in
each test flips one spec field and asserts the opposite outcome.
"""

from dataclasses import dataclass, field

from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)


@dataclass
class FakeScenario:
    running: bool = True
    can_start: bool = True
    logged_in: bool = True
    stores: list[StoreRef] = field(default_factory=list)
    cdp_host: str = '127.0.0.1'
    cdp_port: int = 0  # set to a real listener (cdp_version_port) for OK path
    open_unreachable: bool = False  # client reports port never came up
    drop_login_after_open: bool = False  # mid-task login loss


class FakeYuehulianClient(InjectClient):
    def __init__(self, scenario: FakeScenario):
        self.s = scenario
        self.started = False
        self.opened: list[str] = []
        self.closed: list[str] = []

    async def ensure_running(self) -> None:
        if not self.s.running:
            if not self.s.can_start:
                raise ClientNotRunning()
            self.s.running = True
            self.started = True

    async def is_logged_in(self) -> bool:
        return self.s.logged_in

    async def list_stores(self) -> list[StoreRef]:
        if not self.s.logged_in:
            raise LoginRequired()
        return list(self.s.stores)

    async def open_store(self, ref: str) -> OpenedStore:
        if not self.s.logged_in:
            raise LoginRequired()
        if ref not in {x.name for x in self.s.stores}:
            raise StoreNotFound(ref)
        if self.s.open_unreachable:
            raise CdpUnreachable(ref)
        self.opened.append(ref)
        if self.s.drop_login_after_open:
            self.s.logged_in = False
        return OpenedStore(self.s.cdp_host, self.s.cdp_port, f'udd/{ref}')

    async def close_store(self, ref: str) -> None:
        self.closed.append(ref)
