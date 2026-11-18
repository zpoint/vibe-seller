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


# Anti-detect backends: a logged-in, IP-isolated per-store browser that
# restricts non-seller URLs (so agents get the dual-browser routing, the
# IP-isolation cookie rule, etc.). ziniao talks to its client over HTTP;
# yuehulian injects CDP into its GUI client — but to the agent/prompt and
# the routing layer they behave the same. Credential/OTP auto-fill is
# ziniao-specific and is gated on the exact backend, not this set.
ANTIDETECT_BACKENDS = frozenset({'ziniao', 'yuehulian'})

# Agent-facing display name per anti-detect backend. The browser-routing
# prompt must name the ACTUAL product so the agent never calls a yuehulian
# store "Ziniao" (observed: it reported a yuehulian store's failure as a
# 紫鸟 error). Keep bilingual so it reads right in EN prompts and ZH tasks.
ANTIDETECT_DISPLAY_NAMES = {
    'ziniao': 'Ziniao (紫鸟)',
    'yuehulian': 'Yuehulian (悦互联)',
}


def is_antidetect(backend: str) -> bool:
    """True for logged-in IP-isolated per-store browsers (ziniao/yuehulian)."""
    return backend in ANTIDETECT_BACKENDS


def antidetect_display_name(backend: str) -> str:
    """The product name to show the agent for an anti-detect backend, or a
    neutral fallback so a new backend never leaks the wrong product name."""
    return ANTIDETECT_DISPLAY_NAMES.get(
        backend, "the store's anti-detect browser"
    )


class BrowserBackend(ABC):
    @abstractmethod
    async def start(self, browser_config: dict) -> BrowserSessionInfo: ...

    @abstractmethod
    async def stop(self, info: BrowserSessionInfo) -> None: ...


def _is_root() -> bool:
    """True when running as root (Linux only)."""
    return hasattr(os, 'getuid') and os.getuid() == 0
