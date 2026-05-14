from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
from typing import Any


@dataclass
class BrowserSessionInfo:
    cdp_port: int | None = None
    pid: int | None = None
    browser: Any = field(default=None, repr=False)  # Playwright Browser object
    ws_endpoint: str | None = None


class BrowserBackend(ABC):
    @abstractmethod
    async def start(self, browser_config: dict) -> BrowserSessionInfo: ...

    @abstractmethod
    async def stop(self, info: BrowserSessionInfo) -> None: ...


def _is_root() -> bool:
    """True when running as root (Linux only)."""
    return hasattr(os, 'getuid') and os.getuid() == 0
