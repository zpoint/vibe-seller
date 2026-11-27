"""Concrete inject-driven browser adapters (one per client).

Each adapter is a thin :class:`InjectCdpBackend` subclass plus a client
descriptor (install location, process names, DOM selectors, login
signal). Each one is declared in its own module with a vendor spec
(see app/browser/vendors.py) — adding a client never touches the shared
engine.
"""
