"""Yuehulian (悦互联) anti-detect browser backend — Windows only.

Yuehulian ships no local automation API (unlike ziniao), so we drive its
Electron client through a Node inspector + renderer DOM and inject a CDP
debug port into the launched kernel. From the CDP endpoint onward it is
identical to the ziniao / chrome backends (CDPMuxProxy → browser-use).

The engine (:class:`InjectCdpBackend`) holds the generic flow; this file
is the thin adapter. The real client lives in ``yuehulian_client`` and is
swapped for a fake in tests via the ``client_factory`` seam.
"""

from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.inject.client import InjectClient
from app.browser.inject.engine import ClientFactory, InjectCdpBackend


def make_client(browser_config: dict) -> InjectClient:
    """Build the real Windows-driven client (patched to a fake in tests)."""
    return RealYuehulianClient(browser_config)


class YuehulianBackend(InjectCdpBackend):
    """Route-B backend for the 悦互联 e-commerce browser (Windows)."""

    def __init__(self, client_factory: ClientFactory | None = None):
        # BrowserManager constructs backends with no args (the real
        # path). Tests pass a factory that returns a FakeYuehulianClient.
        super().__init__(client_factory or (lambda cfg: make_client(cfg)))
