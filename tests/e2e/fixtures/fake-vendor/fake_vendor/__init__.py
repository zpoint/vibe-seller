"""A test-only fake browser vendor, installed into the e2e venv.

Registered through the REAL plugin path (a ``vibe_seller.plugins``
entry point — D-S1: no env loading seam), but inert everywhere it
was not asked for: ``should_load`` is gated on ``VIBE_FAKE_VENDOR_HOME``
being set, so a server started without it (every dev server, every
other CI server) never even loads this module's spec. The env var
only parametrizes the fixture's canary paths, like any test config.

What it gives the guard replay (tests/e2e/test_guard_replay.py):

- a vendor spec whose ``private_paths`` point at the test's canary
  dir (a planted "log" with a fake API key, a profile-shaped
  ``cache/profile-1/Default`` tree with a fake ``Login Data``), and
  whose ``local_ports`` name the fake local-API server the test runs;
- a backend class that is never actually started (the replay never
  opens a browser).

The canary values are obviously fake; nothing here comes from any
real account.
"""

import os

from app.browser.vendors import BrowserVendorSpec
from app.plugins import Plugin

# The canary key planted in the fake vendor "log". Deliberately not a
# real key shape for any vendor (the tripwire matches PATHS only
# anyway — this is what a leak would look like in output).
CANARY_KEY = 'FAKE-VENDOR-CANARY-KEY-0001'


class FakeVendorPlugin(Plugin):
    """Registers the fake vendor when the test env asks for it."""

    @classmethod
    def should_load(cls, context) -> bool:
        return bool(os.environ.get('VIBE_FAKE_VENDOR_HOME'))

    @property
    def name(self) -> str:
        return 'fake_vendor'

    def install(self, ctx) -> None:
        home = os.environ.get('VIBE_FAKE_VENDOR_HOME', '')
        port = int(os.environ.get('VIBE_FAKE_VENDOR_PORT', '0') or 0)
        ctx.register_browser_backend(
            'fakevendor',
            _NeverStartedBackend,
            BrowserVendorSpec(
                key='fakevendor',
                display_name_en='FakeVendor',
                display_name_zh='假浏览器',
                private_paths=(
                    # the canary dir, by its distinctive top name and
                    # as the exact path the test planted
                    'FakeVendorData',
                    home.replace('\\', '/').rstrip('/'),
                ),
                local_ports=(port,) if port else (),
            ),
        )


class _NeverStartedBackend:
    """The replay never opens a browser; the backend exists only so
    registration has a class to name."""
