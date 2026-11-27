from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
from typing import Any

from app.browser.vendors import (
    KIND_ANTIDETECT,
    vendor_spec,
)


@dataclass
class BrowserSessionInfo:
    cdp_port: int | None = None
    pid: int | None = None
    browser: Any = field(default=None, repr=False)  # Playwright Browser object
    ws_endpoint: str | None = None


def is_antidetect(backend: str) -> bool:
    """True for logged-in IP-isolated per-store browsers.

    Reads the vendor registry: a backend is anti-detect when its spec says
    so, so a newly registered vendor is covered without editing this file.
    """
    spec = vendor_spec(backend)
    return spec is not None and spec.kind == KIND_ANTIDETECT


def antidetect_display_name(backend: str) -> str:
    """The product name to show the agent for an anti-detect backend, or a
    neutral fallback so a new backend never leaks the wrong product name."""
    spec = vendor_spec(backend)
    if spec is not None:
        # Bilingual so it reads right in EN prompts and ZH tasks — e.g.
        # 'Ziniao (紫鸟)'. The prompt must name the ACTUAL product so the
        # agent never calls another vendor's store "Ziniao" (observed: it
        # reported one vendor's store failure as another vendor's error).
        return f'{spec.display_name_en} ({spec.display_name_zh})'
    return "the store's anti-detect browser"


class BrowserBackend(ABC):
    @abstractmethod
    async def start(self, browser_config: dict) -> BrowserSessionInfo: ...

    @abstractmethod
    async def stop(self, info: BrowserSessionInfo) -> None: ...


def _is_root() -> bool:
    """True when running as root (Linux only)."""
    return hasattr(os, 'getuid') and os.getuid() == 0
