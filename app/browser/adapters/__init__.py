"""Concrete inject-driven browser adapters (one per client).

Each adapter is a thin :class:`InjectCdpBackend` subclass plus a client
descriptor (install location, process names, DOM selectors, login
signal). ``yuehulian`` is the first; more anti-detect clients can be
added here without touching the shared engine.
"""
